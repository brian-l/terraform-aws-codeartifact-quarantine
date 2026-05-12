data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# KMS key for the CodeArtifact domain.
#
# CodeArtifact requires a KMS key for domain creation. We create a
# customer-managed key by default; consumers can override via var.domain_kms_key_arn.
# ---------------------------------------------------------------------------

resource "aws_kms_key" "domain" {
  count = var.domain_kms_key_arn == null ? 1 : 0

  description             = "CodeArtifact domain encryption for ${var.domain_name}"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = var.tags
}

resource "aws_kms_alias" "domain" {
  count = var.domain_kms_key_arn == null ? 1 : 0

  name          = "alias/${var.name}-codeartifact"
  target_key_id = aws_kms_key.domain[0].id
}

locals {
  domain_kms_key_arn = coalesce(
    var.domain_kms_key_arn,
    try(aws_kms_key.domain[0].arn, null),
  )
}

# ---------------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------------

resource "aws_codeartifact_domain" "this" {
  domain         = var.domain_name
  encryption_key = local.domain_kms_key_arn

  tags = var.tags
}

# ---------------------------------------------------------------------------
# Repositories
#
# The upstream block references the upstream repository by *self-reference* to
# another instance of this for_each resource: `aws_codeartifact_repository.
# this[upstream.value].repository`. That reference is what gives Terraform a
# graph edge between the dependent repo (e.g. prod) and its upstreams
# (e.g. staging-npm, staging-pypi).
#
# Without the self-reference, the `repository_name` would be a plain string
# interpolation Terraform can't trace, so it would create everything in
# parallel — and CodeArtifact rejects creation of a repo whose upstream
# doesn't yet exist.
#
# Terraform allows for_each self-references as long as the dependency graph is
# acyclic. Don't introduce cycles in var.repositories (A upstreams B AND B
# upstreams A).
# ---------------------------------------------------------------------------

resource "aws_codeartifact_repository" "this" {
  for_each = var.repositories

  domain      = aws_codeartifact_domain.this.domain
  repository  = "${var.name}-${each.key}"
  description = each.value.description

  # CodeArtifact allows at most one external connection per repository. We model
  # it as a single optional string (not a list) so the type signature reflects
  # the API constraint.
  dynamic "external_connections" {
    for_each = each.value.external_connection == null ? [] : [each.value.external_connection]
    content {
      external_connection_name = external_connections.value
    }
  }

  dynamic "upstream" {
    for_each = each.value.upstreams
    content {
      # Self-reference into the same for_each resource. Terraform reads this as
      # "this instance depends on instance[upstream.value]" and orders creates.
      repository_name = aws_codeartifact_repository.this[upstream.value].repository
    }
  }

  tags = var.tags

  depends_on = [aws_codeartifact_domain.this]
}

# ---------------------------------------------------------------------------
# Package group origin controls
#
# The dependency-confusion defense. Each rule constrains how versions of
# packages matching `pattern` can be added to `repository`.
# ---------------------------------------------------------------------------

# Uses the AWSCC provider because the legacy hashicorp/aws provider has not yet
# implemented aws_codeartifact_package_group. AWSCC is HashCorp-official and
# auto-generated from the AWS Cloud Control API, so coverage tracks AWS-side
# features closely. Track migration to the native aws provider once available.
resource "awscc_codeartifact_package_group" "this" {
  for_each = {
    for g in var.package_groups :
    "${g.repository}:${g.pattern}" => g
  }

  domain_name  = aws_codeartifact_domain.this.domain
  domain_owner = data.aws_caller_identity.current.account_id
  pattern      = each.value.pattern
  description  = "Managed by terraform-aws-codeartifact-quarantine"

  origin_configuration = {
    restrictions = {
      publish = {
        restriction_mode = each.value.publish
        repositories     = []
      }
      external_upstream = {
        restriction_mode = each.value.upstream
        repositories     = []
      }
      internal_upstream = {
        restriction_mode = each.value.upstream
        repositories     = []
      }
    }
  }

  depends_on = [aws_codeartifact_repository.this]
}

# ---------------------------------------------------------------------------
# Cross-account read access on the target (prod) repository.
#
# Consumers in other accounts assume into a read role; the repo resource policy
# allows the listed principals to ReadFromRepository and related read APIs.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "prod_read" {
  count = length(var.consumer_principals) > 0 && contains(keys(var.repositories), "prod") ? 1 : 0

  statement {
    sid = "AllowConsumerRead"
    principals {
      type        = "AWS"
      identifiers = var.consumer_principals
    }
    actions = [
      "codeartifact:ReadFromRepository",
      "codeartifact:GetAuthorizationToken",
      "codeartifact:GetRepositoryEndpoint",
      "codeartifact:ListPackages",
      "codeartifact:ListPackageVersions",
      "codeartifact:DescribePackageVersion",
      "codeartifact:GetPackageVersionAsset",
      "codeartifact:GetPackageVersionReadme",
    ]
    resources = ["*"]
  }
}

resource "aws_codeartifact_repository_permissions_policy" "prod_read" {
  count = length(var.consumer_principals) > 0 && contains(keys(var.repositories), "prod") ? 1 : 0

  domain          = aws_codeartifact_domain.this.domain
  repository      = aws_codeartifact_repository.this["prod"].repository
  policy_document = data.aws_iam_policy_document.prod_read[0].json
}

# ---------------------------------------------------------------------------
# Domain permissions policy: allow listed consumer principals to authenticate.
# Without this, repo policies alone aren't enough — domain auth is the entry point.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "domain_consumer" {
  count = length(var.consumer_principals) > 0 ? 1 : 0

  statement {
    sid = "AllowConsumerAuth"
    principals {
      type        = "AWS"
      identifiers = var.consumer_principals
    }
    actions = [
      "codeartifact:GetAuthorizationToken",
      "codeartifact:GetDomainPermissionsPolicy",
      "codeartifact:ListRepositoriesInDomain",
    ]
    resources = [aws_codeartifact_domain.this.arn]
  }

  statement {
    sid    = "AllowConsumerSTS"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = var.consumer_principals
    }
    actions   = ["sts:GetServiceBearerToken"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "sts:AWSServiceName"
      values   = ["codeartifact.amazonaws.com"]
    }
  }
}

resource "aws_codeartifact_domain_permissions_policy" "consumer" {
  count = length(var.consumer_principals) > 0 ? 1 : 0

  domain          = aws_codeartifact_domain.this.domain
  policy_document = data.aws_iam_policy_document.domain_consumer[0].json
}
