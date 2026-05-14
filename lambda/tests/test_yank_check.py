"""Tests for the yank_check Lambda."""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest
from botocore.stub import Stubber


@pytest.fixture
def handler(fresh_module):
    return fresh_module("yank_check.handler")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTable:
    """Stand-in for boto3.resource('dynamodb').Table — captures put/get calls."""

    def __init__(self, name: str, initial: dict[tuple, dict[str, Any]] | None = None):
        self.name = name
        self.items = initial or {}
        self.put_calls: list[dict[str, Any]] = []

    def get_item(self, *, Key: dict[str, Any]) -> dict[str, Any]:
        key_tuple = tuple(sorted(Key.items()))
        item = self.items.get(key_tuple)
        return {"Item": item} if item else {}

    def put_item(self, *, Item: dict[str, Any]) -> dict[str, Any]:
        self.put_calls.append(Item)
        if "package_arn" in Item and "version" in Item:
            self.items[
                tuple(
                    sorted({"package_arn": Item["package_arn"], "version": Item["version"]}.items())
                )
            ] = Item
        return {}


@pytest.fixture
def tables(handler, mocker) -> dict[str, _FakeTable]:
    state = _FakeTable("test-yank-state")
    audit = _FakeTable("test-yank-audit")

    def fake_table(name: str):
        if name == "test-yank-state":
            return state
        if name == "test-yank-audit":
            return audit
        raise AssertionError(f"unexpected table {name!r}")  # pragma: no cover

    mocker.patch.object(handler._dynamodb, "Table", side_effect=fake_table)
    return {"state": state, "audit": audit}


def _http_resp(status: int, body: dict[str, Any]):
    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _R(json.dumps(body).encode())


