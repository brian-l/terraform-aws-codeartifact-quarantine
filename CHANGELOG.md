# Changelog

All notable changes to this module are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Step Functions log group is now encrypted with a dedicated customer-managed KMS key, scoped via encryption context to only this log group. CloudWatch Logs reads now require both `logs:GetLogEvents` IAM and `kms:Decrypt` on the key.
- Step Functions logging defaults dropped from `level=ALL` / `include_execution_data=true` to `level=ERROR` / `include_execution_data=false`. The verbose configuration is opt-in via the new `var.pipeline.logging` block. Reduces incidental disclosure of Inspector findings, package metadata, and (when approval is required) the Step Functions task token via log reads.
- Added a "Consumer responsibilities" section to `SECURITY.md` documenting the security properties the notification SNS topic must have (no email/SMS subscriptions, SSE-KMS encryption, access-restricted resource policy, IAM-controlled subscribers only). The task token for the human-approval gate is published in the SNS message body; a broadly-subscribed topic would let any subscriber bypass approval.

### Added

- Initial module skeleton (`versions.tf`, `variables.tf`, `outputs.tf`, `main.tf`, `locals.tf`).
- `modules/codeartifact/` submodule: domain, repositories with external connections and upstreams, package group origin controls, optional KMS key.
- `modules/pipeline/` submodule: EventBridge rule, SQS queue + DLQ, Step Functions state machine, IAM, DynamoDB audit table.
- Lambda handler layout under `lambda/` for ingestion, scan, promote, audit, expedite, approve.
- Examples: `simple`, `with-internal-packages`, `multi-account`, `custom-scanner`.
- README, PLAN, comparison docs.
- `var.pipeline.logging` configuration block (`level`, `include_execution_data`).

## [0.1.0] — TBD

First tagged pre-release. Interface subject to change before 1.0.
