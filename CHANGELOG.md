# Changelog

All notable changes to this module are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-05-13

### Added

- `var.pipeline.approval.notification_arn` is now optional. When unset (the new default) the module creates a hardened SNS topic: dedicated customer-managed KMS key, topic resource policy locked to the Step Functions role for publish, zero subscriptions. Consumers attach their own IAM-controlled subscribers against `module.<name>.notification_topic_arn`. Pass an explicit ARN only when sharing a pre-existing topic — SECURITY.md documents the properties that pre-existing topic must satisfy.
- `var.required_tag_keys` lets operators enforce presence of compliance tag keys (e.g. `data-classification`, `owner`, `cost-center`, `environment`) at plan time. Missing keys produce a `check` failure naming the gap. Supports ISO/SOC2 tagging schemas.
- `awscc_codeartifact_package_group` resources now receive `var.tags` (converted to the AWSCC list-of-objects format). All other taggable resources were already tagged.
- Managed consumer IAM policy (`var.create_consumer_policy`, default `true`) granting the minimum permissions a downstream workload needs to pull from the prod repository: `codeartifact:GetAuthorizationToken` / `GetDomainPermissionsPolicy` on the domain, read/list/describe on the prod repo (ARN + `arn/*`), and `sts:GetServiceBearerToken` scoped to `codeartifact.amazonaws.com`. Exposed via `consumer_policy_arn`; the raw JSON is always available via `consumer_policy_document` for inline use.
- Unit test suite for all six Lambda handlers and the shared `common/` modules (53 tests). Uses `botocore.stub.Stubber` for AWS API mocking — no live AWS calls or extra service emulators. CI runs the suite with a coverage gate via the `astral-sh/setup-uv` action.

### Changed

- CI now installs `tflint`, `trivy`, and `terraform-docs` ahead of the pre-commit-terraform hooks so they actually run on every PR.
- Module variable and output descriptions filled in across `modules/codeartifact/` and `modules/pipeline/` to satisfy `terraform_documented_variables` / `terraform_documented_outputs`. Standard-module-structure gaps closed (empty `outputs.tf` in `modules/inspector/`, empty `main.tf` in `modules/pipeline/`, `outputs.tf` split out of `examples/simple/main.tf`).

### Removed

- `var.pre_promote_lambda_arn`, `var.post_promote_lambda_arn`, and `var.policy_storage` were declared at the root but never wired through the pipeline module — they did nothing. Will be reintroduced alongside the implementation when those features land.

### Fixed

- `_iso8601_to_seconds` in the ingestion handler now uses a regex parser supporting the full subset of ISO-8601 durations the module uses (D + H + M + S in any combination, e.g. `P1DT12H`, `PT1H30M45S`). The previous parser silently ignored seconds and certain combinations.
- Renamed the internal `_cooldown_match` local in `modules/pipeline/` to `cooldown_match` to satisfy `terraform_naming_convention`.

## [0.1.0] - 2026-05-12

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