def _http_error(code: int, url: str = "https://example/"):
    return urllib.error.HTTPError(url, code, "", {}, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Source checks
# ---------------------------------------------------------------------------


def test_pypi_yanked(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(200, {"info": {"yanked": True, "yanked_reason": "broken build"}}),
    )
    r = handler._check_pypi("flask", "1.0.0")
    assert r.status == handler.STATUS_YANKED
    assert "broken build" in r.reason


def test_pypi_clean(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(200, {"info": {"yanked": False}}),
    )
    assert handler._check_pypi("flask", "1.0.0").status == handler.STATUS_CLEAN


def test_pypi_unpublished(handler, mocker):
    mocker.patch.object(handler.urllib.request, "urlopen", side_effect=_http_error(404))
    assert handler._check_pypi("flask", "1.0.0").status == handler.STATUS_UNPUBLISHED


def test_pypi_transient_error(handler, mocker):
    mocker.patch.object(handler.urllib.request, "urlopen", side_effect=_http_error(503))
    with pytest.raises(handler._TransientHTTPError):
        handler._check_pypi("flask", "1.0.0")


def test_npm_deprecated(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(200, {"versions": {"1.0.0": {"deprecated": "use foo@^2 instead"}}}),
    )
    r = handler._check_npm(None, "foo", "1.0.0")
    assert r.status == handler.STATUS_YANKED
    assert "use foo" in r.reason


def test_npm_missing_from_packument(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(200, {"versions": {"2.0.0": {}}}),
    )
    assert handler._check_npm(None, "foo", "1.0.0").status == handler.STATUS_UNPUBLISHED


def test_npm_scoped_clean(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(200, {"versions": {"1.0.0": {}}}),
    )
    assert handler._check_npm("@scope", "foo", "1.0.0").status == handler.STATUS_CLEAN


def test_npm_unpublished_404(handler, mocker):
    mocker.patch.object(handler.urllib.request, "urlopen", side_effect=_http_error(404))
    assert handler._check_npm(None, "foo", "1.0.0").status == handler.STATUS_UNPUBLISHED


def test_npm_transient_error(handler, mocker):
    mocker.patch.object(handler.urllib.request, "urlopen", side_effect=_http_error(500))
    with pytest.raises(handler._TransientHTTPError):
        handler._check_npm(None, "foo", "1.0.0")


def test_osv_malware_via_id_prefix(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(
            200, {"vulns": [{"id": "MAL-2025-1", "summary": "credential exfiltration"}]}
        ),
    )
    v = handler.PackageVersion(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="evil",
        version="1.0.0",
        revision=None,
    )
    r = handler._check_osv(v)
    assert r.status == handler.STATUS_MALICIOUS
    assert "MAL-2025-1" in r.reason


def test_osv_malware_via_type_field(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(
            200, {"vulns": [{"id": "GHSA-x", "type": "malware", "summary": "x"}]}
        ),
    )
    v = handler.PackageVersion(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="evil",
        version="1.0.0",
        revision=None,
    )
    assert handler._check_osv(v).status == handler.STATUS_MALICIOUS


def test_osv_malware_via_database_specific(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(
            200,
            {"vulns": [{"id": "GHSA-y", "database_specific": {"malicious_package": "true"}}]},
        ),
    )
    v = handler.PackageVersion(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="evil",
        version="1.0.0",
        revision=None,
    )
    assert handler._check_osv(v).status == handler.STATUS_MALICIOUS


def test_osv_withdrawn_advisories_ignored(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(
            200, {"vulns": [{"id": "MAL-1", "withdrawn": "2026-01-01T00:00:00Z"}]}
        ),
    )
    v = handler.PackageVersion(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="x",
        version="1.0.0",
        revision=None,
    )
    r = handler._check_osv(v)
    assert r.status == handler.STATUS_CLEAN
    assert "withdrawn" in r.reason


def test_osv_non_malware_vuln_treated_as_clean(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(200, {"vulns": [{"id": "GHSA-z"}]}),
    )
    v = handler.PackageVersion(
        repo="test-staging-pypi",
        format="pypi",
        namespace=None,
        name="x",
        version="1.0.0",
        revision=None,
    )
    r = handler._check_osv(v)
    assert r.status == handler.STATUS_CLEAN


def test_osv_no_vulns(handler, mocker):
    mocker.patch.object(handler.urllib.request, "urlopen", return_value=_http_resp(200, {}))
    v = handler.PackageVersion(
        repo="test-staging-pypi",
        format="pypi",
        namespace=None,
        name="x",
        version="1.0.0",
        revision=None,
    )
    assert handler._check_osv(v).status == handler.STATUS_CLEAN


def test_osv_transient_error(handler, mocker):
    mocker.patch.object(handler.urllib.request, "urlopen", side_effect=_http_error(503))
    v = handler.PackageVersion(
        repo="test-staging-pypi",
        format="pypi",
        namespace=None,
        name="x",
        version="1.0.0",
        revision=None,
    )
    with pytest.raises(handler._TransientHTTPError):
        handler._check_osv(v)


def test_http_request_refuses_non_https(handler):
    with pytest.raises(ValueError, match="non-HTTPS"):
        handler._http_get_json("http://example.com")


# ---------------------------------------------------------------------------
# check_one combines sources
# ---------------------------------------------------------------------------


def test_check_one_unsupported_ecosystem(handler):
    v = handler.PackageVersion(
        repo="r", format="maven", namespace=None, name="x", version="1", revision=None
    )
    r = handler._check_one(v, ("upstream", "osv"))
    assert r.status == handler.STATUS_CLEAN


def test_check_one_severity_ordering(handler, mocker):
    """Malicious from OSV beats clean from upstream."""
    mocker.patch.object(
        handler,
        "_check_upstream",
        return_value=handler.CheckResult(status="clean", reason="", source="upstream"),
    )
    mocker.patch.object(
        handler,
        "_check_osv",
        return_value=handler.CheckResult(status="malicious", reason="MAL-x", source="osv"),
    )
    v = handler.PackageVersion(
        repo="r", format="npm", namespace=None, name="x", version="1", revision=None
    )
    assert handler._check_one(v, ("upstream", "osv")).status == handler.STATUS_MALICIOUS


def test_check_one_transient_recorded_as_unknown(handler, mocker):
    mocker.patch.object(handler, "_check_upstream", side_effect=handler._TransientHTTPError("boom"))
    mocker.patch.object(
        handler,
        "_check_osv",
        return_value=handler.CheckResult(status="clean", reason="", source="osv"),
    )
    v = handler.PackageVersion(
        repo="r", format="npm", namespace=None, name="x", version="1", revision=None
    )
    # Unknown wins over clean (severity_order: unknown=1, clean=0).
    r = handler._check_one(v, ("upstream", "osv"))
    assert r.status == handler.STATUS_UNKNOWN


def test_check_one_osv_transient_recorded(handler, mocker):
    mocker.patch.object(
        handler,
        "_check_upstream",
        return_value=handler.CheckResult(status="clean", reason="", source="upstream"),
    )
    mocker.patch.object(handler, "_check_osv", side_effect=handler._TransientHTTPError("boom"))
    v = handler.PackageVersion(
        repo="r", format="pypi", namespace=None, name="x", version="1", revision=None
    )
    assert handler._check_one(v, ("upstream", "osv")).status == handler.STATUS_UNKNOWN


def test_check_upstream_dispatches(handler, mocker):
    mocker.patch.object(
        handler,
        "_check_pypi",
        return_value=handler.CheckResult(status="clean", reason="", source="upstream"),
    )
    mocker.patch.object(
        handler,
        "_check_npm",
        return_value=handler.CheckResult(status="yanked", reason="dep", source="upstream"),
    )
    v = handler.PackageVersion(
        repo="r", format="npm", namespace=None, name="x", version="1", revision=None
    )
    assert handler._check_upstream(v).status == handler.STATUS_YANKED

    v2 = handler.PackageVersion(
        repo="r", format="pypi", namespace=None, name="x", version="1", revision=None
    )
    assert handler._check_upstream(v2).status == handler.STATUS_CLEAN


# ---------------------------------------------------------------------------
# State table
# ---------------------------------------------------------------------------


def test_needs_check_missing_item(handler, tables):
    v = handler.PackageVersion(
        repo="r", format="npm", namespace=None, name="x", version="1", revision=None
    )
    assert handler._needs_check(tables["state"], v, 3000) is True


def test_needs_check_no_timestamp(handler, tables):
    v = handler.PackageVersion(
        repo="r", format="npm", namespace=None, name="x", version="1", revision=None
    )
    tables["state"].items[
        tuple(sorted({"package_arn": v.package_arn, "version": v.version}.items()))
    ] = {"package_arn": v.package_arn, "version": v.version}
    assert handler._needs_check(tables["state"], v, 3000) is True


def test_needs_check_bad_iso(handler, tables):
    v = handler.PackageVersion(
        repo="r", format="npm", namespace=None, name="x", version="1", revision=None
    )
    tables["state"].items[
        tuple(sorted({"package_arn": v.package_arn, "version": v.version}.items()))
    ] = {"last_checked": "not-an-iso"}
    assert handler._needs_check(tables["state"], v, 3000) is True


def test_needs_check_recent(handler, tables, mocker):
    import datetime as dt

    mocker.patch.object(
        handler.dt,
        "datetime",
        wraps=dt.datetime,
    )
    v = handler.PackageVersion(
        repo="r", format="npm", namespace=None, name="x", version="1", revision=None
    )
    tables["state"].items[
        tuple(sorted({"package_arn": v.package_arn, "version": v.version}.items()))
    ] = {"last_checked": dt.datetime.now(dt.UTC).isoformat()}
    assert handler._needs_check(tables["state"], v, 3000) is False


def test_load_prior_status_missing(handler, tables):
    v = handler.PackageVersion(
        repo="r", format="npm", namespace=None, name="x", version="1", revision=None
    )
    assert handler._load_prior_status(tables["state"], v) == handler.STATUS_CLEAN


def test_load_prior_status_present(handler, tables):
    v = handler.PackageVersion(
        repo="r", format="npm", namespace=None, name="x", version="1", revision=None
    )
    tables["state"].items[
        tuple(sorted({"package_arn": v.package_arn, "version": v.version}.items()))
    ] = {"status": "yanked"}
    assert handler._load_prior_status(tables["state"], v) == "yanked"


def test_record_state_writes_row(handler, tables):
    v = handler.PackageVersion(
        repo="r", format="npm", namespace=None, name="lodash", version="1", revision=None
    )
    handler._record_state(
        tables["state"],
        v,
        handler.CheckResult(status="yanked", reason="dep", source="upstream"),
    )
    assert tables["state"].put_calls[0]["status"] == "yanked"
    assert tables["state"].put_calls[0]["repository"] == "r"


def test_is_new_finding(handler):
    assert handler._is_new_finding(
        "clean", handler.CheckResult(status="yanked", reason="", source="")
    )
    # repeat of same status → not new
    assert not handler._is_new_finding(
        "yanked", handler.CheckResult(status="yanked", reason="", source="")
    )
    # clean / unknown never trigger
    assert not handler._is_new_finding(
        "clean", handler.CheckResult(status="clean", reason="", source="")
    )
    assert not handler._is_new_finding(
        "yanked", handler.CheckResult(status="unknown", reason="", source="")
    )


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@pytest.fixture
def pkg(handler):
    return handler.PackageVersion(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="lodash",
        version="1.2.3",
        revision="abc",
    )


def test_apply_action_unlist(handler, pkg):
    stubber = Stubber(handler._ca)
    stubber.add_response(
        "update_package_versions_status",
        {"successfulVersions": {"1.2.3": {"status": "Unlisted", "revision": "abc"}}},
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "repository": "test-prod",
            "format": "npm",
            "package": "lodash",
            "versions": ["1.2.3"],
            "targetStatus": "Unlisted",
        },
    )
    with stubber:
        handler._apply_action_to_repo(pkg, "test-prod", handler.ACTION_UNLIST)
    stubber.assert_no_pending_responses()


