"""Scan Lambda.

Evaluates a package version against the configured scanner and returns a
decision used by the Step Functions Choice state.

Input (from SFN):
    {
      "detail": { packageFormat, packageName, packageVersion, ... },
      ...
    }

Output:
    {
      "decision": "clean" | "findings" | "blocked",
      "findings": [...]
    }

Scanner types:
    "inspector" -- query Inspector v2 ListFindings filtered by the package ARN.
                   Decision is "blocked" if any finding's severity is in
                   BLOCK_ON_SEVERITY, "findings" if any finding exists below
                   that threshold, else "clean".
    "lambda"    -- invoke a consumer-provided Lambda with the same input.
                   Expect {"decision": ..., "findings": [...]} back.
    "none"      -- always return "clean".
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import boto3

from common.config import from_env
from common.logging import get_logger

if TYPE_CHECKING:
    from mypy_boto3_inspector2 import Inspector2Client
    from mypy_boto3_lambda import LambdaClient

log = get_logger(__name__)
config = from_env()

inspector: Inspector2Client = boto3.client("inspector2")
lambda_client: LambdaClient = boto3.client("lambda")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    detail = event.get("detail", {})
    pkg = detail.get("packageName")
    ver = detail.get("packageVersion")
    log.info("scanning", extra={"package": pkg, "version": ver})

    if config.scanner_type == "none":
        return {"decision": "clean", "findings": []}

    if config.scanner_type == "lambda":
        return _invoke_custom_scanner(event)

    return _scan_with_inspector(detail)


def _scan_with_inspector(detail: dict[str, Any]) -> dict[str, Any]:
    pkg = detail.get("packageName", "")
    ver = detail.get("packageVersion", "")

    findings: list[dict[str, Any]] = []
    paginator = inspector.get_paginator("list_findings")
    # Cast: boto3-stubs types filter_criteria as the deeply-nested FilterCriteriaTypeDef
    # union; constructing it as a literal triggers structural mismatches at strict mypy.
    # Runtime payload is exactly what Inspector accepts.
    filter_criteria: Any = {
        "vulnerablePackages": [
            {"name": {"comparison": "EQUALS", "value": pkg}},
            {"version": {"comparison": "EQUALS", "value": ver}},
        ],
    }

    for page in paginator.paginate(filterCriteria=filter_criteria):
        for f in page.get("findings", []):
            findings.append(
                {
                    "id": f.get("findingArn"),
                    "severity": f.get("severity"),
                    "title": f.get("title"),
                }
            )

    if not findings:
        return {"decision": "clean", "findings": []}

    blocking = [
        f for f in findings if (f.get("severity") or "").upper() in config.block_on_severity
    ]
    decision = "blocked" if blocking else "findings"
    log.info(
        "inspector scan complete",
        extra={
            "package": pkg,
            "version": ver,
            "decision": decision,
            "finding_count": len(findings),
            "blocking_count": len(blocking),
        },
    )
    return {"decision": decision, "findings": findings}


def _invoke_custom_scanner(event: dict[str, Any]) -> dict[str, Any]:
    if not config.scanner_lambda_arn:
        raise RuntimeError("scanner_type=lambda but SCANNER_LAMBDA_ARN is empty")

    response = lambda_client.invoke(
        FunctionName=config.scanner_lambda_arn,
        InvocationType="RequestResponse",
        Payload=json.dumps(event).encode(),
    )
    payload = json.loads(response["Payload"].read())

    decision = payload.get("decision", "clean")
    if decision not in {"clean", "findings", "blocked"}:
        raise RuntimeError(f"custom scanner returned invalid decision: {decision!r}")
    return {"decision": decision, "findings": payload.get("findings", [])}
