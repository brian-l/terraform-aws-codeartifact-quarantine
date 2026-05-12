# Implementation Plan — `terraform-aws-codeartifact-quarantine`

A Terraform module that adds a supply-chain quarantine to AWS CodeArtifact: external packages land in a staging repo, sit for a configurable cooldown, get scanned by Inspector (or a pluggable Lambda), and are promoted to a prod repo only after passing. Includes a human-approval path for findings and a one-command expedite for security patches.

This document is the implementation roadmap and design record. The user-facing entry point is `README.md`.

---

## Why this exists

CodeArtifact has the right primitives (package origin controls, package version states, EventBridge events, Inspector integration) but no out-of-the-box wiring that delivers a working quarantine. The package-manager ecosystem (pnpm, yarn, bun, uv, pip, Renovate, Dependabot) all shipped client-side cooldowns in 2025–2026, but no server-side equivalent exists for CodeArtifact. This module fills that gap.

See `docs/comparison.md` (TODO) for a survey of adjacent tools and why none of them fit.

---

## Architecture

```
public npm/PyPI/Maven/...
        │
        ▼
┌───────────────┐  EventBridge   ┌─────────┐   ┌───────────────────┐
│  staging-repo │ ─ on Created ─►│   SQS   │──►│  ingestion Lambda │
│ (external     │                └─────────┘   │ starts SFN exec   │
│  connections) │                              └─────────┬─────────┘
└───────────────┘                                        │
                                                         ▼
                                              ┌──────────────────────┐
                                              │   Step Functions     │
                                              │                      │
                                              │  Wait (cooldown)     │
                                              │       │              │
                                              │       ▼              │
                                              │  scan Lambda         │
                                              │  (Inspector or       │
                                              │   custom scanner)    │
                                              │       │              │
                                              │       ▼              │
                                              │  Choice              │
                                              │  ├─ clean   ─► copy  │
                                              │  ├─ findings─► human │
                                              │  └─ blocked ─► notify│
                                              │       │              │
                                              │       ▼              │
                                              │  audit Lambda        │
                                              └──────────┬───────────┘
                                                         │
                                                         ▼
                                              ┌───────────────────┐
                                              │    prod-repo      │ ◄── consumers
                                              │ (no external      │     install
                                              │  connection)      │     from here
                                              └───────────────────┘

   ┌──────────────────┐
   │ internal-repo    │ ◄── @myorg/*, myorg-* (publish-only, no quarantine)
   └──────────────────┘
```

### Key design decisions

1. **Two-repo airlock** (`staging-repo` + `prod-repo`) instead of single-repo status flips.
   Eliminates the race between EventBridge event firing and Lambda updating status. Consumers point only at `prod-repo`; nothing ever points at `staging-repo`.

2. **Optional `internal-repo`** for internally-published packages.
   Bypasses the quarantine for first-party code without weakening external-package controls. Locked down by package group origin controls (`Publish: ALLOW`, `Upstream: BLOCK` on internal scope patterns).

3. **EventBridge → SQS → Lambda**, not EventBridge → Lambda direct.
   SQS gives DLQ + retry + replay. Cheap insurance against transient Lambda failures.

