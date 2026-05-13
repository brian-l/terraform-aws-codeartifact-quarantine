"""Proactive cache-fill Lambda.

Runs on an EventBridge schedule. Pulls upstream package versions into staging
before any consumer asks for them, so the quarantine pipeline (scan, cooldown,
promote) runs in the background — by the time a developer needs a version,
it's already vetted and in prod.

Two stacked modes:

  - Follow-mode: for every package already cached in a staging repo, ask the
    upstream registry "what versions exist?" and fetch any that don't exist
    in staging yet. Watch set scales with team usage; nothing to maintain.
  - Allowlist-mode: explicit (format, name) entries get the same treatment
    even if they're not yet in staging. Right for known-critical deps you
    want vetted before anyone needs them.

The actual cache-fill is triggered by an authenticated HTTPS GET against the
staging repo's npm/pypi endpoint for the canonical asset of each new version
(tarball for npm, sdist or first wheel for PyPI). CodeArtifact handles the
upstream pull as a side effect of the asset request — exactly what
`npm install` / `pip install` do, just programmatic.

Each fetch writes one audit row into the existing promotion audit table with
`record_type = "proactive_fill"` and `decision = "fetched"`. The subsequent
promotion (after scan + cooldown) writes a separate row with
`record_type = "promotion"`, so consumers can join the two by package + version.

Inputs:
    EventBridge scheduled event — body ignored.

Environment:
    PROACTIVE_FILL_ALLOWLIST_JSON   — JSON-encoded list of {format,name,include_prereleases?}.
    PROACTIVE_FILL_INCLUDE_PRERELEASES — "true"|"false", global default.
    PROACTIVE_FILL_MAX_FETCHES      — Per-run cap on total fetches.
    PROACTIVE_FILL_REPOS_BY_FORMAT  — JSON object mapping ecosystem → staging repo name.
    PROACTIVE_FILL_HTTP_TIMEOUT     — Per-request timeout seconds (default 30 — tarballs can be large).
    AUDIT_TABLE_NAME                — Existing promotion audit table.

Plus the standard DOMAIN_NAME, DOMAIN_OWNER, SOURCE_REPO_NAMES, AWS_REGION, LOG_LEVEL.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import urllib.parse
import urllib.request
from base64 import b64encode
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, cast

import boto3

from common import audit
from common.config import from_env
from common.logging import get_logger

if TYPE_CHECKING:
    from mypy_boto3_codeartifact import CodeArtifactClient


log = get_logger(__name__)
config = from_env()
region = os.environ["AWS_REGION"]

_ca: CodeArtifactClient = boto3.client("codeartifact")


# Pre-release detection regexes — pragmatic, not exhaustive. Tolerate
# false-positives on weird version strings (we'd rather skip a rare stable
# release with an unusual format than pull every alpha into the cache).
#
# npm semver: anything with a hyphen after the version core is pre-release
# (1.0.0-alpha.1, 2.3.4-rc.1+build, ...).
_NPM_PRERELEASE = re.compile(r"^\d+\.\d+\.\d+-")

# PEP 440 pre-release: digit followed by a/b/c/rc/dev/alpha/beta/pre/preview
# marker, possibly preceded by . - or _. Catches 1.0.0a1, 1.0.0.dev2, 1.0a, etc.
_PYPI_PRERELEASE = re.compile(
    r"\d(?:[\.\-_])?(a|b|c|rc|alpha|beta|dev|pre|preview)\d*",
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True)
class Target:
    """A package to watch (repo, format, namespace, name)."""

    repo: str
    format: str
    namespace: str | None
    name: str
    include_prereleases: bool


@dataclasses.dataclass(frozen=True)
class FetchPlan:
    """One version to pull, and the asset path used to trigger cache-fill."""

    version: str
    asset_path: str  # path component appended to the staging endpoint
    is_prerelease: bool


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    cfg = _proactive_config()
    log.info(
        "proactive_fill start",
        extra={
            "schedule": event.get("time"),
            "include_prereleases": cfg["include_prereleases"],
            "max_fetches": cfg["max_fetches"],
        },
    )

    targets = list(_build_watch_set(cfg))
    log.info("watch set built", extra={"count": len(targets)})
    if not targets:
        return {"watched": 0, "fetched": 0}

    token = _get_auth_token()
    endpoints = _resolve_endpoints(config.source_repo_names)

    fetched = 0
    for target in targets:
        if fetched >= cfg["max_fetches"]:
            log.info("max_fetches reached", extra={"fetched": fetched})
            break
        try:
            plans = list(_plan_fetches(target))
        except Exception:
            log.exception(
                "failed to plan",
                extra={"package": target.name, "format": target.format},
            )
            continue
        for plan in plans:
            if fetched >= cfg["max_fetches"]:
                break
            try:
                _fetch_to_staging(target, plan, token, endpoints[target.repo])
                _record_fetch(target, plan)
                fetched += 1
            except Exception:
                log.exception(
                    "fetch failed",
                    extra={"package": target.name, "version": plan.version},
                )

    log.info("proactive_fill done", extra={"watched": len(targets), "fetched": fetched})
    return {"watched": len(targets), "fetched": fetched}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _proactive_config() -> dict[str, Any]:
    allowlist_json = os.environ.get("PROACTIVE_FILL_ALLOWLIST_JSON", "[]")
    allowlist = json.loads(allowlist_json)
    include_prereleases = (
        os.environ.get("PROACTIVE_FILL_INCLUDE_PRERELEASES", "false").lower() == "true"
    )
    return {
        "allowlist": allowlist,
        "include_prereleases": include_prereleases,
        "max_fetches": int(os.environ.get("PROACTIVE_FILL_MAX_FETCHES", "200")),
        "repos_by_format": json.loads(os.environ.get("PROACTIVE_FILL_REPOS_BY_FORMAT", "{}")),
    }


# ---------------------------------------------------------------------------
# Watch set construction
# ---------------------------------------------------------------------------


def _build_watch_set(cfg: dict[str, Any]) -> Iterator[Target]:
    """Union of follow-mode (everything already in staging) and the allowlist."""
    seen: set[tuple[str, str, str | None, str]] = set()
    global_prereleases = cfg["include_prereleases"]

    # Follow-mode: scan each staging repo.
    for repo in config.source_repo_names:
        for fmt, namespace, name in _list_packages(repo):
            key = (repo, fmt, namespace, name)
            if key in seen:
                continue  # pragma: no cover — defensive against duplicate ListPackages entries
            seen.add(key)
            yield Target(
                repo=repo,
                format=fmt,
                namespace=namespace,
                name=name,
                include_prereleases=global_prereleases,
            )

    # Allowlist: add entries even if not yet in staging. Map format → repo via
    # the env-supplied mapping (computed at the Terraform layer from var.repositories).
    repos_by_format = cfg["repos_by_format"]
    for entry in cfg["allowlist"]:
        fmt = entry["format"]
        repo = repos_by_format.get(fmt)
        if not repo:
            log.warning(
                "allowlist entry has no staging repo",
                extra={"format": fmt, "package": entry["name"]},
            )
            continue
        namespace, name = (
            _split_scoped_npm(entry["name"]) if fmt == "npm" else (None, entry["name"])
        )
        key = (repo, fmt, namespace, name)
        if key in seen:
            continue
        seen.add(key)
        per_entry = entry.get("include_prereleases")
        yield Target(
            repo=repo,
            format=fmt,
            namespace=namespace,
            name=name,
            include_prereleases=per_entry if per_entry is not None else global_prereleases,
        )


def _split_scoped_npm(name: str) -> tuple[str | None, str]:
    """`@scope/pkg` → (`@scope`, `pkg`); `pkg` → (None, `pkg`)."""
    if name.startswith("@") and "/" in name:
        scope, base = name.split("/", 1)
        return scope, base
    return None, name


def _list_packages(repo: str) -> Iterator[tuple[str, str | None, str]]:
    """Yield (format, namespace, name) for every package present in repo."""
    paginator = _ca.get_paginator("list_packages")
    for page in paginator.paginate(
        domain=config.domain_name,
        domainOwner=config.domain_owner,
        repository=repo,
    ):
        for p in page.get("packages", []):
            fmt = p.get("format")
            name = p.get("package")
            if not fmt or not name:
                continue  # pragma: no cover
            if fmt not in ("npm", "pypi"):
                continue  # other ecosystems not yet implemented
            yield str(fmt), p.get("namespace"), str(name)


# ---------------------------------------------------------------------------
# Fetch planning
# ---------------------------------------------------------------------------


def _plan_fetches(target: Target) -> Iterator[FetchPlan]:
    """Diff upstream versions against staging, yield FetchPlan per missing version."""
    cached = _list_cached_versions(target)
    candidates = _npm_candidates(target) if target.format == "npm" else _pypi_candidates(target)
    for plan in candidates:
        if plan.version in cached:
            continue
        if plan.is_prerelease and not target.include_prereleases:
            continue
        yield plan


def _list_cached_versions(target: Target) -> set[str]:
    paginator = _ca.get_paginator("list_package_versions")
    kwargs: dict[str, Any] = {
        "domain": config.domain_name,
        "domainOwner": config.domain_owner,
        "repository": target.repo,
        "format": target.format,
        "package": target.name,
    }
    if target.namespace:
        kwargs["namespace"] = target.namespace
    versions: set[str] = set()
    try:
        for page in paginator.paginate(**kwargs):
            for v in page.get("versions", []):
                ver = v.get("version")
                if ver:
                    versions.add(str(ver))
    except _ca.exceptions.ResourceNotFoundException:
        # Allowlist entry for a package nobody has ever fetched. Cache is empty.
        return set()
    return versions


def _npm_candidates(target: Target) -> list[FetchPlan]:
    full_name = f"{target.namespace}/{target.name}" if target.namespace else target.name
    url = f"https://registry.npmjs.org/{urllib.parse.quote(full_name, safe='@')}"
    packument = _http_get_json(url)
    versions: dict[str, Any] = packument.get("versions") or {}
    out: list[FetchPlan] = []
    for ver, entry in versions.items():
        tarball = (entry.get("dist") or {}).get("tarball")
        if not tarball:
            continue  # pragma: no cover — registry should always include dist.tarball
        # Use the basename from the tarball URL — handles scoped packages correctly
        # (URL: https://registry.npmjs.org/@scope/name/-/name-1.0.0.tgz; basename = "name-1.0.0.tgz").
        basename = tarball.rsplit("/", 1)[-1]
        path = f"{full_name}/-/{basename}"
        out.append(
            FetchPlan(
                version=str(ver),
                asset_path=path,
                is_prerelease=bool(_NPM_PRERELEASE.match(str(ver))),
            )
        )
    return out


def _pypi_candidates(target: Target) -> list[FetchPlan]:
    url = f"https://pypi.org/pypi/{urllib.parse.quote(target.name)}/json"
    body = _http_get_json(url)
    releases: dict[str, list[dict[str, Any]]] = body.get("releases") or {}
    out: list[FetchPlan] = []
    for ver, files in releases.items():
        if not files:
            # Versions with no files — typically a deleted release. Skip.
            continue
        primary = _pick_pypi_primary(files)
        if not primary:
            continue  # pragma: no cover
        out.append(
            FetchPlan(
                version=str(ver),
                # CodeArtifact's PyPI endpoint serves PEP 503 simple-index assets at
                # /simple/<normalized-name>/<filename>. Normalize per PEP 503 (lowercased,
                # runs of -._ collapsed to single -).
                asset_path=f"simple/{_pypi_normalize(target.name)}/{primary}",
                is_prerelease=bool(_PYPI_PRERELEASE.search(str(ver))),
            )
        )
    return out


def _pick_pypi_primary(files: list[dict[str, Any]]) -> str | None:
    """Prefer the sdist (smallest, always source-distributable); fall back to first wheel."""
    sdists = [f for f in files if str(f.get("filename", "")).endswith(".tar.gz")]
    if sdists:
        return str(sdists[0]["filename"])
    wheels = [f for f in files if str(f.get("filename", "")).endswith(".whl")]
    if wheels:
        return str(wheels[0]["filename"])
    return None


def _pypi_normalize(name: str) -> str:
    """PEP 503: lowercase, collapse runs of -._ to a single -."""
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------------------
# Fetch execution
# ---------------------------------------------------------------------------


def _get_auth_token() -> str:
    resp = _ca.get_authorization_token(
        domain=config.domain_name,
        domainOwner=config.domain_owner,
        # 12h max — plenty for a single Lambda run (15min hard limit).
        durationSeconds=43200,
    )
    return resp["authorizationToken"]


def _resolve_endpoints(repos: tuple[str, ...]) -> dict[str, str]:
    """Map repo name → its npm-or-pypi staging endpoint URL.

    We only need the endpoint for the ecosystem the repo serves; assume one
    external_connection per repo (CodeArtifact constraint).
    """
    endpoints: dict[str, str] = {}
    for repo in repos:
        info = _ca.describe_repository(
            domain=config.domain_name, domainOwner=config.domain_owner, repository=repo
        )
        # external_connections on the repo tells us which ecosystem; we ask for
        # the matching endpoint format.
        connections = info["repository"].get("externalConnections", [])
        for conn in connections:
            pkg_format = conn.get("packageFormat")
            if pkg_format in ("npm", "pypi"):
                ep = _ca.get_repository_endpoint(
                    domain=config.domain_name,
                    domainOwner=config.domain_owner,
                    repository=repo,
                    format=pkg_format,
                )
                endpoints[repo] = ep["repositoryEndpoint"].rstrip("/")
                break
    return endpoints


def _fetch_to_staging(target: Target, plan: FetchPlan, token: str, endpoint: str) -> None:
    """HTTPS GET against the staging endpoint to trigger upstream cache-fill.

    CodeArtifact accepts:
      - npm: Authorization: Bearer <token>
      - pypi: Authorization: Basic base64("aws:<token>") (PyPI simple-index auth)
    """
    url = f"{endpoint}/{plan.asset_path}"
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS endpoint: {url!r}")  # defense
    if target.format == "npm":
        auth = f"Bearer {token}"
    else:
        auth = "Basic " + b64encode(f"aws:{token}".encode()).decode()
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": auth,
            "User-Agent": "terraform-aws-codeartifact-quarantine/proactive-fill",
            "Accept": "*/*",
        },
    )
    timeout = float(os.environ.get("PROACTIVE_FILL_HTTP_TIMEOUT", "30"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # Drain the body so CodeArtifact completes the server-side cache write.
        # Discard bytes — they're already cached server-side and the EventBridge
        # event has fired (or is about to). Reading is required, body length isn't.
        while resp.read(65536):
            pass
    log.info(
        "fetched",
        extra={
            "package": target.name,
            "version": plan.version,
            "repository": target.repo,
        },
    )


def _record_fetch(target: Target, plan: FetchPlan) -> None:
    from common import codeartifact as ca

    arn = ca.package_arn(
        config.domain_name,
        config.domain_owner,
        region,
        target.repo,
        target.format,
        target.namespace,
        target.name,
    )
    audit.record(
        config.audit_table_name,
        package_arn=arn,
        version=plan.version,
        decision="fetched",
        execution="proactive-fill",
        record_type="proactive_fill",
        extra={
            "format": target.format,
            "namespace": target.namespace or "",
            "name": target.name,
            "repository": target.repo,
            "is_prerelease": plan.is_prerelease,
        },
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _http_get_json(url: str) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    timeout = float(os.environ.get("PROACTIVE_FILL_HTTP_TIMEOUT", "30"))
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "terraform-aws-codeartifact-quarantine/proactive-fill",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return cast("dict[str, Any]", json.loads(data))
