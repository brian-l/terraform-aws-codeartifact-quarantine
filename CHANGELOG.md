# Changelog

All notable changes to this module are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial module skeleton (`versions.tf`, `variables.tf`, `outputs.tf`, `main.tf`, `locals.tf`).
- `modules/codeartifact/` submodule: domain, repositories with external connections and upstreams, package group origin controls, optional KMS key.
- `modules/pipeline/` stubs: EventBridge rule, SQS queue + DLQ, Step Functions state machine, IAM, DynamoDB audit table.
- Lambda handler layout under `lambda/` for ingestion, scan, promote, audit, expedite, approve.
- Examples: `simple`, `with-internal-packages`, `multi-account`, `custom-scanner`.
- README, PLAN, comparison docs.

## [0.1.0] — TBD

First tagged pre-release. Interface subject to change before 1.0.
