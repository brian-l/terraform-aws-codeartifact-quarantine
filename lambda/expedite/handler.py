"""Expedite Lambda.

Manual override entry point. Starts the same Step Functions state machine with
skipCooldown=true, bypassing the Wait state. The scan still runs — only the
cooldown is skipped.

Invoke (CLI):
    aws lambda invoke --function-name <expedite-function-name> \
        --payload '{"format":"npm","name":"@scope/pkg","version":"1.2.3","reason":"CVE-..."}' \
        /dev/stdout

Input:
    {
      "format":  "npm" | "pypi" | "maven" | "nuget" | "generic",
      "namespace": "<scope>",       # optional
      "name":    "<package name>",
      "version": "<version>",
      "source_repository": "<staging repo name>",   # which source repo to promote from
      "reason":  "<free-form reason / ticket link>",
      "requester": "<email or identifier>"   # optional, defaults to caller identity
    }

Output:
    {"executionArn": "...", "expedited": true}
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

import boto3

from common import codeartifact as ca
from common.config import from_env
from common.logging import get_logger

if TYPE_CHECKING:
    from mypy_boto3_stepfunctions import SFNClient

log = get_logger(__name__)
config = from_env()
sfn: SFNClient = boto3.client("stepfunctions")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    fmt = event["format"]
    name = event["name"]
    version = event["version"]
    namespace = event.get("namespace")
    reason = event.get("reason", "no reason provided")
    requester = event.get("requester") or _caller(context)

    # When multiple source repos exist, the caller picks which one. When only
    # one is configured, default to it.
    source_repo: str = event.get("source_repository") or _single_source_or_die()

    if source_repo not in config.source_repo_names:
        raise ValueError(
            f"source_repository {source_repo!r} is not configured; "
            f"valid values: {list(config.source_repo_names)}"
        )

    # Validate the version exists in staging — prevents typo'd manual publish.
    ca.describe_version(
        domain=config.domain_name,
        owner=config.domain_owner,
        repo=source_repo,
        fmt=fmt,
        namespace=namespace,
        name=name,
        version=version,
    )

    detail = {
        "packageFormat": fmt,
        "packageName": name,
        "packageNamespace": namespace,
        "packageVersion": version,
        "domainName": config.domain_name,
        "domainOwner": config.domain_owner,
        "repositoryName": source_repo,
    }
    execution_input = {
        "detail": detail,
        "skipCooldown": True,
        "approvalMode": "always",  # expedites always get human review
        "approvalTimeoutSeconds": 86400 * 14,
        "expedite": {"reason": reason, "requester": requester},
    }

    exec_name = f"expedite-{name}-{version}-{uuid.uuid4().hex[:8]}".replace("@", "_").replace(
        "/", "_"
    )[:80]
    response = sfn.start_execution(
        stateMachineArn=config.state_machine_arn,
        name=exec_name,
        input=json.dumps(execution_input),
    )
    log.info(
        "expedite started",
        extra={
            "package": name,
            "version": version,
            "requester": requester,
            "reason": reason,
        },
    )
    return {"executionArn": response["executionArn"], "expedited": True}


def _single_source_or_die() -> str:
    if len(config.source_repo_names) == 1:
        return config.source_repo_names[0]
    raise ValueError(
        f"multiple source repositories are configured ({list(config.source_repo_names)}); "
        f"expedite event must include 'source_repository' to disambiguate"
    )


def _caller(context: Any) -> str:
    # Lambda doesn't surface invoker IAM principal in context directly. The
    # invoking identity is in the CloudTrail event. As a best-effort, return
    # the invoked function ARN's account; consumers should pass `requester`
    # explicitly via a CLI wrapper that captures `aws sts get-caller-identity`.
    return "unknown"
