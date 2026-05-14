# Security Policy

## Reporting a vulnerability

If you find a security issue in this module — whether in the Terraform code, the Lambda handlers, the IAM policy templates, or the release artifacts — please report it privately:

1. Open a [GitHub Security Advisory](https://github.com/brian-l/terraform-aws-codeartifact-quarantine/security/advisories/new) for this repo, **or**
2. Email the maintainer (see the GitHub profile of `brian-l`).

Please **do not** open a public issue or pull request for security reports until a fix is available. Coordinated disclosure is appreciated; I'll aim to acknowledge reports within 5 business days and target a fix within 30 days for confirmed vulnerabilities, sooner for actively exploited ones.

## Consumer responsibilities

This module is composed; some security properties depend on configuration the consumer provides. Read these carefully before deploying.

### The notification SNS topic (`var.pipeline.approval.notification_arn`)

The state machine's human-approval gate uses Step Functions' `waitForTaskToken` integration with SNS. **The task token is published in the SNS message body.** Anyone who can read the message — through a topic subscription, a downstream queue, an email delivery, an SMS, or historical delivery records — can call `SendTaskSuccess` and resolve the approval. The token grants execution-level authority, not topic-level, but a leaked token bypasses your human-review gate.

**Default (recommended): leave `notification_arn` unset.** The module creates a hardened topic — dedicated KMS CMK, topic policy locked to the Step Functions role for publish, no subscriptions. The ARN is exposed via `module.<name>.notification_topic_arn`; consumers attach IAM-controlled subscribers (Lambda or SQS) themselves. The module enforces every property below for the topic it manages.

**Override path:** pass an existing topic ARN only when you need to share a single topic across multiple pipelines or fan in from other systems. In that case the module cannot enforce the properties below — they become your responsibility:

- **No email or SMS subscriptions.** Both deliver the token in plaintext to an inbox/handset the recipient may share, screenshot, or store insecurely. If you want human notifications, fan out through an internal Lambda that strips the token before re-emitting.
- **SSE-KMS encryption on the topic** (`kms_master_key_id`). Without it, message bodies are stored unencrypted at rest in SNS.
- **Topic resource policy restricted to your account.** No `"Principal": "*"`. No external accounts unless explicitly required.
- **Same-account subscribers only**, vetted individually. Cross-account subscriptions widen the trust boundary.
- **Subscribers should be IAM-controlled compute** (Lambda, SQS) — not human-readable endpoints. The approval Lambda this module ships (`<name>-approve`) is the canonical callback target.

If your approval policy is `"never"` (`var.pipeline.approval.required_when = "never"`), the SNS topic is only used for block/timeout alerts and the token concern doesn't apply — but the topic is still emitted to and should be access-controlled.

For higher-assurance deployments, consider building an indirection layer: the state machine emits an opaque approval ID (not the token); a Lambda you control holds the token-to-ID mapping in DynamoDB and verifies the approver's identity before calling `SendTaskSuccess`. This is out of scope for the module at 1.0; if there's appetite, file an issue.

### The expedite Lambda (`<name>-expedite`)

The expedite path skips the cooldown for security patches. Authorization is **IAM-only**: anyone with `lambda:InvokeFunction` on the expedite Lambda can request a fast-track promotion. The Lambda hard-codes `approvalMode = "always"`, so the human-approval gate (configured via `var.pipeline.approval`) still runs — that gate is what actually authorizes the promotion.

Restrict `lambda:InvokeFunction` on the expedite Lambda to the principals that should be allowed to *request* expedites (security team, on-call engineers, approved CI roles). Audit who has it as carefully as you audit who can approve.

### CodeArtifact serves cached versions as canonical

CodeArtifact never re-fetches a package version once it has been cached. If a version is yanked, deprecated, unpublished, or flagged as malicious upstream *after* it landed in staging, CodeArtifact will continue serving the cached bytes via prod's upstream chain indefinitely — there is no mechanism in CodeArtifact itself to invalidate a cached version against the upstream state.

This is the most operationally significant caveat of the soft-gate model. The module ships an **opt-in** compensating control: `var.pipeline.yank_detection.enabled = true` stands up a scheduled Lambda that polls PyPI yank metadata, npm packument deprecations/unpublishes, and OSV.dev malware advisories on a configurable schedule (default hourly) and applies a configured response (`unlist` / `dispose` / `delete` / `alert`) to both staging and prod copies of newly-yanked versions. See README → "Handling yanked / unpublished / malicious packages" for the full configuration surface and the operational notes (first-run alert volume, `delete` destroys audit evidence, etc.).

Leaving `yank_detection.enabled = false` (the module default, so upgrades from 0.1.1 don't add resources silently) is an explicit acceptance of this risk: a confirmed-malicious version cached in your staging repo before the advisory landed will keep installing for your consumers until someone manually disposes it. For any deployment fronting real workloads, enable yank detection.

### The Inspector v2 enablement (`var.enable_inspector`)

When set to `true`, the module enables Inspector v2 CodeArtifact scanning on the **entire AWS account**, not just this module's domain. Inspector has its own pricing model and surface. Leave this `false` if another module or process already owns Inspector enablement.

## Supply-chain practices in this repo

Because the module's purpose is supply-chain protection, the module's own supply chain is held to the same standard. Notably:

- **Pre-commit hooks** are pinned to full commit SHAs, not tags. Tags can be force-moved; commits cannot.
- **GitHub Actions** in `.github/workflows/` are pinned to full commit SHAs with a `# vX.Y.Z` comment for human reference.
- **Releases** carry a [SLSA build provenance attestation](https://slsa.dev/spec/v1.0/provenance) signed via Sigstore. Verify with:
  ```bash
  gh attestation verify terraform-aws-codeartifact-quarantine-<version>.tar.gz --owner brian-l
  ```
- **Git tags** that mark releases should be GPG- or SSH-signed by the maintainer. Verify with:
  ```bash
  git tag -v v<version>
  ```
- **Dependabot** opens weekly PRs for GitHub Actions and Terraform provider version drift. Security advisories trigger PRs immediately, bypassing the weekly cadence.
- **`.terraform.lock.hcl`** is gitignored intentionally — this is a reusable module, so consumers pin their own provider versions. Convention for root configurations would commit the lockfile.

## Scope

In-scope for security reporting:
- IAM policy templates that grant more permissions than necessary
- Lambda handler code with injection risks
- Terraform configurations that create publicly-readable resources by default
- Dependency declarations that pull from compromised sources
- Release artifacts or attestations that don't verify

Out of scope (these belong upstream):
- Vulnerabilities in `hashicorp/aws`, `hashicorp/awscc`, or `hashicorp/archive` provider source
- Vulnerabilities in Python `boto3` or AWS Lambda runtime
- Vulnerabilities in pre-commit hook upstream repos
- Vulnerabilities in CodeArtifact or other AWS service APIs

## Supported versions

The most recent minor release line receives security updates. Older minor releases get critical fixes only.

| Version | Supported |
|---|---|
| 1.x latest | yes |
| 1.x older | critical only |
| pre-1.0 | no |
