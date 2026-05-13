# Minimal example: staging + prod, 24h cooldown, Inspector scan, no approval.
#
# Run:
#   cd lambda && make
#   cd ../examples/simple
#   terraform init
#   terraform apply

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.50.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

# Tag schema enforced by the module via required_tag_keys below.
locals {
  tags = {
    data-classification = "internal"
    owner               = "platform-eng"
    cost-center         = "eng-platform"
    environment         = "sandbox"
  }
}

module "quarantine" {
  source = "../.."

  name        = "simple-pkg-quarantine"
  domain_name = "simple-platform"

  # CodeArtifact allows at most one external connection per repository, so each
  # public source gets its own staging repo. prod upstreams all of them.
  repositories = {
    staging-npm = {
      description         = "Quarantined npm ingestion"
      external_connection = "public:npmjs"
    }
    staging-pypi = {
      description         = "Quarantined PyPI ingestion"
      external_connection = "public:pypi"
    }
    prod = {
      description = "Consumer-facing registry (vetted only)"
      upstreams   = ["staging-npm", "staging-pypi"]
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
    # notification_arn left unset — the module creates a hardened SNS topic
    # (KMS-encrypted, publish locked to the SFN role). Subscribe consumers
    # (e.g. a Slack-bot Lambda) to module.quarantine.notification_topic_arn.
    approval = {
      required_when = "findings"
    }
  }

  # Tags applied to every taggable resource the module creates. The keys listed
  # in required_tag_keys are enforced at plan time — drop one and plan fails
  # with a clear message. Use this to encode an ISO 27001 / SOC2 tag schema.
  required_tag_keys = [
    "data-classification",
    "owner",
    "cost-center",
    "environment",
  ]

  tags = local.tags
}

# ── Outputs consumers wire into their .npmrc / pyproject.toml ──────────────

output "npm_registry" {
  description = "Set as `registry=` in .npmrc for npm/pnpm/yarn"
  value       = module.quarantine.repository_endpoints["prod"].npm
}

output "pypi_index_url" {
  description = "Set as `index-url` in pyproject.toml [tool.uv] / pip.conf"
  value       = module.quarantine.repository_endpoints["prod"].pypi
}

output "notification_topic_arn" {
  description = "SNS topic the state machine publishes approval requests and alerts to. Subscribe IAM-controlled compute (Lambda, SQS) here — see SECURITY.md."
  value       = module.quarantine.notification_topic_arn
}

output "expedite_command" {
  description = "Run this to manually expedite a security patch (skips cooldown)"
  value       = <<-EOT
    # For npm:
    aws lambda invoke \
      --function-name ${module.quarantine.expedite_lambda_function_name} \
      --payload '{"format":"npm","source_repository":"simple-pkg-quarantine-staging-npm","name":"<PKG>","version":"<VER>","reason":"<CVE/ticket>"}' \
      /dev/stdout

    # For PyPI:
    aws lambda invoke \
      --function-name ${module.quarantine.expedite_lambda_function_name} \
      --payload '{"format":"pypi","source_repository":"simple-pkg-quarantine-staging-pypi","name":"<PKG>","version":"<VER>","reason":"<CVE/ticket>"}' \
      /dev/stdout
  EOT
}
