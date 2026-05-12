"""Ingestion Lambda.

Consumes EventBridge -> SQS messages for new CodeArtifact package versions
and starts a Step Functions execution for each.

Event shape (one SQS record wraps one EventBridge event):
    {
      "Records": [
        {
          "body": "<json string of the EventBridge event>",
          ...
        }
      ]
    }

The EventBridge event has the shape documented at:
    https://docs.aws.amazon.com/codeartifact/latest/ug/monitoring-events.html

We pass the full `detail` object to Step Functions as the execution input,
along with `skipCooldown=false` and the approval mode.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import TYPE_CHECKING, Any

# common/ is bundled into this Lambda's zip by archive_file (see
# modules/pipeline/lambdas.tf). Lambda's /var/task is on sys.path at runtime,
# so `from common.config import ...` works without any path manipulation.
import boto3

from common.config import from_env
from common.logging import get_logger

if TYPE_CHECKING:
    from mypy_boto3_stepfunctions import SFNClient

log = get_logger(__name__)
config = from_env()
sfn: SFNClient = boto3.client("stepfunctions")


APPROVAL_MODE = os.environ.get("APPROVAL_REQUIRED_WHEN", "findings")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        try:
            _process(record)
        except Exception:
            log.exception("failed to process record", extra={"messageId": record.get("messageId")})
            failures.append({"itemIdentifier": record["messageId"]})

    # Partial-batch response: failed messages stay on the queue, the rest are deleted.
    return {"batchItemFailures": failures}


def _process(record: dict[str, Any]) -> None:
    event = json.loads(record["body"])
    detail = event.get("detail", {})

    pkg = detail.get("packageName", "?")
    ver = detail.get("packageVersion", "?")
    log.info("starting quarantine execution", extra={"package": pkg, "version": ver})

    execution_input = {
        "detail": detail,
        "skipCooldown": False,
        "approvalMode": APPROVAL_MODE,
        # Map ISO-8601 duration -> seconds for the SFN TimeoutSecondsPath. The actual
        # approval timeout duration lives on the SFN state, but RequestApproval needs
        # a numeric timeout when using waitForTaskToken.
        "approvalTimeoutSeconds": _iso8601_to_seconds(os.environ.get("APPROVAL_TIMEOUT", "P14D")),
    }

    sfn.start_execution(
        stateMachineArn=config.state_machine_arn,
        name=f"{pkg}-{ver}-{uuid.uuid4().hex[:8]}".replace("@", "_").replace("/", "_")[:80],
        input=json.dumps(execution_input),
    )


_ISO8601_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?" r"(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)


def _iso8601_to_seconds(duration: str) -> int:
    """Minimal ISO-8601 duration parser supporting the subset our config uses.

    Recognises P<D>D, PT<H>H, PT<M>M, PT<S>S and any combination thereof, e.g.
    P1DT12H, PT1H30M, PT45S. Fractional seconds and the year/month components
    are intentionally not supported — surface that as an error rather than
    silently returning zero.
    """
    match = _ISO8601_RE.match(duration)
    if not match or duration in {"P", "PT"}:
        raise ValueError(f"invalid ISO-8601 duration: {duration!r}")
    days = int(match.group("d") or 0)
    hours = int(match.group("h") or 0)
    minutes = int(match.group("m") or 0)
    seconds = int(match.group("s") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds
