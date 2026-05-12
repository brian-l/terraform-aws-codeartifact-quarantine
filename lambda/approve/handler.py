"""Approve Lambda.

Callback target for the human-approval gate. Invoked by an approval system
(Slack bot, web form, etc.) with the task token captured from the SNS message
emitted by the SFN RequestApproval state.

Input:
    {
      "taskToken": "<token from SNS message>",
      "decision":  "approve" | "reject",
      "approver":  "<email or identifier>",
      "reason":    "<optional free-form reason>"
    }

Output:
    {"acknowledged": true}
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import boto3

from common.logging import get_logger

if TYPE_CHECKING:
    from mypy_boto3_stepfunctions import SFNClient

log = get_logger(__name__)
sfn: SFNClient = boto3.client("stepfunctions")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    token = event["taskToken"]
    decision = event["decision"]
    approver = event.get("approver", "unknown")
    reason = event.get("reason", "")

    if decision == "approve":
        sfn.send_task_success(
            taskToken=token,
            output=json.dumps({"approver": approver, "reason": reason}),
        )
        log.info("approval recorded", extra={"approver": approver, "decision": "approve"})
    elif decision == "reject":
        sfn.send_task_failure(
            taskToken=token,
            error="ApprovalRejected",
            cause=json.dumps({"approver": approver, "reason": reason}),
        )
        log.info("rejection recorded", extra={"approver": approver, "decision": "reject"})
    else:
        raise ValueError(f"decision must be 'approve' or 'reject', got: {decision!r}")

    return {"acknowledged": True}
