"""Tests for the scan Lambda."""

from __future__ import annotations

import dataclasses
import json

import pytest
from botocore.stub import ANY, Stubber


@pytest.fixture
def handler(fresh_module):
    return fresh_module("scan.handler")


def _override_config(handler, **overrides):
    """Config is a frozen dataclass; build a new instance and rebind."""
    handler.config = dataclasses.replace(handler.config, **overrides)


def _finding(severity: str, *, arn: str = "arn:finding:1", title: str = "Sample CVE") -> dict:
    """Build a minimally-complete Inspector finding for response validation.

    botocore's response shape validator requires the full set of required
    fields even though our handler only reads severity/title/findingArn.
    """
    return {
        "findingArn": arn,
        "severity": severity,
        "title": title,
        "type": "PACKAGE_VULNERABILITY",
        "description": "test",
        "remediation": {"recommendation": {"text": "upgrade"}},
        "firstObservedAt": __import__("datetime").datetime(2026, 1, 1),
        "lastObservedAt": __import__("datetime").datetime(2026, 1, 2),
        "status": "ACTIVE",
        "awsAccountId": "111111111111",
        "resources": [{"id": "pkg", "type": "AWS_LAMBDA_FUNCTION"}],
    }


def test_scanner_type_none_returns_clean(handler):
    _override_config(handler, scanner_type="none")
    result = handler.lambda_handler({"detail": {"packageName": "x", "packageVersion": "1"}}, None)
    assert result == {"decision": "clean", "findings": []}


def test_inspector_no_findings_returns_clean(handler):
    stubber = Stubber(handler.inspector)
    stubber.add_response(
        "list_findings",
        {"findings": []},
        expected_params={"filterCriteria": ANY},
    )

    with stubber:
        result = handler.lambda_handler(
            {"detail": {"packageName": "lodash", "packageVersion": "1.0.0"}},
            None,
        )

    assert result == {"decision": "clean", "findings": []}
    stubber.assert_no_pending_responses()


def test_inspector_low_severity_returns_findings(handler):
    stubber = Stubber(handler.inspector)
    stubber.add_response(
        "list_findings",
        {"findings": [_finding("MEDIUM")]},
        expected_params={"filterCriteria": ANY},
    )

    with stubber:
        result = handler.lambda_handler(
            {"detail": {"packageName": "lodash", "packageVersion": "1.0.0"}},
            None,
        )

    assert result["decision"] == "findings"
    assert len(result["findings"]) == 1
    stubber.assert_no_pending_responses()


def test_inspector_high_severity_returns_blocked(handler):
    stubber = Stubber(handler.inspector)
    stubber.add_response(
        "list_findings",
        {"findings": [_finding("MEDIUM", arn="arn:1"), _finding("HIGH", arn="arn:2")]},
        expected_params={"filterCriteria": ANY},
    )

    with stubber:
        result = handler.lambda_handler(
            {"detail": {"packageName": "lodash", "packageVersion": "1.0.0"}},
            None,
        )

    assert result["decision"] == "blocked"
    assert len(result["findings"]) == 2
    stubber.assert_no_pending_responses()


def test_inspector_critical_blocks(handler):
    stubber = Stubber(handler.inspector)
    stubber.add_response(
        "list_findings",
        {"findings": [_finding("CRITICAL")]},
        expected_params={"filterCriteria": ANY},
    )

    with stubber:
        result = handler.lambda_handler(
            {"detail": {"packageName": "lodash", "packageVersion": "1.0.0"}},
            None,
        )

    assert result["decision"] == "blocked"


def test_inspector_paginates(handler):
    stubber = Stubber(handler.inspector)
    stubber.add_response(
        "list_findings",
        {"findings": [_finding("LOW", arn="arn:1")], "nextToken": "tok"},
        expected_params={"filterCriteria": ANY},
    )
    stubber.add_response(
        "list_findings",
        {"findings": [_finding("LOW", arn="arn:2")]},
        expected_params={"filterCriteria": ANY, "nextToken": "tok"},
    )

    with stubber:
        result = handler.lambda_handler(
            {"detail": {"packageName": "lodash", "packageVersion": "1.0.0"}},
            None,
        )

    assert len(result["findings"]) == 2
    assert result["decision"] == "findings"
    stubber.assert_no_pending_responses()


def test_custom_scanner_lambda_returns_decision(handler):
    _override_config(handler, scanner_type="lambda", scanner_lambda_arn="arn:lambda:custom")

    stubber = Stubber(handler.lambda_client)
    stubber.add_response(
        "invoke",
        {
            "StatusCode": 200,
            "Payload": __import__("io").BytesIO(
                json.dumps({"decision": "blocked", "findings": [{"id": "x"}]}).encode()
            ),
        },
        expected_params={
            "FunctionName": "arn:lambda:custom",
            "InvocationType": "RequestResponse",
            "Payload": ANY,
        },
    )

    with stubber:
        result = handler.lambda_handler(
            {"detail": {"packageName": "lodash", "packageVersion": "1.0.0"}},
            None,
        )

    assert result == {"decision": "blocked", "findings": [{"id": "x"}]}
    stubber.assert_no_pending_responses()


def test_custom_scanner_rejects_unknown_decision(handler):
    _override_config(handler, scanner_type="lambda", scanner_lambda_arn="arn:lambda:custom")

    stubber = Stubber(handler.lambda_client)
    stubber.add_response(
        "invoke",
        {
            "StatusCode": 200,
            "Payload": __import__("io").BytesIO(
                json.dumps({"decision": "approve-all", "findings": []}).encode()
            ),
        },
        expected_params={"FunctionName": ANY, "InvocationType": ANY, "Payload": ANY},
    )

    with stubber, pytest.raises(RuntimeError, match="invalid decision"):
        handler.lambda_handler(
            {"detail": {"packageName": "x", "packageVersion": "1"}},
            None,
        )


def test_lambda_type_without_arn_raises(handler):
    _override_config(handler, scanner_type="lambda", scanner_lambda_arn="")

    with pytest.raises(RuntimeError, match="SCANNER_LAMBDA_ARN is empty"):
        handler.lambda_handler(
            {"detail": {"packageName": "x", "packageVersion": "1"}},
            None,
        )
