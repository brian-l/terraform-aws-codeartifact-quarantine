"""Yank / unpublish / malware-advisory check Lambda.

Runs on an EventBridge schedule. Enumerates package versions cached in each
staging repo, checks each against the configured sources (upstream registry +
OSV.dev), records the verdict in a state table, and (on a *newly observed*
yank/unpublish/malicious status) takes the configured response.

CodeArtifact treats cached package versions as canonical and never re-fetches
them. Without active yank detection, a version that gets unpublished or marked
malicious upstream continues to be served to consumers via the upstream chain
forever. This handler is the compensating control.

Inputs:
    EventBridge scheduled event — body ignored.

Environment:
    YANK_STATE_TABLE       — DynamoDB table tracking last-checked status per version.
    YANK_AUDIT_TABLE       — Sibling audit table for yank events (PK package_arn, SK version_ts).
    YANK_SOURCES           — Comma-separated subset of {"upstream", "osv"}.
    YANK_RESPONSE_YANKED   — Action for upstream-yanked / deprecated versions.
    YANK_RESPONSE_UNPUBLISHED — Action for versions removed from upstream.
    YANK_RESPONSE_MALICIOUS   — Action for OSV malware advisories.
        Action ∈ {"alert", "unlist", "dispose", "delete"}.
    NOTIFICATION_TOPIC_ARN — SNS topic for alerts (always published, all statuses).
    YANK_RECHECK_SECONDS   — Skip versions checked more recently than this (default 3000s ≈ 50min).
    YANK_MAX_WORKERS       — Concurrent HTTP workers (default 10).
    YANK_HTTP_TIMEOUT      — Per-request timeout in seconds (default 5).

Plus the standard DOMAIN_NAME, DOMAIN_OWNER, SOURCE_REPO_NAMES, AUDIT_TABLE_NAME
(unused here, but inherited from common_env), STATE_MACHINE_ARN, LOG_LEVEL.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any, cast

import boto3

from common import codeartifact as ca
from common.config import from_env
from common.logging import get_logger

if TYPE_CHECKING:
    from mypy_boto3_codeartifact import CodeArtifactClient
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource
    from mypy_boto3_sns import SNSClient


log = get_logger(__name__)
config = from_env()
region = os.environ["AWS_REGION"]

_ca: CodeArtifactClient = boto3.client("codeartifact")
_dynamodb: DynamoDBServiceResource = boto3.resource("dynamodb")
_sns: SNSClient = boto3.client("sns")


# ---- Status constants. Kept as plain strings rather than an Enum so they ----
# travel cleanly through DynamoDB / SNS / audit rows without conversion.
STATUS_CLEAN = "clean"
STATUS_YANKED = "yanked"
STATUS_UNPUBLISHED = "unpublished"
STATUS_MALICIOUS = "malicious"
STATUS_UNKNOWN = "unknown"  # source check failed (e.g. network) — retry next run

ACTION_ALERT = "alert"
ACTION_UNLIST = "unlist"
ACTION_DISPOSE = "dispose"
ACTION_DELETE = "delete"

# Map CodeArtifact package formats to (upstream-checker name, OSV ecosystem name).
# Other formats (maven, nuget, gem, generic) can be added by extending here +
# implementing a checker; we ship npm + pypi which cover ~95% of usage.
_ECOSYSTEMS: dict[str, str] = {
    "npm": "npm",
    "pypi": "PyPI",
}


@dataclasses.dataclass(frozen=True)
class PackageVersion:
    """Identity of one CodeArtifact-cached package version."""

    repo: str
    format: str
    namespace: str | None
    name: str
    version: str
    revision: str | None

    @property
    def package_arn(self) -> str:
        return ca.package_arn(
            config.domain_name,
            config.domain_owner,
            region,
            self.repo,
            self.format,
            self.namespace,
            self.name,
        )


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """Outcome of checking one version against the configured sources."""

    status: str
    reason: str
    source: str  # "upstream" | "osv" | "" (clean / unknown)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    cfg = _yank_config()
    log.info("yank_check start", extra={"sources": cfg["sources"], "schedule": event.get("time")})

    versions = list(_enumerate_versions(config.source_repo_names))
    log.info("enumerated versions", extra={"count": len(versions)})

    recheck_seconds = int(os.environ.get("YANK_RECHECK_SECONDS", "3000"))
    state_table = _dynamodb.Table(os.environ["YANK_STATE_TABLE"])
    pending = [v for v in versions if _needs_check(state_table, v, recheck_seconds)]
    log.info("versions due for check", extra={"count": len(pending)})

    if not pending:
        return {"checked": 0, "actions": 0}

    actions_taken = 0
    max_workers = int(os.environ.get("YANK_MAX_WORKERS", "10"))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_check_one, v, cfg["sources"]): v for v in pending}
        for fut in as_completed(futures):
            v = futures[fut]
            try:
                result = fut.result()
            except Exception:
                log.exception("check failed", extra={"package": v.name, "version": v.version})
                continue
            prior = _load_prior_status(state_table, v)
            _record_state(state_table, v, result)
            if _is_new_finding(prior, result):
                action = cfg["response"].get(result.status, ACTION_ALERT)
                _act(v, result, action)
                actions_taken += 1

    log.info("yank_check done", extra={"checked": len(pending), "actions": actions_taken})
    return {"checked": len(pending), "actions": actions_taken}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _yank_config() -> dict[str, Any]:
    sources_csv = os.environ.get("YANK_SOURCES", "upstream,osv")
    return {
        "sources": tuple(s.strip() for s in sources_csv.split(",") if s.strip()),
        "response": {
            STATUS_YANKED: os.environ.get("YANK_RESPONSE_YANKED", ACTION_UNLIST),
            STATUS_UNPUBLISHED: os.environ.get("YANK_RESPONSE_UNPUBLISHED", ACTION_DISPOSE),
            STATUS_MALICIOUS: os.environ.get("YANK_RESPONSE_MALICIOUS", ACTION_DISPOSE),
        },
    }


# ---------------------------------------------------------------------------
# CodeArtifact enumeration
# ---------------------------------------------------------------------------


def _enumerate_versions(repos: tuple[str, ...]) -> Iterator[PackageVersion]:
    """Yield every package version cached in each staging repo.

    Two-step listing: ListPackages per repo, then ListPackageVersions per package.
    CodeArtifact has no single-call "list every version in a repo" API.
    """
    for repo in repos:
        for fmt, namespace, name in _list_packages(repo):
            for version in _list_versions(repo, fmt, namespace, name):
                yield PackageVersion(
                    repo=repo,
                    format=fmt,
                    namespace=namespace,
                    name=name,
                    version=version,
                    revision=None,
                )


def _list_packages(repo: str) -> Iterator[tuple[str, str | None, str]]:
    paginator = _ca.get_paginator("list_packages")
    for page in paginator.paginate(
        domain=config.domain_name,
        domainOwner=config.domain_owner,
        repository=repo,
    ):
        for p in page.get("packages", []):
            fmt = p.get("format")
            name = p.get("package")
            # boto3-stubs marks these as optional; CodeArtifact always returns them.
            if not fmt or not name:
                continue  # pragma: no cover
            yield str(fmt), p.get("namespace"), str(name)


def _list_versions(repo: str, fmt: str, namespace: str | None, name: str) -> Iterator[str]:
    paginator = _ca.get_paginator("list_package_versions")
    kwargs: dict[str, Any] = {
        "domain": config.domain_name,
        "domainOwner": config.domain_owner,
        "repository": repo,
        "format": fmt,
        "package": name,
        # status="Published" — Unlisted/Disposed entries are already in their
        # post-yank state; re-checking them every hour is wasteful.
        "status": "Published",
    }
    if namespace:
        kwargs["namespace"] = namespace
    for page in paginator.paginate(**kwargs):
        for v in page.get("versions", []):
            ver = v.get("version")
            if ver:
                yield str(ver)


# ---------------------------------------------------------------------------
# State table
# ---------------------------------------------------------------------------


def _needs_check(table: Any, v: PackageVersion, recheck_seconds: int) -> bool:
    item = table.get_item(Key={"package_arn": v.package_arn, "version": v.version}).get("Item")
    if not item:
        return True
    last_iso = item.get("last_checked")
    if not last_iso:
        return True
    try:
        last = dt.datetime.fromisoformat(last_iso)
    except ValueError:
        return True
    age = (dt.datetime.now(dt.UTC) - last).total_seconds()
    return age >= recheck_seconds


def _load_prior_status(table: Any, v: PackageVersion) -> str:
    item = table.get_item(Key={"package_arn": v.package_arn, "version": v.version}).get("Item")
    if not item:
        return STATUS_CLEAN  # first observation; anything other than clean is "new"
    return str(item.get("status", STATUS_CLEAN))


def _record_state(table: Any, v: PackageVersion, result: CheckResult) -> None:
    table.put_item(
        Item={
            "package_arn": v.package_arn,
            "version": v.version,
            "last_checked": dt.datetime.now(dt.UTC).isoformat(),
            "status": result.status,
            "reason": result.reason,
            "source": result.source,
            "format": v.format,
            "repository": v.repo,
        }
    )


def _is_new_finding(prior: str, result: CheckResult) -> bool:
    if result.status in (STATUS_CLEAN, STATUS_UNKNOWN):
        return False
    return prior != result.status


# ---------------------------------------------------------------------------
# Source checks
# ---------------------------------------------------------------------------


def _check_one(v: PackageVersion, sources: tuple[str, ...]) -> CheckResult:
    """Combine source signals. Most severe wins (malicious > unpublished > yanked > clean)."""
    if v.format not in _ECOSYSTEMS:
        # Unsupported ecosystem — record as clean rather than spamming logs.
        return CheckResult(status=STATUS_CLEAN, reason="unsupported ecosystem", source="")

    findings: list[CheckResult] = []

    if "upstream" in sources:
        try:
            findings.append(_check_upstream(v))
        except _TransientHTTPError as e:
            log.warning("upstream check transient error", extra={"err": str(e)})
            findings.append(CheckResult(status=STATUS_UNKNOWN, reason=str(e), source="upstream"))

    if "osv" in sources:
        try:
            findings.append(_check_osv(v))
        except _TransientHTTPError as e:
            log.warning("osv check transient error", extra={"err": str(e)})
            findings.append(CheckResult(status=STATUS_UNKNOWN, reason=str(e), source="osv"))

    severity_order = {
        STATUS_MALICIOUS: 4,
        STATUS_UNPUBLISHED: 3,
        STATUS_YANKED: 2,
        STATUS_UNKNOWN: 1,
        STATUS_CLEAN: 0,
    }
    return max(findings, key=lambda r: severity_order[r.status])


class _TransientHTTPError(RuntimeError):
    """Network / 5xx / parse failure that should retry next run, not flip status."""


def _check_upstream(v: PackageVersion) -> CheckResult:
    ecosystem = _ECOSYSTEMS[v.format]
    if ecosystem == "PyPI":
        return _check_pypi(v.name, v.version)
    if ecosystem == "npm":
        return _check_npm(v.namespace, v.name, v.version)
    # Unreachable while _ECOSYSTEMS only maps to PyPI/npm; safety net for
    # future ecosystem entries that lack a dispatch arm.
    return CheckResult(
        status=STATUS_CLEAN, reason="unsupported", source="upstream"
    )  # pragma: no cover


def _check_pypi(name: str, version: str) -> CheckResult:
    """PEP 592 yank semantics. 404 on the version URL means it was removed."""
    url = f"https://pypi.org/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/json"
    try:
        body = _http_get_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return CheckResult(status=STATUS_UNPUBLISHED, reason="pypi 404", source="upstream")
        raise _TransientHTTPError(f"pypi {e.code}") from e
    info = body.get("info", {}) or {}
    if info.get("yanked"):
        reason = info.get("yanked_reason") or "yanked"
        return CheckResult(status=STATUS_YANKED, reason=str(reason), source="upstream")
    return CheckResult(status=STATUS_CLEAN, reason="", source="upstream")


def _check_npm(namespace: str | None, name: str, version: str) -> CheckResult:
    """npm has no first-class yank; deprecation is the moral equivalent, and a
    missing version in the packument means unpublish/security-removal."""
    full = f"{namespace}/{name}" if namespace else name
    url = f"https://registry.npmjs.org/{urllib.parse.quote(full, safe='@')}"
    try:
        packument = _http_get_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return CheckResult(status=STATUS_UNPUBLISHED, reason="npm 404", source="upstream")
        raise _TransientHTTPError(f"npm {e.code}") from e
    versions = packument.get("versions") or {}
    entry = versions.get(version)
    if entry is None:
        return CheckResult(
            status=STATUS_UNPUBLISHED, reason="missing from packument", source="upstream"
        )
    deprecated = entry.get("deprecated")
    if deprecated:
        return CheckResult(status=STATUS_YANKED, reason=str(deprecated), source="upstream")
    return CheckResult(status=STATUS_CLEAN, reason="", source="upstream")


def _check_osv(v: PackageVersion) -> CheckResult:
    """OSV.dev: query single package@version. Use the single-query endpoint
    rather than batch — concurrency at the executor level is simpler than
    chunking. Switch to /querybatch if Lambda CPU profiling shows we need it.
    """
    ecosystem = _ECOSYSTEMS[v.format]
    pkg_name = f"{v.namespace}/{v.name}" if v.namespace and v.format == "npm" else v.name
    body = json.dumps(
        {"package": {"name": pkg_name, "ecosystem": ecosystem}, "version": v.version}
    ).encode()
    try:
        resp = _http_post_json("https://api.osv.dev/v1/query", body)
    except urllib.error.HTTPError as e:
        raise _TransientHTTPError(f"osv {e.code}") from e
    vulns = resp.get("vulns") or []
    if not vulns:
        return CheckResult(status=STATUS_CLEAN, reason="", source="osv")

    # Malware advisories: OSV gives them id prefix "MAL-" and/or a type field.
    # Withdrawn advisories shouldn't promote a "malicious" verdict.
    active = [vu for vu in vulns if not vu.get("withdrawn")]
    if not active:
        return CheckResult(status=STATUS_CLEAN, reason="all advisories withdrawn", source="osv")
    malicious = [vu for vu in active if str(vu.get("id", "")).startswith("MAL-") or _is_malware(vu)]
    if malicious:
        return CheckResult(
            status=STATUS_MALICIOUS,
            reason=f"osv: {malicious[0].get('id')} {malicious[0].get('summary', '')}".strip(),
            source="osv",
        )
    # Non-malware vulns: we leave to the scan handler / Inspector path. OSV is
    # used here only for the "this is bad and pip won't tell you" cases.
    return CheckResult(status=STATUS_CLEAN, reason="non-malware advisories only", source="osv")


def _is_malware(vuln: dict[str, Any]) -> bool:
    # GHSA / OSV both surface malware via the "type" field or "database_specific".
    if str(vuln.get("type", "")).upper() == "MALWARE":
        return True
    db = vuln.get("database_specific") or {}
    return str(db.get("malicious_package", "")).lower() == "true"


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only — no extra deps in the Lambda zip)
# ---------------------------------------------------------------------------


def _http_get_json(url: str) -> dict[str, Any]:
    return _http_request(url, method="GET")


def _http_post_json(url: str, body: bytes) -> dict[str, Any]:
    return _http_request(url, method="POST", body=body)


def _http_request(url: str, *, method: str, body: bytes | None = None) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url}")  # defense against env-var injection
    timeout = float(os.environ.get("YANK_HTTP_TIMEOUT", "5"))
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "User-Agent": "terraform-aws-codeartifact-quarantine/yank-check",
            "Accept": "application/json",
            "Content-Type": "application/json" if body else "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return cast("dict[str, Any]", json.loads(data))


# ---------------------------------------------------------------------------
# Response actions
# ---------------------------------------------------------------------------


def _act(v: PackageVersion, result: CheckResult, action: str) -> None:
    log.info(
        "yank action",
        extra={
            "package": v.name,
            "version": v.version,
            "status": result.status,
            "action": action,
        },
    )

    # Always alert + audit regardless of action.
    _publish_sns(v, result, action)
    _write_audit(v, result, action)

    if action == ACTION_ALERT:
        return
    # Apply in staging *and* prod (promoted versions live as physical copies
    # in prod; the staging-side action alone doesn't reach them).
    target_repos = [v.repo, config.target_repo_name]
    for repo in target_repos:
        try:
            _apply_action_to_repo(v, repo, action)
        except _ca.exceptions.ResourceNotFoundException:
            # Version doesn't exist in this repo (typical for prod when version
            # was never promoted). Not an error.
            log.info("skip action (no version in repo)", extra={"repo": repo})


def _apply_action_to_repo(v: PackageVersion, repo: str, action: str) -> None:
    kwargs: dict[str, Any] = {
        "domain": config.domain_name,
        "domainOwner": config.domain_owner,
        "repository": repo,
        "format": v.format,
        "package": v.name,
        "versions": [v.version],
    }
    if v.namespace:
        kwargs["namespace"] = v.namespace

    if action == ACTION_UNLIST:
        _ca.update_package_versions_status(**kwargs, targetStatus="Unlisted")
    elif action == ACTION_DISPOSE:
        _ca.dispose_package_versions(**kwargs)
    elif action == ACTION_DELETE:
        _ca.delete_package_versions(**kwargs)
    else:
        raise ValueError(f"unknown action: {action!r}")


def _publish_sns(v: PackageVersion, result: CheckResult, action: str) -> None:
    topic = os.environ.get("NOTIFICATION_TOPIC_ARN")
    if not topic:
        log.warning("no NOTIFICATION_TOPIC_ARN; skipping SNS publish")
        return
    payload = {
        "event": "yank-detected",
        "package_arn": v.package_arn,
        "format": v.format,
        "namespace": v.namespace,
        "name": v.name,
        "version": v.version,
        "repository": v.repo,
        "status": result.status,
        "reason": result.reason,
        "source": result.source,
        "action": action,
    }
    _sns.publish(
        TopicArn=topic,
        Subject=f"[quarantine] {result.status}: {v.name}@{v.version}"[:100],
        Message=json.dumps(payload, indent=2),
    )


def _write_audit(v: PackageVersion, result: CheckResult, action: str) -> None:
    table_name = os.environ["YANK_AUDIT_TABLE"]
    table = _dynamodb.Table(table_name)
    now = dt.datetime.now(dt.UTC).isoformat()
    table.put_item(
        Item={
            "package_arn": v.package_arn,
            "version_ts": f"{v.version}#{now}",
            "version": v.version,
            "status": result.status,
            "reason": result.reason,
            "source": result.source,
            "action": action,
            "repository": v.repo,
            "format": v.format,
            "ts": now,
        }
    )
