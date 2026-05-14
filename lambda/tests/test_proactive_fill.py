"""Tests for the proactive_fill Lambda."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from botocore.stub import Stubber


@pytest.fixture
def handler(fresh_module):
    return fresh_module("proactive_fill.handler")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _http_resp(body: dict[str, Any] | bytes):
    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    if isinstance(body, bytes):
        return _R(body)
    return _R(json.dumps(body).encode())


# ---------------------------------------------------------------------------
# Pre-release regex
# ---------------------------------------------------------------------------


def test_npm_prerelease_regex(handler):
    assert handler._NPM_PRERELEASE.match("1.0.0-alpha.1")
    assert handler._NPM_PRERELEASE.match("2.3.4-rc.1")
    assert not handler._NPM_PRERELEASE.match("1.0.0")
    assert not handler._NPM_PRERELEASE.match("0.0.1")


def test_pypi_prerelease_regex(handler):
    assert handler._PYPI_PRERELEASE.search("1.0.0a1")
    assert handler._PYPI_PRERELEASE.search("1.0.0b2")
    assert handler._PYPI_PRERELEASE.search("1.0.0rc1")
    assert handler._PYPI_PRERELEASE.search("1.0.0.dev1")
    assert handler._PYPI_PRERELEASE.search("2.0.0.alpha1")
    assert not handler._PYPI_PRERELEASE.search("1.0.0")
    assert not handler._PYPI_PRERELEASE.search("2.3.4")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def test_split_scoped_npm(handler):
    assert handler._split_scoped_npm("@scope/foo") == ("@scope", "foo")
    assert handler._split_scoped_npm("foo") == (None, "foo")
    # Edge: starts with @ but no slash → treat as unscoped name.
    assert handler._split_scoped_npm("@weird") == (None, "@weird")


def test_pick_pypi_primary_prefers_sdist(handler):
    files = [
        {"filename": "foo-1.0.0-py3-none-any.whl"},
        {"filename": "foo-1.0.0.tar.gz"},
    ]
    assert handler._pick_pypi_primary(files) == "foo-1.0.0.tar.gz"


def test_pick_pypi_primary_falls_back_to_wheel(handler):
    files = [{"filename": "foo-1.0.0-py3-none-any.whl"}]
    assert handler._pick_pypi_primary(files) == "foo-1.0.0-py3-none-any.whl"


def test_pick_pypi_primary_none_for_unknown(handler):
    assert handler._pick_pypi_primary([{"filename": "foo-1.0.0.zip"}]) is None


def test_pypi_normalize(handler):
    assert handler._pypi_normalize("Foo.Bar") == "foo-bar"
    assert handler._pypi_normalize("foo__bar") == "foo-bar"
    assert handler._pypi_normalize("foo.bar_baz") == "foo-bar-baz"


def test_http_get_json_refuses_non_https(handler):
    with pytest.raises(ValueError, match="non-HTTPS"):
        handler._http_get_json("http://pypi.org/whatever")


# ---------------------------------------------------------------------------
# Upstream candidates
# ---------------------------------------------------------------------------


def test_npm_candidates_parses_packument(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(
            {
                "versions": {
                    "1.0.0": {"dist": {"tarball": "https://reg/lodash/-/lodash-1.0.0.tgz"}},
                    "2.0.0-rc.1": {
                        "dist": {"tarball": "https://reg/lodash/-/lodash-2.0.0-rc.1.tgz"}
                    },
                }
            }
        ),
    )
    target = handler.Target(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="lodash",
        include_prereleases=False,
    )
    plans = handler._npm_candidates(target)
    by_version = {p.version: p for p in plans}
    assert by_version["1.0.0"].asset_path == "lodash/-/lodash-1.0.0.tgz"
    assert not by_version["1.0.0"].is_prerelease
    assert by_version["2.0.0-rc.1"].is_prerelease


def test_npm_candidates_scoped(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(
            {
                "versions": {
                    "1.0.0": {"dist": {"tarball": "https://reg/@scope/pkg/-/pkg-1.0.0.tgz"}},
                }
            }
        ),
    )
    target = handler.Target(
        repo="test-staging-npm",
        format="npm",
        namespace="@scope",
        name="pkg",
        include_prereleases=False,
    )
    plans = handler._npm_candidates(target)
    assert plans[0].asset_path == "@scope/pkg/-/pkg-1.0.0.tgz"


def test_pypi_candidates_picks_sdist(handler, mocker):
    mocker.patch.object(
        handler.urllib.request,
        "urlopen",
        return_value=_http_resp(
            {
                "releases": {
                    "1.0.0": [
                        {"filename": "Foo.Bar-1.0.0-py3-none-any.whl"},
                        {"filename": "Foo.Bar-1.0.0.tar.gz"},
                    ],
                    "2.0.0a1": [{"filename": "Foo.Bar-2.0.0a1.tar.gz"}],
                    "0.9.0": [],  # empty: skipped
                    "0.9.1": [{"filename": "unknown-extension.zip"}],  # no sdist or wheel
                }
            }
        ),
    )
    target = handler.Target(
        repo="test-staging-pypi",
        format="pypi",
        namespace=None,
        name="Foo.Bar",
        include_prereleases=False,
    )
    plans = handler._pypi_candidates(target)
    by_version = {p.version: p for p in plans}
    assert "0.9.0" not in by_version
    assert "0.9.1" not in by_version
    assert by_version["1.0.0"].asset_path == "simple/foo-bar/Foo.Bar-1.0.0.tar.gz"
    assert by_version["2.0.0a1"].is_prerelease


# ---------------------------------------------------------------------------
# Plan fetches diffing
# ---------------------------------------------------------------------------


def test_plan_fetches_skips_cached_and_prereleases(handler, mocker):
    target = handler.Target(
        repo="test-staging-pypi",
        format="pypi",
        namespace=None,
        name="foo",
        include_prereleases=False,
    )
    mocker.patch.object(
        handler,
        "_list_cached_versions",
        return_value={"1.0.0"},
    )
    mocker.patch.object(
        handler,
        "_pypi_candidates",
        return_value=[
            handler.FetchPlan(version="1.0.0", asset_path="x", is_prerelease=False),
            handler.FetchPlan(version="2.0.0", asset_path="x", is_prerelease=False),
            handler.FetchPlan(version="3.0.0a1", asset_path="x", is_prerelease=True),
        ],
    )
    plans = list(handler._plan_fetches(target))
    assert [p.version for p in plans] == ["2.0.0"]


def test_plan_fetches_npm_dispatch(handler, mocker):
    """Verifies the npm branch is reached (line coverage)."""
    target = handler.Target(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="lodash",
        include_prereleases=True,
    )
    mocker.patch.object(handler, "_list_cached_versions", return_value=set())
    npm_called = mocker.patch.object(
        handler,
        "_npm_candidates",
        return_value=[handler.FetchPlan(version="1.0.0", asset_path="x", is_prerelease=False)],
    )
    plans = list(handler._plan_fetches(target))
    assert npm_called.called
    assert plans[0].version == "1.0.0"


def test_plan_fetches_includes_prereleases_when_opted_in(handler, mocker):
    target = handler.Target(
        repo="test-staging-pypi",
        format="pypi",
        namespace=None,
        name="foo",
        include_prereleases=True,
    )
    mocker.patch.object(handler, "_list_cached_versions", return_value=set())
    mocker.patch.object(
        handler,
        "_pypi_candidates",
        return_value=[
            handler.FetchPlan(version="3.0.0a1", asset_path="x", is_prerelease=True),
        ],
    )
    plans = list(handler._plan_fetches(target))
    assert plans[0].version == "3.0.0a1"


def test_list_cached_versions_returns_empty_when_package_unknown(handler):
    target = handler.Target(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="never-fetched",
        include_prereleases=False,
    )
    stubber = Stubber(handler._ca)
    stubber.add_client_error(
        "list_package_versions",
        service_error_code="ResourceNotFoundException",
        service_message="x",
    )
    with stubber:
        assert handler._list_cached_versions(target) == set()


def test_list_cached_versions_with_namespace(handler):
    target = handler.Target(
        repo="test-staging-npm",
        format="npm",
        namespace="@scope",
        name="pkg",
        include_prereleases=False,
    )
    stubber = Stubber(handler._ca)
    stubber.add_response(
        "list_package_versions",
        {"versions": [{"version": "1.0.0", "status": "Published"}]},
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "repository": "test-staging-npm",
            "format": "npm",
            "package": "pkg",
            "namespace": "@scope",
        },
    )
    with stubber:
        assert handler._list_cached_versions(target) == {"1.0.0"}


# ---------------------------------------------------------------------------
# Watch set construction
# ---------------------------------------------------------------------------


def test_build_watch_set_follow_mode(handler):
    stubber = Stubber(handler._ca)
    stubber.add_response(
        "list_packages",
        {
            "packages": [
                {"format": "npm", "package": "lodash"},
                {"format": "maven", "package": "ignored"},  # filtered (not npm/pypi)
            ]
        },
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "repository": "test-staging-npm",
        },
    )
    stubber.add_response(
        "list_packages",
        {"packages": [{"format": "pypi", "package": "fastapi"}]},
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "repository": "test-staging-pypi",
        },
    )
    with stubber:
        targets = list(
            handler._build_watch_set(
                {
                    "allowlist": [],
                    "include_prereleases": False,
                    "max_fetches": 200,
                    "repos_by_format": {"npm": "test-staging-npm", "pypi": "test-staging-pypi"},
                }
            )
        )
    names = {t.name for t in targets}
    assert names == {"lodash", "fastapi"}


def test_build_watch_set_allowlist_resolves_repo(handler):
    stubber = Stubber(handler._ca)
    # Empty follow-mode discovery.
    stubber.add_response(
        "list_packages", {"packages": []}, expected_params=mock_params_for_repo("test-staging-npm")
    )
    stubber.add_response(
        "list_packages", {"packages": []}, expected_params=mock_params_for_repo("test-staging-pypi")
    )
    with stubber:
        targets = list(
            handler._build_watch_set(
                {
                    "allowlist": [
                        {"format": "npm", "name": "@scope/critical"},
                        {"format": "pypi", "name": "fastapi", "include_prereleases": True},
                        # Allowlist entry whose format has no staging repo configured — skipped.
                        {"format": "maven", "name": "ignored"},
                    ],
                    "include_prereleases": False,
                    "max_fetches": 200,
                    "repos_by_format": {"npm": "test-staging-npm", "pypi": "test-staging-pypi"},
                }
            )
        )
    by_name = {t.name: t for t in targets}
    assert by_name["critical"].namespace == "@scope"
    assert by_name["fastapi"].include_prereleases is True


def mock_params_for_repo(repo: str) -> dict:
    return {
        "domain": "test-domain",
        "domainOwner": "111111111111",
        "repository": repo,
    }


def test_build_watch_set_dedupes(handler):
    """A package that's both in staging AND on the allowlist should appear once."""
    stubber = Stubber(handler._ca)
    stubber.add_response(
        "list_packages",
        {"packages": [{"format": "npm", "package": "lodash"}]},
        expected_params=mock_params_for_repo("test-staging-npm"),
    )
    stubber.add_response(
        "list_packages",
        {"packages": []},
        expected_params=mock_params_for_repo("test-staging-pypi"),
    )
    with stubber:
        targets = list(
            handler._build_watch_set(
                {
                    "allowlist": [{"format": "npm", "name": "lodash"}],
                    "include_prereleases": False,
                    "max_fetches": 200,
                    "repos_by_format": {"npm": "test-staging-npm", "pypi": "test-staging-pypi"},
                }
            )
        )
    assert len(targets) == 1


