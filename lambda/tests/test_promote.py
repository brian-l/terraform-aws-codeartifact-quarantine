"""Tests for the promote Lambda."""

from __future__ import annotations

import pytest
from botocore.stub import Stubber


@pytest.fixture
def handler(fresh_module):
    return fresh_module("promote.handler")


def test_happy_path(handler):
    stubber = Stubber(handler.ca._client)
    stubber.add_response(
        "copy_package_versions",
        {
            "successfulVersions": {"1.2.3": {"revision": "abc", "status": "Published"}},
            "failedVersions": {},
        },
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "sourceRepository": "test-staging-npm",
            "destinationRepository": "test-prod",
            "format": "npm",
            "package": "lodash",
            "versions": ["1.2.3"],
            "allowOverwrite": False,
            "includeFromUpstream": False,
        },
    )

    with stubber:
        result = handler.lambda_handler(
            {
                "detail": {
                    "packageFormat": "npm",
                    "packageName": "lodash",
                    "packageVersion": "1.2.3",
                    "repositoryName": "test-staging-npm",
                }
            },
            None,
        )

    assert result["promoted"] is True
    stubber.assert_no_pending_responses()


def test_rejects_unknown_source_repo(handler):
    with pytest.raises(RuntimeError, match="unrecognised repo"):
        handler.lambda_handler(
            {
                "detail": {
                    "packageFormat": "npm",
                    "packageName": "lodash",
                    "packageVersion": "1.2.3",
                    "repositoryName": "attacker-repo",
                }
            },
            None,
        )


def test_raises_on_failed_versions(handler):
    stubber = Stubber(handler.ca._client)
    stubber.add_response(
        "copy_package_versions",
        {
            "successfulVersions": {},
            "failedVersions": {"1.2.3": {"errorCode": "NOT_FOUND", "errorMessage": "x"}},
        },
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "sourceRepository": "test-staging-npm",
            "destinationRepository": "test-prod",
            "format": "npm",
            "package": "lodash",
            "versions": ["1.2.3"],
            "allowOverwrite": False,
            "includeFromUpstream": False,
        },
    )

    with stubber, pytest.raises(RuntimeError, match="reported failures"):
        handler.lambda_handler(
            {
                "detail": {
                    "packageFormat": "npm",
                    "packageName": "lodash",
                    "packageVersion": "1.2.3",
                    "repositoryName": "test-staging-npm",
                }
            },
            None,
        )


def test_namespace_passed_through(handler):
    stubber = Stubber(handler.ca._client)
    stubber.add_response(
        "copy_package_versions",
        {"successfulVersions": {"1.0.0": {}}, "failedVersions": {}},
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "sourceRepository": "test-staging-npm",
            "destinationRepository": "test-prod",
            "format": "npm",
            "namespace": "types",
            "package": "node",
            "versions": ["1.0.0"],
            "allowOverwrite": False,
            "includeFromUpstream": False,
        },
    )

    with stubber:
        result = handler.lambda_handler(
            {
                "detail": {
                    "packageFormat": "npm",
                    "packageNamespace": "types",
                    "packageName": "node",
                    "packageVersion": "1.0.0",
                    "repositoryName": "test-staging-npm",
                }
            },
            None,
        )

    assert result["promoted"] is True
    stubber.assert_no_pending_responses()
