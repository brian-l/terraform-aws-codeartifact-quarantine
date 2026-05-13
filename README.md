# terraform-aws-codeartifact-quarantine

A Terraform module that adds a supply-chain quarantine to AWS CodeArtifact.

External packages land in a staging repo, sit for a configurable cooldown window (default 24h), get scanned by Amazon Inspector (or a pluggable Lambda), and are promoted to a prod repo only after passing. Human-approval gate for findings. One-command expedite path for security patches.

> **Status: pre-1.0 — interface may change. Do not use in production yet.**

---

## Why

Package-registry compromises are now a monthly occurrence (TanStack/Mistral, Shai-Hulud, ctrl/tinycolor, Lottiefiles, Nx, ...). The defense the ecosystem converged on is a **cooldown**: don't install a package until it's been published long enough for the community and security vendors to catch problems. Every major package manager added a client-side cooldown in 2025–2026 — pnpm `minimumReleaseAge`, yarn `npmMinimalAgeGate`, bun, uv, pip `--uploaded-prior-to`, Deno, Renovate, Dependabot.

But client-side cooldowns leak: a forgetful CI runner, a dev with custom `.npmrc`, or an AI agent that ignores workspace settings can bypass them. A **server-side** cooldown at the registry level is the obvious next layer. CodeArtifact has every primitive you need (EventBridge events, package version states, Inspector v2 scanning, copy-package-versions, package origin controls) but no off-the-shelf wiring. This module is that wiring.

---

## What you get

- **Two-repo airlock**: external packages land in `staging-repo`, consumers read from `prod-repo`. Versions move between them only after vetting.
- **Configurable cooldown** (default 24h) before any scanning runs.
- **Inspector v2 scanning** by default; pluggable Lambda for Socket / Phylum / custom behavioral scanners.
- **Human-approval gate** triggered by findings, wired through SNS.
- **Audit trail** of every promotion (and rejection) in DynamoDB.
- **Yank / unpublish / malware detection** on cached versions, via a scheduled Lambda that checks each cached version against upstream registry metadata (PEP 592 / npm deprecation) and OSV.dev advisories. Configurable response (alert / unlist / dispose / delete) — see "Handling yanked / unpublished / malicious packages" below.
- **Proactive cache-fill** pulls new upstream versions into staging on a schedule (follow-mode + optional allowlist) so the quarantine pipeline runs ahead of consumer demand — by the time anyone needs a version, it's already vetted and in prod. See "Proactive cache-fill" below.
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