def test_list_packages_yields_format_namespace_name(handler):
    stubber = Stubber(handler._ca)
    stubber.add_response(
        "list_packages",
        {
            "packages": [
                {"format": "npm", "package": "node", "namespace": "types"},
            ]
        },
        expected_params=mock_params_for_repo("test-staging-npm"),
    )
    with stubber:
        out = list(handler._list_packages("test-staging-npm"))
    assert out == [("npm", "types", "node")]


# ---------------------------------------------------------------------------
# Endpoints + auth
# ---------------------------------------------------------------------------


def test_get_auth_token(handler):
    stubber = Stubber(handler._ca)
    stubber.add_response(
        "get_authorization_token",
        {
            "authorizationToken": "tok-1",
            "expiration": __import__("datetime").datetime.now(__import__("datetime").UTC),
        },
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "durationSeconds": 43200,
        },
    )
    with stubber:
        assert handler._get_auth_token() == "tok-1"


def test_resolve_endpoints(handler):
    stubber = Stubber(handler._ca)
    stubber.add_response(
        "describe_repository",
        {
            "repository": {
                "name": "test-staging-npm",
                "externalConnections": [
                    {"externalConnectionName": "public:npmjs", "packageFormat": "npm"}
                ],
            }
        },
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "repository": "test-staging-npm",
        },
    )
    stubber.add_response(
        "get_repository_endpoint",
        {"repositoryEndpoint": "https://endpoint/npm/staging/"},
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "repository": "test-staging-npm",
            "format": "npm",
        },
    )
    stubber.add_response(
        "describe_repository",
        {
            "repository": {
                "name": "test-staging-pypi",
                "externalConnections": [
                    {"externalConnectionName": "public:pypi", "packageFormat": "pypi"}
                ],
            }
        },
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "repository": "test-staging-pypi",
        },
    )
    stubber.add_response(
        "get_repository_endpoint",
        {"repositoryEndpoint": "https://endpoint/pypi/staging/"},
        expected_params={
            "domain": "test-domain",
            "domainOwner": "111111111111",
            "repository": "test-staging-pypi",
            "format": "pypi",
        },
    )
    with stubber:
        endpoints = handler._resolve_endpoints(("test-staging-npm", "test-staging-pypi"))
    # Trailing slash stripped.
    assert endpoints["test-staging-npm"] == "https://endpoint/npm/staging"
    assert endpoints["test-staging-pypi"] == "https://endpoint/pypi/staging"


