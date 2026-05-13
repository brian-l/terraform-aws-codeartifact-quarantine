"""Tests for common.codeartifact."""

from __future__ import annotations

from botocore.stub import Stubber


def test_package_arn_formats_without_namespace(fresh_module):
    ca = fresh_module("common.codeartifact")
    arn = ca.package_arn(
        domain="my-domain",
        owner="111111111111",
        region="us-east-1",
        repo="my-repo",
        fmt="npm",
        namespace=None,
        name="lodash",
    )
    # Empty namespace path segment is intentional — npm packages without a scope
    # appear with no namespace.
    assert (
        arn == "arn:aws:codeartifact:us-east-1:111111111111:package/my-domain/my-repo/npm//lodash"
    )


def test_package_arn_formats_with_namespace(fresh_module):
    ca = fresh_module("common.codeartifact")
    arn = ca.package_arn(
        domain="my-domain",
        owner="111111111111",
        region="us-east-1",
        repo="my-repo",
        fmt="npm",
        namespace="types",
        name="node",
    )
    assert (
        arn
        == "arn:aws:codeartifact:us-east-1:111111111111:package/my-domain/my-repo/npm/types/node"
    )


def test_describe_version_without_namespace(fresh_module):
    ca = fresh_module("common.codeartifact")
    stubber = Stubber(ca._client)
    stubber.add_response(
        "describe_package_version",
        {"packageVersion": {"version": "1.2.3", "status": "Published"}},
        expected_params={
            "domain": "my-domain",
            "domainOwner": "111111111111",
            "repository": "my-repo",
            "format": "npm",
            "package": "lodash",
            "packageVersion": "1.2.3",
        },
    )

    with stubber:
        result = ca.describe_version(
            domain="my-domain",
            owner="111111111111",
            repo="my-repo",
            fmt="npm",
            namespace=None,
            name="lodash",
            version="1.2.3",
        )

    assert result == {"version": "1.2.3", "status": "Published"}
    stubber.assert_no_pending_responses()


def test_describe_version_with_namespace(fresh_module):
    ca = fresh_module("common.codeartifact")
    stubber = Stubber(ca._client)
    stubber.add_response(
        "describe_package_version",
        {"packageVersion": {"version": "1.0.0"}},
        expected_params={
            "domain": "my-domain",
            "domainOwner": "111111111111",
            "repository": "my-repo",
            "format": "npm",
            "namespace": "types",
            "package": "node",
            "packageVersion": "1.0.0",
        },
    )

    with stubber:
        ca.describe_version(
            domain="my-domain",
            owner="111111111111",
            repo="my-repo",
            fmt="npm",
            namespace="types",
            name="node",
            version="1.0.0",
        )

    stubber.assert_no_pending_responses()


def test_copy_version_no_namespace_no_revision(fresh_module):
    ca = fresh_module("common.codeartifact")
    stubber = Stubber(ca._client)
    stubber.add_response(
        "copy_package_versions",
        {"successfulVersions": {"1.0.0": {"status": "Published"}}, "failedVersions": {}},
        expected_params={
            "domain": "my-domain",
            "domainOwner": "111111111111",
            "sourceRepository": "src-repo",
            "destinationRepository": "tgt-repo",
            "format": "npm",
            "package": "lodash",
            "versions": ["1.0.0"],
            "allowOverwrite": False,
            "includeFromUpstream": False,
        },
    )

    with stubber:
        result = ca.copy_version(
            domain="my-domain",
            owner="111111111111",
            source_repo="src-repo",
            target_repo="tgt-repo",
            fmt="npm",
            namespace=None,
            name="lodash",
            version="1.0.0",
        )

    assert result["successfulVersions"]["1.0.0"]["status"] == "Published"
    stubber.assert_no_pending_responses()


def test_copy_version_no_namespace_with_revision(fresh_module):
    ca = fresh_module("common.codeartifact")
    stubber = Stubber(ca._client)
    stubber.add_response(
        "copy_package_versions",
        {"successfulVersions": {}, "failedVersions": {}},
        expected_params={
            "domain": "my-domain",
            "domainOwner": "111111111111",
            "sourceRepository": "src-repo",
            "destinationRepository": "tgt-repo",
            "format": "pypi",
            "package": "requests",
            "versions": ["2.31.0"],
            "versionRevisions": {"2.31.0": "rev-xyz"},
            "allowOverwrite": False,
            "includeFromUpstream": False,
        },
    )

    with stubber:
        ca.copy_version(
            domain="my-domain",
            owner="111111111111",
            source_repo="src-repo",
            target_repo="tgt-repo",
            fmt="pypi",
            namespace=None,
            name="requests",
            version="2.31.0",
            revision="rev-xyz",
        )

    stubber.assert_no_pending_responses()


def test_copy_version_with_namespace_no_revision(fresh_module):
    ca = fresh_module("common.codeartifact")
    stubber = Stubber(ca._client)
    stubber.add_response(
        "copy_package_versions",
        {"successfulVersions": {}, "failedVersions": {}},
        expected_params={
            "domain": "my-domain",
            "domainOwner": "111111111111",
            "sourceRepository": "src-repo",
            "destinationRepository": "tgt-repo",
            "format": "npm",
            "namespace": "types",
            "package": "node",
            "versions": ["1.0.0"],
            "allowOverwrite": False,
            "includeFromUpstream": False,
        },
    )

    with stubber:
        ca.copy_version(
            domain="my-domain",
            owner="111111111111",
            source_repo="src-repo",
            target_repo="tgt-repo",
            fmt="npm",
            namespace="types",
            name="node",
            version="1.0.0",
        )

    stubber.assert_no_pending_responses()


def test_copy_version_with_namespace_and_revision(fresh_module):
    ca = fresh_module("common.codeartifact")
    stubber = Stubber(ca._client)
    stubber.add_response(
        "copy_package_versions",
        {"successfulVersions": {}, "failedVersions": {}},
        expected_params={
            "domain": "my-domain",
            "domainOwner": "111111111111",
            "sourceRepository": "src-repo",
            "destinationRepository": "tgt-repo",
            "format": "npm",
            "namespace": "types",
            "package": "node",
            "versions": ["1.0.0"],
            "versionRevisions": {"1.0.0": "abc123"},
            "allowOverwrite": False,
            "includeFromUpstream": False,
        },
    )

    with stubber:
        ca.copy_version(
            domain="my-domain",
            owner="111111111111",
            source_repo="src-repo",
            target_repo="tgt-repo",
            fmt="npm",
            namespace="types",
            name="node",
            version="1.0.0",
            revision="abc123",
        )

    stubber.assert_no_pending_responses()
