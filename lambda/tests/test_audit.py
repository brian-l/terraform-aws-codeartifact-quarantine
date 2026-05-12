"""Tests for the audit Lambda."""

from __future__ import annotations

import pytest


@pytest.fixture
def handler(fresh_module):
    return fresh_module("audit.handler")


def _capture_put(mocker, audit_module) -> dict:
    """Replace put_item with a capturing fake. Returns the dict that will hold the call kwargs."""
    captured: dict = {}

    def fake_put(**kwargs):
        captured.update(kwargs)
        return {}

    mocker.patch.object(
        audit_module._dynamodb,
        "Table",
        side_effect=lambda _name: type("FakeTable", (), {"put_item": staticmethod(fake_put)})(),
    )
    return captured


def test_promoted_decision_writes_row(handler, mocker):
    from common import audit as audit_module

    captured = _capture_put(mocker, audit_module)

    handler.lambda_handler(
        {
            "decision": "promoted",
            "execution": "exec-1",
            "input": {
                "detail": {
                    "packageFormat": "npm",
                    "packageName": "lodash",
                    "packageVersion": "1.2.3",
                    "repositoryName": "test-staging-npm",
                }
            },
        },
        None,
    )

    item = captured["Item"]
    assert item["decision"] == "promoted"
    assert item["version"] == "1.2.3"
    assert item["execution"] == "exec-1"
    assert "1.2.3#" in item["version_ts"]
    # Package ARN built from source repo in the event detail.
    assert item["package_arn"].startswith(
        "arn:aws:codeartifact:us-east-1:111111111111:package/test-domain/test-staging-npm/npm"
    )


def test_findings_included_when_present(handler, mocker):
    from common import audit as audit_module

    captured = _capture_put(mocker, audit_module)

    handler.lambda_handler(
        {
            "decision": "blocked",
            "execution": "exec-2",
            "input": {
                "detail": {
                    "packageFormat": "npm",
                    "packageName": "lodash",
                    "packageVersion": "1.0.0",
                    "repositoryName": "test-staging-npm",
                },
                "scanResult": {
                    "decision": "blocked",
                    "findings": [{"id": "f1", "severity": "HIGH"}],
                },
            },
        },
        None,
    )

    item = captured["Item"]
    assert item["decision"] == "blocked"
    assert item["findings"] == [{"id": "f1", "severity": "HIGH"}]


def test_error_path_includes_error(handler, mocker):
    from common import audit as audit_module

    captured = _capture_put(mocker, audit_module)

    handler.lambda_handler(
        {
            "decision": "scan-error",
            "execution": "exec-3",
            "input": {
                "detail": {
                    "packageFormat": "npm",
                    "packageName": "lodash",
                    "packageVersion": "1.0.0",
                    "repositoryName": "test-staging-npm",
                },
                "error": {"Cause": "scan blew up"},
            },
        },
        None,
    )

    assert captured["Item"]["error"] == {"Cause": "scan blew up"}