internal-repo (optional) ─► consumers      # first-party packages, no quarantine
```

### Design notes

- **Two-repo airlock, not single-repo status flips.** Consumers point only at `prod-repo`; nothing ever points at `staging-repo`. Removes the race between EventBridge firing and a Lambda updating package state. By default the airlock is a **soft gate** — prod transparently upstreams from staging, so first installs work immediately and the pipeline curates which versions are *retained* in prod. Strict install-time blocking is opt-in via `package_groups`. See [Developer experience](#developer-experience-how-packages-reach-prod).
- **EventBridge → SQS → Lambda**, not EventBridge → Lambda direct. SQS provides DLQ, retry, and replay — cheap insurance against transient Lambda failures.
- **Step Functions for orchestration**, not one mega-Lambda. The cooldown is a SFN `Wait` state (Lambdas can't sleep for hours), `waitForTaskToken` cleanly handles human approval, and each step is independently retryable.
- **Inspector v2 as the default scanner**, pluggable via `scanner.type = "lambda"`. Inspector covers npm/PyPI/Maven/NuGet/Gem; swap in Socket, Phylum, or your own behavioral scanner without forking.
- **Package group origin controls are data**, not code (`var.package_groups`). The dependency-confusion defense (`pypi:myorg-* → Publish: ALLOW, Upstream: BLOCK`) is a row in your tfvars.
- **Lambdas ship inline** as `archive_file` zips from `lambda/`. No external image build, no ECR repo. `terraform apply` is self-contained.

---

## Developer experience: how packages reach prod

There are two ways to operate the airlock. **The soft-gate model below is the recommended default** — it is what `examples/simple/` ships, and what the variable defaults produce. The strict alternative is documented for completeness but is opt-in.

### Default: soft-gate (recommended)

With prod's `upstreams = ["staging-*"]` and no `BLOCK` rules in `package_groups`:

1. A developer points their `.npmrc` / `pip.conf` at the **prod** endpoint and runs `npm install` (or `pip install`, `uv sync`, ...) like normal.
2. For any package version not yet known to the module, CodeArtifact transparently traverses prod → staging → public registry, caches the artifact in staging, and returns it to the client. **The first install succeeds with normal public-registry latency** (~1–3s of cache-fill per new version). No 404, no `--registry` flag-flipping, no waiting.
3. Each new version that lands in staging emits a `CodeArtifact Package Version State Change` event. The pipeline picks it up, waits out the cooldown, runs the configured scanner, optionally pauses for human approval on findings, and then **copies the version into prod** for durable retention.
4. From then on, that exact version is physically resident in prod — independent of staging, surviving any staging teardown, and recorded in the audit table with the scan verdict and approver (if any).

In this mode the "quarantine" is really **audit + retention curation**, not an install-time block. The protection it gives you:

- A signed, queryable audit trail of every version that reaches prod, including the Inspector verdict and (when triggered) the human approver.
- A curated set of prod-pinned versions that survives even if staging or upstream public registries change.
- A latency-free, transparent developer onboarding experience — friction-free is the only kind of policy that survives contact with deadline pressure.

What it explicitly **does not** give you: a hard block at install time on uncached versions. The first developer to need a brand-new version gets it immediately via the upstream chain; the scan and human review happen concurrently. If a critical finding lands after the version has already been used in a build, the audit trail tells you who used it, and the rejection record blocks the *promotion-to-prod* (so future installs after staging eviction will fail), but the bytes were already on developer machines.

For most teams adopting CodeArtifact as a defense-in-depth layer on top of client-side cooldowns (pnpm `minimumReleaseAge`, uv `--exclude-newer`, Renovate/Dependabot cooldowns, ...), this trade is the right one. The client-side cooldown is your first line; this module's audit and retention curation is your second.

### Handling yanked / unpublished / malicious packages

CodeArtifact treats a cached package version as canonical and never re-fetches it. Once `left-pad@1.3.0` lives in staging, an upstream yank, deprecation, security removal, or post-publication malware advisory has no effect on what your consumers see — staging keeps serving the cached bytes via the upstream chain. **This is the most operationally significant caveat of the soft-gate model**, and the module ships an opt-out-able compensating control.

The `yank_detection` block (under `var.pipeline`) wires up a Lambda that runs on an EventBridge schedule (default hourly), enumerates every version cached in staging, and checks each against:

- **Upstream registry.** PyPI's PEP 592 yank metadata, npm's `deprecated` field + missing-from-packument detection (the closest equivalents).
- **OSV.dev.** Withdrawal advisories and malware reports (catches the cases npm/PyPI don't expose in their own metadata).

On a *newly observed* yank, unpublish, or malware verdict, the Lambda takes the configured response and always publishes to the existing SNS topic + writes a sibling audit row (`<name>-yank-audit`).

Defaults:

```hcl
pipeline = {
  # ...
  yank_detection = {
    enabled  = true
    schedule = "rate(1 hour)"
    sources  = ["upstream", "osv"]
    response = {
      yanked      = "unlist"   # PEP 592 yank / npm deprecate — pinned installs work, resolver skips
      unpublished = "dispose"  # upstream removed — downloads fail, audit metadata preserved
      malicious   = "dispose"  # OSV malware advisory — same; the audit trail is the point
    }
  }
}
```

Response semantics (applied to the version in both staging and prod, since promotion is a physical copy):

| Action    | CodeArtifact API                  | Effect on consumers                                                 |
|-----------|------------------------------------|----------------------------------------------------------------------|
| `alert`   | none (SNS + audit only)            | None — operator review only                                          |
| `unlist`  | `UpdatePackageVersionsStatus`      | Resolver skips for new resolutions; pinned lockfiles still install   |
| `dispose` | `DisposePackageVersions`           | Downloads fail with 404; metadata + audit history preserved          |
| `delete`  | `DeletePackageVersions`            | Hard-delete; destroys audit evidence; **never a recommended default** |

The Lambda is idempotent across runs — a state table (`<name>-yank-state`) records `last_checked` and `status` per version so an unchanged hourly check is a single DynamoDB read.

#### Operational notes

- **First run after enablement** flags every currently-yanked cached version as "newly observed" and runs the configured response on each. Recommend setting `response.* = "alert"` for the first hour, reviewing the SNS volume, then flipping to the real responses.
- **Lockfile build failures** can follow a `dispose` action: a service whose lockfile pins a now-disposed version will start 404-ing on install. That's the correct trade — the alternative is continuing to serve a confirmed-malicious or operator-yanked version. The SNS alert + `yank_audit_table_name` output tell you which version, when, and why.
- **`delete` is destructive.** It removes the audit metadata along with the artifact. The module exposes it as an option but never defaults to it; reserve for explicit operator action after triage.
- **OSV opt-out**: drop `"osv"` from `sources` if you don't want the soft runtime dependency on `api.osv.dev`. You lose malware-advisory coverage but keep upstream yank detection.
- **Custom schedule**: bump to `rate(15 minutes)` for higher-stakes environments, or `rate(1 day)` for low-traffic ones. The state table caches results between runs so cost scales with *new arrivals*, not total cache size.

### Proactive cache-fill

Soft-gate's main weakness is latency: a version isn't scanned until the *first developer* asks for it. The cooldown + scan + audit chain runs while that developer waits. Proactive cache-fill closes that gap — a scheduled Lambda pulls new upstream versions into staging on its own, so the pipeline runs ahead of demand and the audit row exists before anyone needs the version.

Two modes are stacked:

- **Follow-mode** (always on when `enabled = true`): for every package already cached in a staging repo, the Lambda queries the upstream registry on each cycle and fetches any new versions that aren't in staging yet. The watch set scales naturally with team usage — `lodash` you've already used gets every future `lodash` release pre-vetted; `crypto-js` you've never touched costs you nothing.
- **Allowlist-mode** (opt-in via `allowlist`): explicit `{format, name}` entries get the same treatment even if no one has installed them yet. Right for known-critical deps where you want vetted versions ready before the first install.

Defaults:

```hcl
pipeline = {
  # ...
  proactive_fill = {
    enabled             = true
    schedule            = "rate(1 hour)"
    include_prereleases = false
    max_fetches_per_run = 200
    allowlist = [
      # { format = "npm",  name = "@myorg/utils" },
      # { format = "pypi", name = "fastapi", include_prereleases = true },
    ]
  }
}
```

The fetch is triggered by an authenticated HTTPS GET against the staging repo's npm/pypi endpoint — exactly what `npm install` / `pip install` do, just programmatic. CodeArtifact handles the upstream pull as a side effect of the asset request, fires the existing `Package Version State Change` event, and the standard pipeline (scan → cooldown → optional approval → promote) takes over. **No changes to the existing pipeline** — proactive fill is just a different requester.

Each fetch writes an audit row into the existing promotion audit table with `record_type = "proactive_fill"` and `decision = "fetched"`. The subsequent promotion writes a separate row with `record_type = "promotion"`, so you can join the two by `(package_arn, version)` to see the full lifecycle.

#### Operational notes

- **Pre-releases are skipped by default.** PyPI `1.0.0a1`-style pre-releases and npm `1.0.0-alpha`-style suffixes are detected by regex and not fetched. Override globally with `include_prereleases = true`, or per-allowlist-entry. The regex is pragmatic (not exhaustive) — we'd rather skip an unusual stable release than pull every alpha into the cache.
- **First-run cost is real.** With an allowlist of 50 packages, the first run after enablement will fetch every published version of each (typically hundreds to thousands). The `max_fetches_per_run` cap (default 200) bounds the per-cycle work; on a busy first run, expect the cache to populate across several hourly cycles. The cap also protects against accidental allowlist explosions.
- **Inspector scan cost scales with churn**, not cache size. Each new version → one scan ($0.09). For a 1k-package follow-set with ~5 new versions/year average that's ~$450/year on top of reactive scanning. Tolerable for most; configure `schedule` longer (e.g. `rate(4 hours)`) if you want to throttle.
- **Disable per-pipeline if you don't want the runtime dep on PyPI/npm registries** — `proactive_fill = { enabled = false }`. Follow-mode goes away entirely, allowlist becomes inert.
- **Closure-mode (transitive completion) is not yet implemented.** Today the module fetches direct entries only; transitive deps still cache-fill reactively. The strict-mode "first dev waits on transitives" problem is unsolved here — see the Strict-mode section below.

### Strict mode (opt-in)

If you need install-time blocking — for example, a regulated environment where any uncached version must be human-reviewed before it touches a build — add explicit origin controls that block upstream traversal on prod:

```hcl
package_groups = [
  { repository = "prod", pattern = "/npm/",  upstream = "BLOCK" },
  { repository = "prod", pattern = "/pypi/", upstream = "BLOCK" },
]
```

Now prod refuses to serve any version it does not physically contain. A developer's first `npm install` of a net-new package 404s; they must either request a one-time `--registry <staging-endpoint>` install to seed staging (which kicks off the pipeline) or wait for a separate process to do so. After cooldown + scan + (optional) approval, the version is copied into prod and subsequent installs succeed.

This is a real operational shift: be ready for the "first dev to need package X waits up to 24h" pattern, and consider a CI hook that pre-warms staging from lockfile diffs at PR-open time so the cooldown overlaps with code review. Bulk-promote everything currently in staging before flipping this on, or every uncached version breaks at once.

The `expedite_command` output exists for both modes — it bypasses the cooldown for vetted security patches.

---

## Quickstart

```hcl
module "quarantine" {
  source  = "brian-l/codeartifact-quarantine/aws"
  version = "~> 0.1"

