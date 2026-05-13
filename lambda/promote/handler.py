"""Promote Lambda.

Copies a package version from the source (staging) repository to the target
(prod) repository using CopyPackageVersions.

Input (from SFN):
    {
      "detail": { packageFormat, packageName, packageNamespace, packageVersion, packageVersionRevision, ... },
      ...
    }

Output:
    {
      "promoted": true,
      "successful": [...],
      "failed": [...]
    }
"""

from __future__ import annotations

from typing import Any

from common import codeartifact as ca
from common.config import from_env
from common.logging import get_logger

log = get_logger(__name__)
config = from_env()


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    detail = event.get("detail", {})
    fmt = detail["packageFormat"]
    namespace = detail.get("packageNamespace")
    name = detail["packageName"]
    version = detail["packageVersion"]
    revision = detail.get("packageVersionRevision")
    # Source repo identity comes from the EventBridge payload (or the expedite
    # request), not config — multiple staging repos may all funnel into the
    # same pipeline.
    source_repo = detail["repositoryName"]

    if source_repo not in config.source_repo_names:
        raise RuntimeError(
            f"refusing to promote from unrecognised repo {source_repo!r}; "
            f"allowed: {config.source_repo_names}"
        )

    log.info("promoting", extra={"package": name, "version": version})

    result = ca.copy_version(
        domain=config.domain_name,
        owner=config.domain_owner,
        source_repo=source_repo,
        target_repo=config.target_repo_name,
        fmt=fmt,
        namespace=namespace,
        name=name,
        version=version,
        revision=revision,
    )

    successful = result.get("successfulVersions", {})
    failed = result.get("failedVersions", {})

    if failed:
        log.error("promotion failed", extra={"failed": failed})
        raise RuntimeError(f"copy-package-versions reported failures: {failed}")

    log.info("promoted", extra={"package": name, "version": version})
    return {"promoted": True, "successful": successful, "failed": failed}