def test_apply_action_dispose(handler, pkg):
    stubber = Stubber(handler._ca)
    stubber.add_response(
        "dispose_package_versions",
        {"successfulVersions": {"1.2.3": {"status": "Disposed", "revision": "abc"}}},
    )
    with stubber:
        handler._apply_action_to_repo(pkg, "test-staging-npm", handler.ACTION_DISPOSE)
    stubber.assert_no_pending_responses()


def test_apply_action_delete(handler, pkg):
    stubber = Stubber(handler._ca)
    stubber.add_response(
        "delete_package_versions",
        {"successfulVersions": {"1.2.3": {"status": "Deleted", "revision": "abc"}}},
    )
    with stubber:
        handler._apply_action_to_repo(pkg, "test-staging-npm", handler.ACTION_DELETE)
    stubber.assert_no_pending_responses()


def test_apply_action_unknown(handler, pkg):
    with pytest.raises(ValueError, match="unknown action"):
        handler._apply_action_to_repo(pkg, "test-staging-npm", "bogus")


def test_apply_action_with_namespace(handler):
    v = handler.PackageVersion(
        repo="test-staging-npm",
        format="npm",
        namespace="@scope",
        name="pkg",
        version="1",
        revision=None,
    )
    stubber = Stubber(handler._ca)
    stubber.add_response(
        "dispose_package_versions",
        {"successfulVersions": {"1": {"status": "Disposed"}}},
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "repository": "test-staging-npm",
            "format": "npm",
            "package": "pkg",
            "versions": ["1"],
            "namespace": "@scope",
        },
    )
    with stubber:
        handler._apply_action_to_repo(v, "test-staging-npm", handler.ACTION_DISPOSE)