4. **Step Functions for orchestration**, not a single mega-Lambda.
   `Wait` state replaces what would be a sleeping Lambda (forbidden by Lambda's runtime model). `waitForTaskToken` gives us human approval cleanly. Each step is independently retryable.

5. **Inspector v2 as default scanner**, pluggable via Lambda ARN.
   Inspector covers npm, PyPI, Maven, NuGet, Gem. Consumers can override with their own Lambda (Socket, Phylum, custom behavioral scanner) without forking the module.

6. **Package group origin controls** as first-class input (`var.package_groups`).
   The dependency-confusion defense (`pypi:myorg-* → Publish:ALLOW, Upstream:BLOCK`) is data, not code.

7. **All Lambdas inline** as `archive_file` zips from `lambda/` subdir.
   No external image build, no ECR repo, no separate publish pipeline. `terraform apply` is self-contained.

8. **Module is AWS-only.** No k8s, no Argo, no consumer CI configuration. Outputs registry URLs; consumers wire them into `.npmrc` / `pyproject.toml` / CI auth themselves.

---

## Repository layout

```
terraform-aws-codeartifact-quarantine/
├── README.md                       # User-facing overview + quickstart
├── PLAN.md                         # This file
├── CHANGELOG.md
├── LICENSE
├── versions.tf                     # Terraform + provider version constraints
├── variables.tf                    # Public variable schema
├── outputs.tf                      # Module outputs (registry URLs, ARNs)
├── main.tf                         # Submodule composition
├── locals.tf                       # Derived names, validations
├── data.tf                         # Region, account ID lookups
│
├── modules/
│   ├── codeartifact/               # Domain, repos, origin controls
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   ├── versions.tf
│   │   └── package_groups.tf
│   │
│   ├── pipeline/                   # EventBridge → SQS → SFN → Lambdas
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   ├── versions.tf
│   │   ├── eventbridge.tf
│   │   ├── sqs.tf
│   │   ├── step_functions.tf
│   │   ├── state_machine.json.tftpl
│   │   ├── lambdas.tf
│   │   ├── iam.tf
│   │   └── dynamodb.tf
│   │
│   └── inspector/                  # Optional Inspector v2 enablement
│       ├── main.tf
│       ├── variables.tf
│       └── versions.tf
│
├── lambda/
│   ├── common/                     # Shared utilities (boto3 clients, logging)
│   │   ├── __init__.py
│   │   ├── codeartifact.py
│   │   ├── audit.py
│   │   └── logging.py
│   ├── ingestion/
│   │   ├── handler.py              # EventBridge → SFN execution start
│   │   └── requirements.txt
│   ├── scan/
│   │   ├── handler.py              # Inspector ListFindings + policy eval
│   │   └── requirements.txt
│   ├── promote/
│   │   ├── handler.py              # copy-package-versions
│   │   └── requirements.txt
│   ├── audit/
│   │   ├── handler.py              # DynamoDB put_item
│   │   └── requirements.txt
│   ├── expedite/
│   │   ├── handler.py              # Manual override entry point
│   │   └── requirements.txt
│   └── approve/
│       ├── handler.py              # SendTaskSuccess/Failure callback
│       └── requirements.txt
│
├── examples/
│   ├── simple/                     # Bare minimum config
│   ├── with-internal-packages/     # Adds internal-repo + package groups
│   ├── multi-account/              # Cross-account consumer reads
│   └── custom-scanner/             # Pluggable scanner Lambda
│
├── test/
│   ├── basic_test.go               # terratest: terraform plan/apply/destroy
│   └── fixtures/
│
└── .github/
    └── workflows/
        ├── ci.yml                  # tflint, tfsec, terraform fmt/validate
        ├── lambda-tests.yml        # pytest for Lambda handlers
        └── release.yml             # auto-tag + release notes
```

---

## Variable schema (frozen for v1.0)

```hcl
variable "name" {
  description = "Prefix for all created resources (e.g., 'platform-pkg-quarantine')."
  type        = string
}

variable "domain_name" {
  description = "CodeArtifact domain name."
  type        = string
}

variable "domain_kms_key_arn" {
  description = "KMS key ARN for the CodeArtifact domain. If null, a key is created."
  type        = string
  default     = null
}

variable "repositories" {
  description = <<-EOT
    Map of CodeArtifact repositories to create. The keys 'staging' and 'prod' are required.
    Add 'internal' (or any other key) for additional repos.
  EOT
  type = map(object({
    description          = optional(string)
    external_connections = optional(list(string), [])
    upstreams            = optional(list(string), [])
  }))
  validation {
    condition     = contains(keys(var.repositories), "staging") && contains(keys(var.repositories), "prod")
    error_message = "repositories must include both 'staging' and 'prod' keys."
  }
}

variable "pipeline" {
  description = "Quarantine pipeline configuration."
  type = object({
    source_repository = string                   # default "staging"
    target_repository = string                   # default "prod"
    cooldown          = string                   # ISO-8601 duration, e.g. "PT24H"
    scanner = object({
      type              = string                 # "inspector" | "lambda" | "none"
      block_on_severity = optional(list(string), ["HIGH", "CRITICAL"])
      lambda_arn        = optional(string)
    })
    approval = object({
      required_when    = string                  # "always" | "findings" | "never"
      timeout          = optional(string, "P14D")
      notification_arn = string                  # SNS topic for approvals + alerts
    })
  })
}

variable "package_groups" {
  description = "Package group origin control rules. List of {repository, pattern, publish, upstream}."
  type = list(object({
    repository = string
    pattern    = string
    publish    = optional(string, "ALLOW")
    upstream   = optional(string, "ALLOW")
  }))
  default = []
}

variable "consumer_principals" {
  description = "List of AWS principal ARNs allowed to read from prod-repo cross-account."
  type        = list(string)
  default     = []
}

variable "enable_inspector" {
  description = "Enable Inspector v2 CodeArtifact scanning. Account-wide side effect; leave false if another module owns Inspector enablement."
  type        = bool
  default     = false
}

variable "pre_promote_lambda_arn" {
  description = "Optional Lambda invoked after scan, before copy. Can veto promotion."
  type        = string
  default     = null
}

variable "post_promote_lambda_arn" {
  description = "Optional Lambda invoked after successful promotion. Best-effort; failures don't roll back."
  type        = string
  default     = null
}

variable "policy_storage" {
  description = "Where pipeline policy lives. 'env' bakes into Lambda env vars (immutable until apply); 'ssm' uses Parameter Store (editable)."
  type        = string
  default     = "env"
  validation {
    condition     = contains(["env", "ssm"], var.policy_storage)
    error_message = "policy_storage must be 'env' or 'ssm'."
  }
}

variable "tags" {
  description = "Tags applied to all created resources."
  type        = map(string)
  default     = {}
}
```

## Outputs

```hcl
output "repository_endpoints" {
  description = "Map of repo_key -> registry endpoint URL (npm + pypi flavors)."
  value = {
    for k, r in module.codeartifact.repositories : k => {
      npm  = "${r.endpoint}/${r.name}/"  # actual format varies per package format
      pypi = "${r.endpoint}/${r.name}/simple/"
    }
  }
}

output "prod_repository_endpoint_npm"  { ... }
output "prod_repository_endpoint_pypi" { ... }
output "staging_repository_arn"        { ... }
output "prod_repository_arn"           { ... }
output "internal_repository_arn"       { ... }  # null if not configured

output "audit_table_name" { ... }
output "audit_table_arn"  { ... }

output "expedite_lambda_arn"      { ... }
output "expedite_lambda_function_name" { ... }
output "approval_lambda_arn"      { ... }
output "state_machine_arn"        { ... }

output "notification_topic_arn"   { ... }  # consumer-provided pass-through
```

---

## Implementation phases

### Milestone 1 — Module skeleton + CodeArtifact submodule (Day 1)

- [x] LICENSE, .gitignore (already exists)
- [ ] README.md (user-facing, with quickstart)
- [ ] PLAN.md (this file)
- [ ] CHANGELOG.md (initial entry: "0.1.0 — Pre-release")
- [ ] versions.tf, variables.tf, outputs.tf, main.tf, locals.tf (root)
- [ ] modules/codeartifact/ — domain, repos, package groups, KMS

**Exit criteria:** `terraform validate` passes on the root module. `examples/simple/` plans cleanly (even though it only creates CA resources, no pipeline yet).

### Milestone 2 — Pipeline submodule shell (Day 2)

- [ ] modules/pipeline/ — empty TF files committed with stubs and TODO comments
- [ ] EventBridge rule + SQS queue + DLQ
- [ ] IAM roles for each Lambda (stub policies)
- [ ] DynamoDB audit table
- [ ] Step Functions state machine definition (placeholder Lambdas)
- [ ] One Lambda (`ingestion`) with real handler code

**Exit criteria:** `terraform apply` against a sandbox account creates all resources. The ingestion Lambda receives EventBridge events but no downstream wiring works yet.

### Milestone 3 — Lambda handlers (Day 3–4)

- [ ] `lambda/common/` — shared boto3 clients, structured logging, env config parser
- [ ] `lambda/ingestion/` — parses EventBridge payload, starts SFN execution
- [ ] `lambda/scan/` — calls Inspector ListFindings, applies severity policy
- [ ] `lambda/promote/` — `copy-package-versions` staging → prod
- [ ] `lambda/audit/` — DynamoDB put_item with full context
- [ ] `lambda/expedite/` — manual override entry, starts SFN with skip-cooldown flag
- [ ] `lambda/approve/` — `SendTaskSuccess`/`SendTaskFailure` callback target

**Exit criteria:** End-to-end happy path works: publish a package to `staging-repo`, wait for cooldown, see it copied to `prod-repo`, see DynamoDB audit row.

### Milestone 4 — Inspector + scanner plugin point (Day 4)

- [ ] modules/inspector/ — optional v2 enablement
- [ ] Scan Lambda supports `scanner.type = "lambda"` (invokes consumer-provided ARN)
- [ ] Test custom scanner contract via `examples/custom-scanner/`

**Exit criteria:** `examples/custom-scanner/` with a no-op Lambda returns "clean" and the workflow promotes.

### Milestone 5 — Examples + tests (Day 5)

- [ ] `examples/simple/` — staging + prod only, no internal repo, no approval
- [ ] `examples/with-internal-packages/` — adds internal-repo, package groups for `pypi:myorg-*` and `npm:@myorg/*`
- [ ] `examples/multi-account/` — `consumer_principals` cross-account read
- [ ] `examples/custom-scanner/` — pluggable scanner with stub Lambda
- [ ] `test/basic_test.go` — terratest harness running `simple` against a real AWS account
- [ ] `.github/workflows/ci.yml` — fmt, validate, tflint, tfsec, checkov

**Exit criteria:** All examples pass `terraform plan`. CI is green. Terratest runs end-to-end against the sandbox account.

### Milestone 6 — Documentation + 1.0 release (Day 6)

- [ ] README.md fleshed out with architecture diagram, quickstart, full variable reference
- [ ] docs/comparison.md — survey of adjacent tools (SafeDep, Inspector standalone, Renovate, JFrog Curation)
- [ ] docs/operations.md — runbook (handling alerts, expediting, rotating KMS, etc.)
- [ ] docs/extending.md — how to write a custom scanner Lambda + pre/post-promote hooks
- [ ] Tag `v1.0.0`, publish to Terraform Registry

**Exit criteria:** Module is on the Terraform Registry. README quickstart works from a clean clone.

---

## Open design questions

1. **Lambda packaging strategy.** Inline `archive_file` is simplest but doesn't handle dependencies. Options:
   - (a) Inline zip, vendor dependencies into the zip via `pip install --target` before packaging. Used for small Lambdas. Simpler ops.
   - (b) Lambda layer for shared boto3 + dependencies, individual function zips on top. More complex but smaller per-function size.
   - (c) Container images in ECR. Most flexible, requires consumer to have ECR or accept the module managing one.
   - **Tentative: (a)** with a `Makefile` target that runs `pip install` before each apply. Document as a prerequisite step.

2. **Approval webhook integration.** Step Functions task token + `SendTaskSuccess`/`SendTaskFailure` is generic. How does the consumer wire it to Slack?
   - Option: module emits SNS with the task token; consumer subscribes a Slack-bot Lambda. Clean separation.
   - Option: ship a reference Slack-bot Lambda as an optional submodule. Faster adoption but adds Slack-specific code to the OSS module.
   - **Tentative:** SNS-only in core module; reference Slack bot lives in a sibling repo or `examples/`.

3. **Multi-format scanner ergonomics.** Inspector v2 supports npm, PyPI, Maven, NuGet, Gem. We should not artificially limit; let consumers configure formats.
   - The `repositories[*].external_connections` list controls what flows in. Scanner is format-agnostic if it just calls Inspector.

4. **State machine versioning.** When we change the SFN definition, in-flight executions still use the old version. Document that upgrades require either draining or accepting the mismatch.

5. **DynamoDB schema.** Partition key options:
   - (a) `package_arn` (HASH), `version#timestamp` (RANGE) — supports query-by-package
   - (b) `package_arn#version` (HASH only) — simpler, no time-series queries
   - **Tentative: (a)** with a GSI on `decision` for "show me all blocked versions in the last 30 days."

6. **Tooling for the expedite path.** A small CLI wrapper around `aws lambda invoke` is much nicer than raw invoke. Where does it live?
   - In this module? No — modules don't ship binaries.
   - Sibling repo `codeartifact-quarantine-cli`? Maybe.
   - Just document the curl/aws-cli invocation in README? Probably enough for v1.

---

## Non-goals for v1

- Managing the consumer's EKS cluster, CI runners, or `.npmrc`/`pyproject.toml`.
- Slack/Teams/PagerDuty integration (SNS emit only).
- Multi-region replication of the CodeArtifact domain.
- Container image scanning (ECR has its own Inspector integration).
- Terraform module hosting (CodeArtifact supports generic packages but TF modules need a different protocol).
- Behavioral malware scanning (composable via `scanner.type = "lambda"`).

---

## Compatibility commitments

- **Terraform**: `>= 1.5.0` (for `optional()` with defaults in object types).
- **AWS provider**: `>= 5.50.0` (Inspector v2 CodeArtifact scanning resources).
- **Python Lambda runtime**: `python3.12` (current LTS as of 2026-05).
- **Semver**: `MAJOR.MINOR.PATCH`. Breaking changes to the variable schema bump MAJOR.

---

## What this module deliberately does not abstract

To stay small and honest:

- We don't try to abstract over which **package formats** are configured. Consumers list them explicitly in `repositories[*].external_connections`.
- We don't try to ship a "default policy" beyond "block on HIGH/CRITICAL Inspector findings." Consumers tune `pipeline.scanner.block_on_severity`.
- We don't try to enforce naming conventions for the resources we create beyond `var.name` prefixing. Customization beyond that requires a fork.

These limits are why the module is ~600 lines instead of ~6000.