def test_resolve_endpoints_skips_unknown_format(handler):
    """A repo with no recognised external_connection just gets no endpoint entry."""
    stubber = Stubber(handler._ca)
    stubber.add_response(
        "describe_repository",
        {
            "repository": {
                "name": "test-staging-maven",
                "externalConnections": [
                    {"externalConnectionName": "public:maven-central", "packageFormat": "maven"}
                ],
            }
        },
        expected_params=mock_params_for_repo("test-staging-maven"),
    )
    with stubber:
        endpoints = handler._resolve_endpoints(("test-staging-maven",))
    assert endpoints == {}


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def test_fetch_to_staging_npm_uses_bearer(handler, mocker):
    captured: dict[str, Any] = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return _http_resp(b"tarball-bytes")

    mocker.patch.object(handler.urllib.request, "urlopen", side_effect=fake_urlopen)
    target = handler.Target(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="lodash",
        include_prereleases=False,
    )
    plan = handler.FetchPlan(
        version="1.0.0", asset_path="lodash/-/lodash-1.0.0.tgz", is_prerelease=False
    )
    handler._fetch_to_staging(target, plan, "tok", "https://endpoint/npm/staging")
    assert captured["url"] == "https://endpoint/npm/staging/lodash/-/lodash-1.0.0.tgz"
    assert captured["auth"] == "Bearer tok"


