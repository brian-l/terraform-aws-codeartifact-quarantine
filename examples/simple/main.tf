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

# Notifications: where the module emits approval requests and block alerts.
# In a real deployment, subscribe Slack/email/PagerDuty to this topic.
resource "aws_sns_topic" "security" {
  name = "codeartifact-quarantine-alerts"
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
    approval = {
      required_when    = "findings"
      notification_arn = aws_sns_topic.security.arn
    }
  }

  tags = {
    Environment = "sandbox"
    Owner       = "platform-eng"
  }
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
