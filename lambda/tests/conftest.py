"""Shared pytest fixtures.

Sets the env vars every handler's module-level `config = from_env()` requires,
so importing a handler at test collection time doesn't error.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _lambda_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DOMAIN_NAME", "test-domain")
    monkeypatch.setenv("DOMAIN_OWNER", "111111111111")
    monkeypatch.setenv("SOURCE_REPO_NAMES", "test-staging-npm,test-staging-pypi")
    monkeypatch.setenv("TARGET_REPO_NAME", "test-prod")
    monkeypatch.setenv("AUDIT_TABLE_NAME", "test-audit")
    monkeypatch.setenv(
        "STATE_MACHINE_ARN",
        "arn:aws:states:us-east-1:111111111111:stateMachine:test-quarantine",
    )
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("BLOCK_ON_SEVERITY", "HIGH,CRITICAL")
    monkeypatch.setenv("SCANNER_TYPE", "inspector")
    monkeypatch.setenv("SCANNER_LAMBDA_ARN", "")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("APPROVAL_REQUIRED_WHEN", "findings")
    monkeypatch.setenv("APPROVAL_TIMEOUT", "P14D")
    monkeypatch.setenv("YANK_STATE_TABLE", "test-yank-state")
    monkeypatch.setenv("YANK_AUDIT_TABLE", "test-yank-audit")
    monkeypatch.setenv("YANK_SOURCES", "upstream,osv")
    monkeypatch.setenv("YANK_RESPONSE_YANKED", "unlist")
    monkeypatch.setenv("YANK_RESPONSE_UNPUBLISHED", "dispose")
    monkeypatch.setenv("YANK_RESPONSE_MALICIOUS", "dispose")
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:111111111111:test-notify")
    monkeypatch.setenv("YANK_RECHECK_SECONDS", "0")
    monkeypatch.setenv("YANK_MAX_WORKERS", "1")
    monkeypatch.setenv("PROACTIVE_FILL_ALLOWLIST_JSON", "[]")
    monkeypatch.setenv("PROACTIVE_FILL_INCLUDE_PRERELEASES", "false")
    monkeypatch.setenv("PROACTIVE_FILL_MAX_FETCHES", "200")
    monkeypatch.setenv(
        "PROACTIVE_FILL_REPOS_BY_FORMAT",
        '{"npm": "test-staging-npm", "pypi": "test-staging-pypi"}',
    )
    monkeypatch.setenv("PROACTIVE_FILL_HTTP_TIMEOUT", "5")
    yield


@pytest.fixture
def fresh_module():
    """Return a function that re-imports a module under test.

    Handlers do module-level work (config parsing, boto3 client creation), so
    each test that mutates env or expects a clean client needs a freshly-
    imported handler. Usage:

        def test_x(fresh_module):
            h = fresh_module("promote.handler")
            ...
    """
    import importlib
    import sys

    def _reload(name: str):
        if name in sys.modules:
            return importlib.reload(sys.modules[name])
        return importlib.import_module(name)

    return _reload
