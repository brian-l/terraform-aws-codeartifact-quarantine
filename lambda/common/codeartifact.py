"""Thin wrappers around boto3 codeartifact client calls.

Why wrappers: every handler ends up writing the same domain/owner/repo bookkeeping;
centralising it keeps individual handlers tight.

The `# type: ignore[arg-type]` on `format=` calls is intentional: boto3-stubs
type the format parameter as a `Literal[...]` of known package formats, but our
handlers receive the format as a plain str from EventBridge events. The runtime
values are always valid (they come from CodeArtifact itself).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import boto3

if TYPE_CHECKING:
    from mypy_boto3_codeartifact import CodeArtifactClient

_client: CodeArtifactClient = boto3.client("codeartifact")


def package_arn(
    domain: str, owner: str, region: str, repo: str, fmt: str, namespace: str | None, name: str
) -> str:
    """Construct a CodeArtifact package ARN.

    Format: arn:aws:codeartifact:<region>:<owner>:package/<domain>/<repo>/<format>/<namespace>/<name>
    Namespace is empty for npm packages without a scope.
    """
    ns = namespace or ""
    return f"arn:aws:codeartifact:{region}:{owner}:package/{domain}/{repo}/{fmt}/{ns}/{name}"


def describe_version(
    domain: str,
    owner: str,
    repo: str,
    fmt: str,
    namespace: str | None,
    name: str,
    version: str,
) -> dict[str, Any]:
    """Describe a single package version. Returns the `packageVersion` payload."""
    if namespace:
        result = _client.describe_package_version(
            domain=domain,
            domainOwner=owner,
            repository=repo,
            format=fmt,  # type: ignore[arg-type]
            namespace=namespace,
            package=name,
            packageVersion=version,
        )
    else:
        result = _client.describe_package_version(
            domain=domain,
            domainOwner=owner,
            repository=repo,
            format=fmt,  # type: ignore[arg-type]
            package=name,
            packageVersion=version,
        )
    return cast("dict[str, Any]", result["packageVersion"])


def copy_version(
    domain: str,
    owner: str,
    source_repo: str,
    target_repo: str,
    fmt: str,
    namespace: str | None,
    name: str,
    version: str,
    revision: str | None = None,
) -> dict[str, Any]:
    """Copy a single package version from source_repo to target_repo."""
    version_revisions = {version: revision} if revision else None
    if namespace and version_revisions:
        result = _client.copy_package_versions(
            domain=domain,
            domainOwner=owner,
            sourceRepository=source_repo,
            destinationRepository=target_repo,
            format=fmt,  # type: ignore[arg-type]
            namespace=namespace,
            package=name,
            versions=[version],
            versionRevisions=version_revisions,
            allowOverwrite=False,
            includeFromUpstream=False,
        )
    elif namespace:
        result = _client.copy_package_versions(
            domain=domain,
            domainOwner=owner,
            sourceRepository=source_repo,
            destinationRepository=target_repo,
            format=fmt,  # type: ignore[arg-type]
            namespace=namespace,
            package=name,
            versions=[version],
            allowOverwrite=False,
            includeFromUpstream=False,
        )
    elif version_revisions:
        result = _client.copy_package_versions(
            domain=domain,
            domainOwner=owner,
            sourceRepository=source_repo,
            destinationRepository=target_repo,
            format=fmt,  # type: ignore[arg-type]
            package=name,
            versions=[version],
            versionRevisions=version_revisions,
            allowOverwrite=False,
            includeFromUpstream=False,
        )
    else:
        result = _client.copy_package_versions(
            domain=domain,
            domainOwner=owner,
            sourceRepository=source_repo,
            destinationRepository=target_repo,
            format=fmt,  # type: ignore[arg-type]
            package=name,
            versions=[version],
            allowOverwrite=False,
            includeFromUpstream=False,
        )
    return cast("dict[str, Any]", result)
