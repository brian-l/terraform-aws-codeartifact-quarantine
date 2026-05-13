"""Audit Lambda.

Records the outcome of a quarantine execution in DynamoDB. Called as the final
terminal step of each Step Functions branch (promoted, blocked, rejected, timeout,
scan-error, promote-error).

Input (from SFN, varies by branch):
    {
      "decision": "promoted" | "blocked" | "rejected" | "approval-timeout" | "scan-error" | "promote-error",
      "execution": "<sfn execution name>",
      "input": { ...original SFN execution input... }
    }
"""

from __future__ import annotations

import os
from typing import Any

from common import audit
from common import codeartifact as ca
from common.config import from_env
from common.logging import get_logger

log = get_logger(__name__)
config = from_env()
region = os.environ["AWS_REGION"]


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    decision = event["decision"]
    execution = event["execution"]
    inp = event.get("input", {})
    detail = inp.get("detail", {})

    fmt = detail.get("packageFormat", "")
    namespace = detail.get("packageNamespace")
    name = detail.get("packageName", "")
    version = detail.get("packageVersion", "")
    # Source repo from the original event, not config — multi-source pipeline
    # may audit promotions from any of several staging repos.
    source_repo = detail.get("repositoryName", "")

    arn = ca.package_arn(
        config.domain_name,
        config.domain_owner,
        region,
        source_repo,
        fmt,
        namespace,
        name,
    )

    extra: dict[str, Any] = {
        "format": fmt,
        "namespace": namespace or "",
        "name": name,
    }
    if "scanResult" in inp:
        extra["findings"] = inp["scanResult"].get("findings", [])
    if "error" in inp:
        extra["error"] = inp["error"]

    audit.record(
        config.audit_table_name,
        package_arn=arn,
        version=version,
        decision=decision,
        execution=execution,
        extra=extra,
    )
    log.info("audited", extra={"package": name, "version": version, "decision": decision})
    return {"audited": True}
