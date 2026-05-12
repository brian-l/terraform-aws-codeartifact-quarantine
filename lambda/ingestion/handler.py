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


def _iso8601_to_seconds(duration: str) -> int:
    """Minimal ISO-8601 duration parser. Handles PT<H>H, PT<M>M, P<D>D — enough for our config."""
    if not duration.startswith("P"):
        raise ValueError(f"invalid ISO-8601 duration: {duration}")
    s = duration[1:]
    days = hours = minutes = 0
    if "T" in s:
        date_part, time_part = s.split("T", 1)
    else:
        date_part, time_part = s, ""
    if date_part.endswith("D"):
        days = int(date_part[:-1])
    if time_part.endswith("H"):
        hours = int(time_part[:-1])
    elif time_part.endswith("M"):
        minutes = int(time_part[:-1])
    return days * 86400 + hours * 3600 + minutes * 60