def test_act_alert_only_skips_codeartifact_calls(handler, tables, mocker, pkg):
    sns_pub = mocker.patch.object(handler._sns, "publish", return_value={"MessageId": "m"})
    apply = mocker.patch.object(handler, "_apply_action_to_repo")
    handler._act(
        pkg,
        handler.CheckResult(status="yanked", reason="dep", source="upstream"),
        handler.ACTION_ALERT,
    )
    assert sns_pub.called
    assert not apply.called
    assert tables["audit"].put_calls


def test_act_dispose_applies_to_both_repos(handler, tables, mocker, pkg):
    mocker.patch.object(handler._sns, "publish", return_value={"MessageId": "m"})
    calls: list[str] = []
    mocker.patch.object(
        handler, "_apply_action_to_repo", side_effect=lambda v, repo, action: calls.append(repo)
    )
    handler._act(
        pkg,
        handler.CheckResult(status="unpublished", reason="404", source="upstream"),
        handler.ACTION_DISPOSE,
    )
    assert calls == ["test-staging-npm", "test-prod"]


def test_act_skips_missing_resource(handler, tables, mocker, pkg):
    """If the version doesn't exist in prod (never promoted), CodeArtifact
    raises ResourceNotFoundException — should be swallowed."""
    mocker.patch.object(handler._sns, "publish", return_value={"MessageId": "m"})

    def side(v, repo, action):
        if repo == "test-prod":
            raise handler._ca.exceptions.ResourceNotFoundException(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
                "DisposePackageVersions",
            )

    mocker.patch.object(handler, "_apply_action_to_repo", side_effect=side)
    # Should not raise.
    handler._act(
        pkg,
        handler.CheckResult(status="unpublished", reason="404", source="upstream"),
        handler.ACTION_DISPOSE,
    )


def test_publish_sns_no_topic(handler, mocker, pkg, monkeypatch):
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    pub = mocker.patch.object(handler._sns, "publish")
    handler._publish_sns(
        pkg,
        handler.CheckResult(status="yanked", reason="", source="upstream"),
        handler.ACTION_ALERT,
    )
    assert not pub.called


def test_publish_sns_payload(handler, mocker, pkg):
    pub = mocker.patch.object(handler._sns, "publish", return_value={"MessageId": "m"})
    handler._publish_sns(
        pkg,
        handler.CheckResult(status="yanked", reason="dep msg", source="upstream"),
        handler.ACTION_UNLIST,
    )
    body = json.loads(pub.call_args.kwargs["Message"])
    assert body["status"] == "yanked"
    assert body["action"] == "unlist"
    assert body["name"] == "lodash"


def test_write_audit_shape(handler, tables, pkg):
    handler._write_audit(
        pkg,
        handler.CheckResult(status="malicious", reason="MAL-1", source="osv"),
        handler.ACTION_DISPOSE,
    )
    row = tables["audit"].put_calls[0]
    assert row["status"] == "malicious"
    assert row["action"] == "dispose"
    assert row["version_ts"].startswith("1.2.3#")