def test_fetch_to_staging_pypi_uses_basic(handler, mocker):
    captured: dict[str, Any] = {}

    def fake_urlopen(req, timeout=None):
        captured["auth"] = req.get_header("Authorization")
        return _http_resp(b"wheel-bytes")

    mocker.patch.object(handler.urllib.request, "urlopen", side_effect=fake_urlopen)
    target = handler.Target(
        repo="test-staging-pypi",
        format="pypi",
        namespace=None,
        name="fastapi",
        include_prereleases=False,
    )
    plan = handler.FetchPlan(
        version="1.0.0", asset_path="simple/fastapi/fastapi-1.0.0.tar.gz", is_prerelease=False
    )
    handler._fetch_to_staging(target, plan, "tok", "https://endpoint/pypi/staging")
    # Basic base64("aws:tok") = "YXdzOnRvaw=="
    assert captured["auth"].startswith("Basic ")


def test_fetch_to_staging_rejects_non_https(handler):
    target = handler.Target(
        repo="r", format="npm", namespace=None, name="x", include_prereleases=False
    )
    plan = handler.FetchPlan(version="1.0.0", asset_path="x", is_prerelease=False)
    with pytest.raises(ValueError, match="non-HTTPS"):
        handler._fetch_to_staging(target, plan, "tok", "http://endpoint")


