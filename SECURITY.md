# Security Policy

## Reporting a vulnerability

If you find a security issue in this module — whether in the Terraform code, the Lambda handlers, the IAM policy templates, or the release artifacts — please report it privately:

1. Open a [GitHub Security Advisory](https://github.com/brian-l/terraform-aws-codeartifact-quarantine/security/advisories/new) for this repo, **or**
2. Email the maintainer (see the GitHub profile of `brian-l`).

Please **do not** open a public issue or pull request for security reports until a fix is available. Coordinated disclosure is appreciated; I'll aim to acknowledge reports within 5 business days and target a fix within 30 days for confirmed vulnerabilities, sooner for actively exploited ones.

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