# ---------------------------------------------------------------------------
# Enumeration + top-level handler
# ---------------------------------------------------------------------------


def _stub_list_pages(stubber, repo: str, packages: list[dict[str, Any]]):
    stubber.add_response(
        "list_packages",
        {"packages": packages},
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "repository": repo,
        },
    )


def _stub_list_versions(stubber, repo: str, pkg: dict[str, Any], versions: list[dict[str, Any]]):
    params: dict[str, Any] = {
        "domain": "test-domain",
        "domainOwner": "111111111111",
        "repository": repo,
        "format": pkg["format"],
        "package": pkg["package"],
        "status": "Published",
    }
    if pkg.get("namespace"):
        params["namespace"] = pkg["namespace"]
    stubber.add_response("list_package_versions", {"versions": versions}, expected_params=params)


def test_lambda_handler_flags_yanked_and_acts(handler, tables, mocker):
    stubber = Stubber(handler._ca)
    _stub_list_pages(
        stubber,
        "test-staging-npm",
        [{"format": "npm", "package": "lodash"}],
    )
    _stub_list_versions(
        stubber,
        "test-staging-npm",
        {"format": "npm", "package": "lodash"},
        [{"version": "1.0.0", "revision": "abc", "status": "Published"}],
    )
    _stub_list_pages(stubber, "test-staging-pypi", [])

    # Stub the npm packument check to surface a deprecation (=yanked).
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(200, {"versions": {"1.0.0": {"deprecated": "use foo"}}}),
    )

    # OSV: include the call; clean
    osv_check = mocker.patch.object(
        handler,
        "_check_osv",
        return_value=handler.CheckResult(status="clean", reason="", source="osv"),
    )

    # Action dispatch — capture only, don't hit CodeArtifact again.
    act = mocker.patch.object(handler, "_act")

    with stubber:
        result = handler.lambda_handler({}, None)

    assert result == {"checked": 1, "actions": 1}
    assert act.called
    assert osv_check.called


def test_lambda_handler_no_versions_due(handler, tables, mocker, monkeypatch):
    """If state table has a recent entry, version is skipped."""
    import datetime as dt

    monkeypatch.setenv("YANK_RECHECK_SECONDS", "3000")
    # Re-import to pick up new env value.
    handler = handler

    stubber = Stubber(handler._ca)
    _stub_list_pages(stubber, "test-staging-npm", [{"format": "npm", "package": "lodash"}])
    _stub_list_versions(
        stubber,
        "test-staging-npm",
        {"format": "npm", "package": "lodash"},
        [{"version": "1.0.0", "revision": "abc", "status": "Published"}],
    )
    _stub_list_pages(stubber, "test-staging-pypi", [])

    v = handler.PackageVersion(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="lodash",
        version="1.0.0",
        revision="abc",
    )
    tables["state"].items[
        tuple(sorted({"package_arn": v.package_arn, "version": "1.0.0"}.items()))
    ] = {"last_checked": dt.datetime.now(dt.UTC).isoformat()}

    with stubber:
        result = handler.lambda_handler({}, None)
    assert result == {"checked": 0, "actions": 0}


def test_lambda_handler_check_exception_is_logged(handler, tables, mocker):
    stubber = Stubber(handler._ca)
    _stub_list_pages(stubber, "test-staging-npm", [{"format": "npm", "package": "lodash"}])
    _stub_list_versions(
        stubber,
        "test-staging-npm",
        {"format": "npm", "package": "lodash"},
        [{"version": "1.0.0", "revision": "abc", "status": "Published"}],
    )
    _stub_list_pages(stubber, "test-staging-pypi", [])

    mocker.patch.object(handler, "_check_one", side_effect=RuntimeError("boom"))
    act = mocker.patch.object(handler, "_act")

    with stubber:
        result = handler.lambda_handler({}, None)
    assert result == {"checked": 1, "actions": 0}
    assert not act.called


def test_list_packages_namespaced(handler):
    stubber = Stubber(handler._ca)
    _stub_list_pages(
        stubber,
        "test-staging-npm",
        [{"format": "npm", "package": "node", "namespace": "types"}],
    )
    _stub_list_versions(
        stubber,
        "test-staging-npm",
        {"format": "npm", "package": "node", "namespace": "types"},
        [{"version": "1.0.0", "status": "Published"}],
    )
    with stubber:
        versions = list(handler._enumerate_versions(("test-staging-npm",)))
    assert versions[0].namespace == "types"
    assert versions[0].name == "node"
