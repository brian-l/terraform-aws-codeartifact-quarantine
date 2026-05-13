"""Tests for common.config."""

from __future__ import annotations

import pytest


def test_from_env_reads_all_required(fresh_module):
    config = fresh_module("common.config")
    cfg = config.from_env()

    assert cfg.domain_name == "test-domain"
    assert cfg.domain_owner == "111111111111"
    assert cfg.source_repo_names == ("test-staging-npm", "test-staging-pypi")
    assert cfg.target_repo_name == "test-prod"
    assert cfg.audit_table_name == "test-audit"
    assert cfg.state_machine_arn.endswith(":stateMachine:test-quarantine")
    assert cfg.log_level == "INFO"
    assert cfg.scanner_type == "inspector"
    assert cfg.scanner_lambda_arn == ""
    assert cfg.block_on_severity == ("HIGH", "CRITICAL")


def test_from_env_raises_when_required_missing(monkeypatch, fresh_module):
    config = fresh_module("common.config")
    monkeypatch.delenv("DOMAIN_NAME")
    with pytest.raises(RuntimeError, match="DOMAIN_NAME"):
        config.from_env()


def test_block_on_severity_parses_csv(monkeypatch, fresh_module):
    config = fresh_module("common.config")
    monkeypatch.setenv("BLOCK_ON_SEVERITY", "high , critical, medium")
    cfg = config.from_env()
    # Whitespace stripped, uppercased.
    assert cfg.block_on_severity == ("HIGH", "CRITICAL", "MEDIUM")


def test_block_on_severity_handles_empty(monkeypatch, fresh_module):
    config = fresh_module("common.config")
    monkeypatch.setenv("BLOCK_ON_SEVERITY", "")
    cfg = config.from_env()
    # Empty string → no severities. Defensive: this means the scanner will never
    # report "blocked", only "findings" or "clean". Documented behaviour.
    assert cfg.block_on_severity == ()


def test_source_repo_names_handles_single(monkeypatch, fresh_module):
    config = fresh_module("common.config")
    monkeypatch.setenv("SOURCE_REPO_NAMES", "only-staging")
    cfg = config.from_env()
    assert cfg.source_repo_names == ("only-staging",)


def test_source_repo_names_handles_empty(monkeypatch, fresh_module):
    config = fresh_module("common.config")
    monkeypatch.setenv("SOURCE_REPO_NAMES", "")
    cfg = config.from_env()
    assert cfg.source_repo_names == ()


def test_log_level_defaults_to_info(monkeypatch, fresh_module):
    config = fresh_module("common.config")
    monkeypatch.delenv("LOG_LEVEL")
    cfg = config.from_env()
    assert cfg.log_level == "INFO"
