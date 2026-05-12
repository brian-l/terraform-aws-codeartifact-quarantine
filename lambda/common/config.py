"""Environment variable parsing.

Read once at module load so handlers don't re-parse on each invocation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    domain_name: str
    domain_owner: str
    # Source-repo identity is event-driven (EventBridge detail.repositoryName), not
    # config-driven. We expose the list of allowed sources for validation only —
    # e.g. the expedite handler verifies the caller named a known staging repo.
    source_repo_names: tuple[str, ...]
    target_repo_name: str
    audit_table_name: str
    state_machine_arn: str
    log_level: str

    scanner_type: str = "inspector"
    scanner_lambda_arn: str = ""
    block_on_severity: tuple[str, ...] = ("HIGH", "CRITICAL")


def from_env() -> Config:
    severities = os.environ.get("BLOCK_ON_SEVERITY", "HIGH,CRITICAL")
    sources = os.environ.get("SOURCE_REPO_NAMES", "")
    return Config(
        domain_name=_required("DOMAIN_NAME"),
        domain_owner=_required("DOMAIN_OWNER"),
        source_repo_names=tuple(s.strip() for s in sources.split(",") if s.strip()),
        target_repo_name=_required("TARGET_REPO_NAME"),
        audit_table_name=_required("AUDIT_TABLE_NAME"),
        state_machine_arn=_required("STATE_MACHINE_ARN"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        scanner_type=os.environ.get("SCANNER_TYPE", "inspector"),
        scanner_lambda_arn=os.environ.get("SCANNER_LAMBDA_ARN", ""),
        block_on_severity=tuple(s.strip().upper() for s in severities.split(",") if s.strip()),
    )


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required env var {name} is not set")
    return value
