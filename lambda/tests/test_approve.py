"""Tests for the approve Lambda."""

from __future__ import annotations

import json

import pytest
from botocore.stub import ANY, Stubber


@pytest.fixture
def handler(fresh_module):
    return fresh_module("approve.handler")


def test_approve_decision_calls_send_task_success(handler):
    stubber = Stubber(handler.sfn)
    stubber.add_response(
        "send_task_success",
        {},
        expected_params={
            "taskToken": "tok-1",
            "output": ANY,
        },
    )

    with stubber:
        result = handler.lambda_handler(
            {
                "taskToken": "tok-1",
                "decision": "approve",
                "approver": "alice@example.com",
                "reason": "verified",
            },
            None,
        )

    assert result == {"acknowledged": True}
    stubber.assert_no_pending_responses()


def test_reject_decision_calls_send_task_failure(handler):
    stubber = Stubber(handler.sfn)
    stubber.add_response(
        "send_task_failure",
        {},
        expected_params={
            "taskToken": "tok-1",
            "error": "ApprovalRejected",
            "cause": ANY,
        },
    )

    with stubber:
        result = handler.lambda_handler(
            {
                "taskToken": "tok-1",
                "decision": "reject",
                "approver": "bob@example.com",
                "reason": "looks suspicious",
            },
            None,
        )

    assert result == {"acknowledged": True}
    stubber.assert_no_pending_responses()


def test_invalid_decision_raises(handler):
    with pytest.raises(ValueError, match="must be 'approve' or 'reject'"):
        handler.lambda_handler(
            {"taskToken": "tok", "decision": "maybe", "approver": "x"},
            None,
        )


def test_approval_output_contains_approver_and_reason(handler, mocker):
    captured: dict = {}

    def fake_success(**kwargs):
        captured.update(kwargs)

    mocker.patch.object(handler.sfn, "send_task_success", side_effect=fake_success)

    handler.lambda_handler(
        {
            "taskToken": "tok",
            "decision": "approve",
            "approver": "alice@example.com",
            "reason": "vetted",
        },
        None,
    )

    output = json.loads(captured["output"])
    assert output == {"approver": "alice@example.com", "reason": "vetted"}
