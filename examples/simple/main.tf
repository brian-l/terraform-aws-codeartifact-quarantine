# Minimal example: staging + prod, 24h cooldown, Inspector scan, no approval.
#
# Operating model: soft-gate (the recommended default). prod transparently
# upstreams from staging, so a developer's first `npm install` of a net-new
# package succeeds immediately via the upstream chain. The pipeline curates
# which versions are physically *retained* in prod, scans them, and writes an
# audit record per promotion. See README → "Developer experience" for the
# trade-offs vs. strict install-time blocking (which requires adding
# `package_groups` entries with `upstream = "BLOCK"` on the prod repo).
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