  name        = "platform-pkg-quarantine"
  domain_name = "platform"

  # CodeArtifact allows at most one external connection per repository, so each
  # public source gets its own staging repo. prod upstreams all of them.
  repositories = {
    staging-npm = {
      external_connection = "public:npmjs"
    }
    staging-pypi = {
      external_connection = "public:pypi"
    }
    prod = {
      upstreams = ["staging-npm", "staging-pypi"]
    }
  }

  pipeline = {
    source_repositories = ["staging-npm", "staging-pypi"]
    target_repository   = "prod"
    cooldown            = "PT24H"
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

Versioning follows semver — breaking changes to the variable schema bump MAJOR.

The module assumes you have:

- An AWS account where you can create CodeArtifact, IAM, Lambda, Step Functions, EventBridge, SQS, DynamoDB, KMS, and (optionally) Inspector v2 resources.
- An SNS topic for notifications/approval messages (you can pass the ARN of an existing topic, or create one with `aws_sns_topic`).

---

## Verifying a release

Because this module's purpose is supply-chain protection, its own supply chain is held to the same standard. Every release ships with a SLSA build provenance attestation signed by Sigstore via GitHub Actions OIDC. To verify a release tarball:

```bash
gh attestation verify terraform-aws-codeartifact-quarantine-<version>.tar.gz --owner brian-l
git tag -v v<version>     # signed tag verification
sha256sum -c terraform-aws-codeartifact-quarantine-<version>.tar.gz.sha256
```

Module-internal supply-chain practices are documented in [`SECURITY.md`](./SECURITY.md):

- Pre-commit hooks pinned to full commit SHAs (not tags)
- GitHub Actions pinned to full commit SHAs in `.github/workflows/`
- Releases attested via [SLSA Provenance v1.0](https://slsa.dev/spec/v1.0/provenance) through Sigstore
- Dependabot watches for dependency drift; security advisories bypass the weekly cadence
- Release tags are GPG/SSH-signed by the maintainer

## License

MIT — see [`LICENSE`](./LICENSE).

---

## Contributing

This is pre-1.0; the variable schema may change. Issues and PRs welcome but please open an issue to discuss before sending non-trivial patches.
