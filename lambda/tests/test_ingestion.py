"""Tests for the ingestion Lambda."""

from __future__ import annotations

import json

import pytest
from botocore.stub import ANY, Stubber


@pytest.fixture
def handler(fresh_module):
    return fresh_module("ingestion.handler")


def _sqs_record(eventbridge_detail: dict, message_id: str = "msg-1") -> dict:
    return {
        "messageId": message_id,
        "body": json.dumps({"detail": eventbridge_detail}),
    }


def test_single_record_starts_one_execution(handler):
    stubber = Stubber(handler.sfn)
    stubber.add_response(
        "start_execution",
        {
            "executionArn": "arn:aws:states:us-east-1:111111111111:execution:test-quarantine:e1",
            "startDate": __import__("datetime").datetime(2026, 1, 1),
        },
        expected_params={
            "stateMachineArn": handler.config.state_machine_arn,
            "name": ANY,
            "input": ANY,
        },
    )

    event = {
        "Records": [
            _sqs_record(
                {
                    "packageName": "lodash",
                    "packageVersion": "4.17.21",
                    "repositoryName": "test-staging-npm",
                }
            )
        ]
    }

    with stubber:
        result = handler.lambda_handler(event, context=None)

    assert result == {"batchItemFailures": []}
    stubber.assert_no_pending_responses()


def test_multiple_records_start_multiple_executions(handler):
    stubber = Stubber(handler.sfn)
    for _ in range(3):
        stubber.add_response(
            "start_execution",
            {"executionArn": "arn:...", "startDate": __import__("datetime").datetime(2026, 1, 1)},
            expected_params={"stateMachineArn": ANY, "name": ANY, "input": ANY},
        )

    event = {
        "Records": [
            _sqs_record(
                {
                    "packageName": f"pkg-{i}",
                    "packageVersion": "1.0.0",
                    "repositoryName": "test-staging-npm",
                },
                f"m-{i}",
            )
            for i in range(3)
        ]
    }

    with stubber:
        result = handler.lambda_handler(event, context=None)

    assert result == {"batchItemFailures": []}
    stubber.assert_no_pending_responses()


def test_bad_json_returns_batch_item_failure(handler):
    event = {"Records": [{"messageId": "bad", "body": "{not-json"}]}
    result = handler.lambda_handler(event, context=None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "bad"}]}


def test_one_bad_one_good_partial_failure(handler):
    stubber = Stubber(handler.sfn)
    stubber.add_response(
        "start_execution",
        {"executionArn": "arn:...", "startDate": __import__("datetime").datetime(2026, 1, 1)},
        expected_params={"stateMachineArn": ANY, "name": ANY, "input": ANY},
    )

    event = {
        "Records": [
            {"messageId": "bad", "body": "{not-json"},
            _sqs_record(
                {
                    "packageName": "lodash",
                    "packageVersion": "1.0.0",
                    "repositoryName": "test-staging-npm",
                },
                "good",
            ),
        ]
    }

    with stubber:
        result = handler.lambda_handler(event, context=None)

    # Only the bad record stays on the queue.
    assert result == {"batchItemFailures": [{"itemIdentifier": "bad"}]}
    stubber.assert_no_pending_responses()


def test_execution_input_includes_approval_mode(handler, mocker):
    captured: dict = {}

    def fake_start(**kwargs):
        captured.update(kwargs)
        return {"executionArn": "arn:...", "startDate": __import__("datetime").datetime(2026, 1, 1)}

    mocker.patch.object(handler.sfn, "start_execution", side_effect=fake_start)

    event = {
        "Records": [
            _sqs_record(
                {
                    "packageName": "lodash",
                    "packageVersion": "1.0.0",
                    "repositoryName": "test-staging-npm",
                }
            )
        ]
    }
    handler.lambda_handler(event, context=None)

    payload = json.loads(captured["input"])
    assert payload["approvalMode"] == "findings"  # env default
    assert payload["skipCooldown"] is False
    assert payload["approvalTimeoutSeconds"] == 86400 * 14  # P14D default


@pytest.mark.parametrize(
    "duration,expected_seconds",
    [
        ("PT24H", 86400),
        ("PT1H", 3600),
        ("PT30M", 30 * 60),
        ("P7D", 7 * 86400),
        ("P1DT12H", 86400 + 12 * 3600),
        ("PT45S", 45),
    ],
)
def test_iso8601_to_seconds_supported_forms(handler, duration, expected_seconds):
    assert handler._iso8601_to_seconds(duration) == expected_seconds


def test_iso8601_to_seconds_rejects_invalid(handler):
    with pytest.raises(ValueError, match="invalid ISO-8601"):
        handler._iso8601_to_seconds("24h")  # no P prefix
