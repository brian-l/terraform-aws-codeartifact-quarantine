# Simple example

Smallest viable configuration. Creates:

- A CodeArtifact domain named `simple-platform`
- Three repositories: `simple-pkg-quarantine-staging-npm` (external connection to public:npmjs), `simple-pkg-quarantine-staging-pypi` (external connection to public:pypi), and `simple-pkg-quarantine-prod` (consumer-facing, upstreams both staging repos)
- The full quarantine pipeline: EventBridge rule, SQS queue + DLQ, Step Functions state machine, 6 Lambda functions, DynamoDB audit table, KMS keys, IAM roles
- An SNS topic for alerts (no subscribers — subscribe Slack/email manually after apply)

## Run

```bash
cd examples/simple
terraform init
terraform plan
terraform apply
```

No build step needed — `archive_file` reads `lambda/common/` directly when assembling each handler's zip.

## Verify

```bash
# Get the prod registry URL
terraform output npm_registry

# Configure pnpm to use it (replace with your auth token from `aws codeartifact get-authorization-token`)
TOKEN=$(aws codeartifact get-authorization-token --domain simple-platform --query authorizationToken --output text)
npm config set registry "$(terraform output -raw npm_registry)"
npm config set //"$(terraform output -raw npm_registry | sed 's|https://||;s|/$||')":_authToken "$TOKEN"

# Install something benign and watch CloudWatch:
#   /aws/vendedlogs/states/simple-pkg-quarantine-quarantine  (state machine)
#   /aws/lambda/simple-pkg-quarantine-{ingestion,scan,promote,audit}
npm install --dry-run lodash
```

After 24h, the version appears in `simple-pkg-quarantine-prod` and a DynamoDB row lands in `simple-pkg-quarantine-audit` with `decision=promoted`.

## Test the expedite path

```bash
$(terraform output -raw expedite_command | sed 's|<PKG>|lodash|;s|<VER>|4.17.21|;s|<CVE/ticket>|smoke-test|')
```

Watch the state machine in the AWS console — it will skip the Wait state and go straight to Scan.

## Destroy

```bash
terraform destroy
```

Note: CodeArtifact retains 30 days of cached external packages by default. The `aws_codeartifact_domain` won't destroy until all repositories are empty; the AWS provider handles this with `force_destroy` on the repository (not yet wired in this module — TODO).
