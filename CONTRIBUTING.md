# Contributing

## Local setup

```bash
# Install pre-commit (one-time)
pip install pre-commit

# Install the hooks into your local repo
pre-commit install

# Optionally run against all files (slow first time — downloads tflint, trivy, etc.)
pre-commit run --all-files
```

After install, every `git commit` will run:

- `terraform fmt`, `validate`, `tflint`, `trivy`, `docs` over the TF files
- `ruff` + `mypy` over the Lambda handlers
- Generic hygiene checks (trailing whitespace, large files, merge conflict markers, private keys)
- `typos` over prose

If a hook auto-fixes (e.g., `terraform fmt`, `ruff --fix`), re-stage and commit again. If a hook reports an unfixable issue, fix the underlying code.

## Build before applying

The Lambda handlers reference shared code under `lambda/common/`. Each handler's
zip is built by Terraform's `archive_file`, which only follows files in its
`source_dir`. So before `terraform apply` (or `terraform plan` for the example),
run:

```bash
make -C lambda all
```

This copies `lambda/common/` into each handler dir. Re-run after any change to
the shared utilities.

## Module structure

See [`PLAN.md`](./PLAN.md) for the architecture and roadmap. Key invariants:

- The root module composes three submodules: `codeartifact/`, `pipeline/`, `inspector/`.
- The `codeartifact/` submodule owns all CodeArtifact resources and origin controls.
- The `pipeline/` submodule owns all EventBridge / SQS / Step Functions / Lambda / DynamoDB.
- Lambda handlers import only from the stdlib + boto3 (provided by the Lambda runtime).
- Anything that would force adopters to inherit our opinions (specific naming, Slack integration, package patterns) goes through a variable, not a hardcoded value.

## Adding a new variable

1. Add the variable to root `variables.tf` with `description`, `type`, optional `default`, and `validation` blocks for any non-trivial constraint.
2. Pass through to the relevant submodule in `main.tf`.
3. Add the corresponding variable declaration in the submodule's `variables.tf`.
4. Update `PLAN.md` if the addition affects the public interface significantly.
5. Update the relevant example(s) under `examples/` to demonstrate the new variable when appropriate.

## Releasing

Versions follow semver. Breaking changes to the variable schema bump MAJOR.

1. Update `CHANGELOG.md`.
2. Tag `vX.Y.Z` on `main`.
3. GitHub Actions publishes to the Terraform Registry (TODO once `.github/workflows/release.yml` lands).