def test_record_fetch_writes_audit_row(handler, mocker):
    captured: dict[str, Any] = {}

    def fake_put_item(**kwargs):
        captured.update(kwargs)
        return {}

    mocker.patch.object(
        handler.audit._dynamodb,
        "Table",
        side_effect=lambda name: type("FakeTable", (), {"put_item": staticmethod(fake_put_item)})(),
    )
    target = handler.Target(
        repo="test-staging-npm",
        format="npm",
        namespace="@scope",
        name="pkg",
        include_prereleases=False,
    )
    plan = handler.FetchPlan(version="1.2.3", asset_path="x", is_prerelease=False)
    handler._record_fetch(target, plan)
    item = captured["Item"]
    assert item["decision"] == "fetched"
    assert item["record_type"] == "proactive_fill"
    assert item["repository"] == "test-staging-npm"
    assert item["namespace"] == "@scope"


# ---------------------------------------------------------------------------
# Top-level handler
# ---------------------------------------------------------------------------


def test_lambda_handler_empty_watch_set(handler, mocker):
    mocker.patch.object(handler, "_build_watch_set", return_value=iter([]))
    assert handler.lambda_handler({}, None) == {"watched": 0, "fetched": 0}


def test_lambda_handler_happy_path(handler, mocker):
    target = handler.Target(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="lodash",
        include_prereleases=False,
    )
    plan = handler.FetchPlan(version="1.0.0", asset_path="x", is_prerelease=False)

    mocker.patch.object(handler, "_build_watch_set", return_value=iter([target]))
    mocker.patch.object(handler, "_get_auth_token", return_value="tok")
    mocker.patch.object(
        handler,
        "_resolve_endpoints",
        return_value={"test-staging-npm": "https://endpoint/npm/staging"},
    )
    mocker.patch.object(handler, "_plan_fetches", return_value=iter([plan]))
    fetch = mocker.patch.object(handler, "_fetch_to_staging")
    record = mocker.patch.object(handler, "_record_fetch")

    out = handler.lambda_handler({}, None)
    assert out == {"watched": 1, "fetched": 1}
    fetch.assert_called_once()
    record.assert_called_once()


def test_lambda_handler_plan_exception(handler, mocker):
    target = handler.Target(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="lodash",
        include_prereleases=False,
    )
    mocker.patch.object(handler, "_build_watch_set", return_value=iter([target]))
    mocker.patch.object(handler, "_get_auth_token", return_value="tok")
    mocker.patch.object(handler, "_resolve_endpoints", return_value={"test-staging-npm": "x"})
    mocker.patch.object(handler, "_plan_fetches", side_effect=RuntimeError("boom"))
    out = handler.lambda_handler({}, None)
    assert out == {"watched": 1, "fetched": 0}


def test_lambda_handler_fetch_exception(handler, mocker):
    target = handler.Target(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="lodash",
        include_prereleases=False,
    )
    plan = handler.FetchPlan(version="1.0.0", asset_path="x", is_prerelease=False)
    mocker.patch.object(handler, "_build_watch_set", return_value=iter([target]))
    mocker.patch.object(handler, "_get_auth_token", return_value="tok")
    mocker.patch.object(handler, "_resolve_endpoints", return_value={"test-staging-npm": "x"})
    mocker.patch.object(handler, "_plan_fetches", return_value=iter([plan]))
    mocker.patch.object(handler, "_fetch_to_staging", side_effect=RuntimeError("net"))
    out = handler.lambda_handler({}, None)
    assert out == {"watched": 1, "fetched": 0}


def test_lambda_handler_max_fetches_cap(handler, mocker, monkeypatch):
    monkeypatch.setenv("PROACTIVE_FILL_MAX_FETCHES", "1")
    handler = __import__("importlib").reload(handler)

    t = handler.Target(
        repo="test-staging-npm",
        format="npm",
        namespace=None,
        name="lodash",
        include_prereleases=False,
    )
    plans = [
        handler.FetchPlan(version=str(i), asset_path="x", is_prerelease=False) for i in range(5)
    ]
    mocker.patch.object(handler, "_build_watch_set", return_value=iter([t, t]))
    mocker.patch.object(handler, "_get_auth_token", return_value="tok")
    mocker.patch.object(handler, "_resolve_endpoints", return_value={"test-staging-npm": "x"})
    mocker.patch.object(handler, "_plan_fetches", return_value=iter(plans))
    mocker.patch.object(handler, "_fetch_to_staging")
    mocker.patch.object(handler, "_record_fetch")
    out = handler.lambda_handler({}, None)
    assert out["fetched"] == 1
