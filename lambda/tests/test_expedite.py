"""Tests for the expedite Lambda."""

from __future__ import annotations

import json

import pytest
from botocore.stub import ANY, Stubber


@pytest.fixture
def handler(fresh_module):
    return fresh_module("expedite.handler")


@pytest.fixture
def stub_ca_and_sfn(handler):
    """Yield (ca_stubber, sfn_stubber) — caller adds responses, then enters their contexts."""
    from common import codeartifact as ca

    return Stubber(ca._client), Stubber(handler.sfn)


def test_happy_path_with_explicit_source(handler, stub_ca_and_sfn):
    ca_st, sfn_st = stub_ca_and_sfn
    ca_st.add_response(
        "describe_package_version",
        {"packageVersion": {"version": "1.2.3"}},
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "repository": "test-staging-npm",
            "format": "npm",
            "package": "lodash",
            "packageVersion": "1.2.3",
        },
    )
    sfn_st.add_response(
        "start_execution",
        {
            "executionArn": "arn:...:execution:test-quarantine:exp-1",
            "startDate": __import__("datetime").datetime(2026, 1, 1),
        },
        expected_params={"stateMachineArn": ANY, "name": ANY, "input": ANY},
    )

    with ca_st, sfn_st:
        result = handler.lambda_handler(
            {
                "format": "npm",
                "name": "lodash",
                "version": "1.2.3",
                "source_repository": "test-staging-npm",
                "reason": "CVE-2026-0001",
            },
            None,
        )

    assert result["expedited"] is True
    assert result["executionArn"].endswith("exp-1")
    ca_st.assert_no_pending_responses()
    sfn_st.assert_no_pending_responses()


def test_rejects_unknown_source_repository(handler):
    with pytest.raises(ValueError, match="not configured"):
        handler.lambda_handler(
            {
                "format": "npm",
                "name": "lodash",
                "version": "1.2.3",
                "source_repository": "wrong-repo",
                "reason": "test",
            },
            None,
        )


def test_multiple_sources_require_explicit_repo(handler):
    # Conftest configures SOURCE_REPO_NAMES="test-staging-npm,test-staging-pypi" → 2 sources.
    with pytest.raises(ValueError, match="multiple source repositories"):
        handler.lambda_handler(
            {
                "format": "npm",
                "name": "lodash",
                "version": "1.2.3",
                # source_repository omitted
                "reason": "test",
            },
            None,
        )


def test_single_source_defaults(monkeypatch, fresh_module):
    monkeypatch.setenv("SOURCE_REPO_NAMES", "only-staging")
    handler = fresh_module("expedite.handler")

    from common import codeartifact as ca

    ca_st = Stubber(ca._client)
    ca_st.add_response(
        "describe_package_version",
        {"packageVersion": {"version": "1.0.0"}},
        expected_params={
            "domain": ANY,
            "domainOwner": ANY,
            "repository": "only-staging",
            "format": ANY,
            "package": ANY,
            "packageVersion": ANY,
        },
    )
    sfn_st = Stubber(handler.sfn)
    sfn_st.add_response(
        "start_execution",
        {"executionArn": "arn:...", "startDate": __import__("datetime").datetime(2026, 1, 1)},
        expected_params={"stateMachineArn": ANY, "name": ANY, "input": ANY},
    )

    with ca_st, sfn_st:
        handler.lambda_handler(
            {"format": "npm", "name": "lodash", "version": "1.0.0", "reason": "x"},
            None,
        )


def test_expedite_payload_skips_cooldown(handler, stub_ca_and_sfn, mocker):
    ca_st, _ = stub_ca_and_sfn
    ca_st.add_response(
        "describe_package_version",
        {"packageVersion": {"version": "1.2.3"}},
        expected_params={
            "domain": ANY,
            "domainOwner": ANY,
            "repository": ANY,
            "format": ANY,
            "package": ANY,
            "packageVersion": ANY,
        },
    )

    captured: dict = {}

    def fake_start(**kwargs):
        captured.update(kwargs)
        return {"executionArn": "arn:...", "startDate": __import__("datetime").datetime(2026, 1, 1)}

    mocker.patch.object(handler.sfn, "start_execution", side_effect=fake_start)

    with ca_st:
        handler.lambda_handler(
            {
                "format": "npm",
                "name": "lodash",
                "version": "1.2.3",
                "source_repository": "test-staging-npm",
                "reason": "test",
            },
            None,
        )

    payload = json.loads(captured["input"])
    assert payload["skipCooldown"] is True
    # Expedites always require human approval.
    assert payload["approvalMode"] == "always"
    assert "expedite" in payload
    assert payload["expedite"]["reason"] == "test"
