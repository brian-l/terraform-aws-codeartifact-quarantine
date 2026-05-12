# terraform-aws-codeartifact-quarantine

A Terraform module that adds a supply-chain quarantine to AWS CodeArtifact.

External packages land in a staging repo, sit for a configurable cooldown window (default 24h), get scanned by Amazon Inspector (or a pluggable Lambda), and are promoted to a prod repo only after passing. Human-approval gate for findings. One-command expedite path for security patches.

> **Status: pre-1.0 — interface may change. Do not use in production yet.**

---

## Why

Package-registry compromises are now a monthly occurrence (TanStack/Mistral, Shai-Hulud, ctrl/tinycolor, Lottiefiles, Nx, ...). The defense the ecosystem converged on is a **cooldown**: don't install a package until it's been published long enough for the community and security vendors to catch problems. Every major package manager added a client-side cooldown in 2025–2026 — pnpm `minimumReleaseAge`, yarn `npmMinimalAgeGate`, bun, uv, pip `--uploaded-prior-to`, Deno, Renovate, Dependabot.

But client-side cooldowns leak: a forgetful CI runner, a dev with custom `.npmrc`, or an AI agent that ignores workspace settings can bypass them. A **server-side** cooldown at the registry level is the obvious next layer. CodeArtifact has every primitive you need (EventBridge events, package version states, Inspector v2 scanning, copy-package-versions, package origin controls) but no off-the-shelf wiring. This module is that wiring.

See [`PLAN.md`](./PLAN.md) for full architecture and roadmap.

---

## What you get

- **Two-repo airlock**: external packages land in `staging-repo`, consumers read from `prod-repo`. Versions move between them only after vetting.
- **Configurable cooldown** (default 24h) before any scanning runs.
- **Inspector v2 scanning** by default; pluggable Lambda for Socket / Phylum / custom behavioral scanners.
- **Human-approval gate** triggered by findings, wired through SNS.
- **Audit trail** of every promotion (and rejection) in DynamoDB.
- **Optional `internal-repo`** for first-party packages, bypassing the quarantine.
- **Package group origin controls** as configuration — codifies the dependency-confusion defense.
- **Expedite path** for security patches: one Lambda invoke, same audit trail, no waiting.

---

## Architecture

```
public registry ─► staging-repo ─► EventBridge ─► SQS ─► Step Functions
                                                              │
                                              wait → scan → choice ─► copy ─► prod-repo
                                                              │                    ▲
                                                              └─► human-approve ───┘
                                                                  (on findings)
```

For the full diagram, design rationale, and trade-offs, see [`PLAN.md`](./PLAN.md).

---

## Quickstart

```hcl
module "quarantine" {
  source  = "brian-l/codeartifact-quarantine/aws"
  version = "~> 0.1"

  name        = "platform-pkg-quarantine"
  domain_name = "platform"

  repositories = {
    staging = {
      external_connections = ["public:npmjs", "public:pypi"]
    }
    prod = {
      upstreams = ["staging"]
    }
  }

  pipeline = {
    source_repository = "staging"
    target_repository = "prod"
    cooldown          = "PT24H"
    scanner = {
      type              = "inspector"
      block_on_severity = ["HIGH", "CRITICAL"]
    }
    approval = {
      required_when    = "findings"
      notification_arn = aws_sns_topic.security.arn
    }
  }
}

# Consumers read from this endpoint
output "npm_registry" {
  value = module.quarantine.repository_endpoints["prod"].npm
}
```

See [`examples/`](./examples/) for more configurations:

- `examples/simple/` — minimal staging+prod
- `examples/with-internal-packages/` — adds an internal-repo with origin controls
- `examples/multi-account/` — cross-account consumer reads
- `examples/custom-scanner/` — plug in your own scanner Lambda

---

## What this module does NOT do

To stay focused and small:

- **Does not** manage your EKS cluster, CI runners, or developer machines.
- **Does not** configure your `.npmrc` / `pyproject.toml` / pip indexes — outputs the registry URLs, you wire them in.
- **Does not** provide Slack/Teams/PagerDuty integration directly — emits SNS, you subscribe whatever.
- **Does not** enable Inspector v2 by default (account-wide side effect; usually owned by another module). Set `enable_inspector = true` if you want this module to own it.
- **Does not** replace your client-side `minimumReleaseAge` settings — those defend a different surface (developer machines bypassing CI). Run both.
- **Does not** scan container images — that's ECR's Inspector integration, separately.

---

## Comparison with alternatives

| Tool | What it does | Why this module is different |
|---|---|---|
| `pnpm minimumReleaseAge`, yarn `npmMinimalAgeGate`, uv `--exclude-newer`, Renovate cooldown, Dependabot cooldown | Client-side cooldown enforced in the package manager | Server-side: can't be bypassed by a forgetful tool or AI agent |
| `aws-samples/codeartifact-retention-policy` | Deletes *old* packages past a retention period | This module quarantines *new* packages before promotion — opposite end of the lifecycle |
| `jonrau1/CodeArtifactVulnScanner` | Scans CodeArtifact packages against NIST NVD and purges on CVE | This module quarantines *before* scan, uses Inspector v2 (not NVD scraping), and supports promotion rather than purge. Built ~2020, predates Inspector v2 |
| JFrog Xray Curation | Quarantine + scanning for Artifactory | This is the equivalent for CodeArtifact, OSS |
| SafeDep `vet` / `pmg` | Behavioral scanning of dependencies | Complementary — plug them in as the `scanner.type = "lambda"` extension point |
| Inspector v2 standalone | Vulnerability findings on CodeArtifact assets | This module wires Inspector into a promotion gate; standalone Inspector tells you about vulns but doesn't act on them |

---

## Requirements

| Component | Version |
|---|---|
| Terraform | >= 1.5.0 |
| AWS provider | >= 5.50.0 |
| Python Lambda runtime | 3.12 |

The module assumes you have:

- An AWS account where you can create CodeArtifact, IAM, Lambda, Step Functions, EventBridge, SQS, DynamoDB, KMS, and (optionally) Inspector v2 resources.
- An SNS topic for notifications/approval messages (you can pass the ARN of an existing topic, or create one with `aws_sns_topic`).

---

## License

MIT — see [`LICENSE`](./LICENSE).

---

## Contributing

This is pre-1.0; the variable schema may change. Issues and PRs welcome but please open an issue to discuss before sending non-trivial patches. See `PLAN.md` for the design intent — changes that conflict with stated non-goals or design decisions will be redirected there for discussion first.
