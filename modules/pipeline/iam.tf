# ---------------------------------------------------------------------------
# IAM roles for each Lambda. Least-privilege per function.
#
# CodeArtifact's IAM model splits across three resource types:
#   - Domain:     arn:<part>:codeartifact:<region>:<account>:domain/<domain>
#   - Repository: arn:<part>:codeartifact:<region>:<account>:repository/<domain>/<repo>
#   - Package:    arn:<part>:codeartifact:<region>:<account>:package/<domain>/<repo>/<fmt>/<ns>/<name>
#
# Most "read/write package" actions (DescribePackageVersion, CopyPackageVersions,
# PublishPackageVersion, ...) require the *package* ARN. Repository ARNs only
# cover repo-level actions (ReadFromRepository, GetRepositoryEndpoint).
# `GetAuthorizationToken` is *domain*-scoped. Earlier iterations of this module
# scoped only to repo ARNs and silently failed at runtime; the locals below
# derive the package wildcards and domain ARN from the inputs we already have.
# ---------------------------------------------------------------------------

locals {
  # arn:...:repository/<domain>/<repo> → arn:...:package/<domain>/<repo>/*
  source_package_arn_wildcards = [
    for arn in var.source_repo_arns : "${replace(arn, ":repository/", ":package/")}/*"
  ]
  target_package_arn_wildcard = "${replace(var.target_repo_arn, ":repository/", ":package/")}/*"

  domain_arn = "arn:${data.aws_partition.current.partition}:codeartifact:${data.aws_region.current.region}:${var.domain_owner}:domain/${var.domain_name}"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ---- Ingestion: read SQS, start SFN execution ------------------------------

resource "aws_iam_role" "ingestion" {
  name               = "${var.name}-ingestion"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "ingestion" {
  statement {
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.ingestion.arn]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.sqs.arn]
  }
  statement {
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.quarantine.arn]
  }
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ingestion" {
  role   = aws_iam_role.ingestion.id
  policy = data.aws_iam_policy_document.ingestion.json
}

# ---- Scan: query Inspector + optionally invoke custom scanner Lambda ------

resource "aws_iam_role" "scan" {
  name               = "${var.name}-scan"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "scan" {
  statement {
    sid = "InspectorFindings"
    actions = [
      "inspector2:ListFindings",
      "inspector2:BatchGetFindingDetails",
    ]
    resources = ["*"]
  }

  # Optional: when scanner.type = "lambda", invoke the consumer-provided scanner.
  dynamic "statement" {
    for_each = var.scanner.type == "lambda" && var.scanner.lambda_arn != null ? [1] : []
    content {
      sid       = "InvokeCustomScanner"
      actions   = ["lambda:InvokeFunction"]
      resources = [var.scanner.lambda_arn]
    }
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "scan" {
  role   = aws_iam_role.scan.id
  policy = data.aws_iam_policy_document.scan.json
}

# ---- Promote: copy-package-versions from staging to prod ------------------

resource "aws_iam_role" "promote" {
  name               = "${var.name}-promote"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "promote" {
  # Domain-level: getting an auth token. CopyPackageVersions internally needs this.
  statement {
    sid       = "DomainAuth"
    actions   = ["codeartifact:GetAuthorizationToken"]
    resources = [local.domain_arn]
  }

  # Repository-level: reading from source, describing endpoints, etc.
  statement {
    sid = "RepositoryRead"
    actions = [
      "codeartifact:ReadFromRepository",
      "codeartifact:GetRepositoryEndpoint",
      "codeartifact:DescribeRepository",
    ]
    resources = concat(var.source_repo_arns, [var.target_repo_arn])
  }

  # Package-level: the actual copy and the metadata calls it triggers.
  # CopyPackageVersions requires the action on BOTH source and destination
  # packages; we list them together.
  statement {
    sid = "PackageReadWrite"
    actions = [
      "codeartifact:CopyPackageVersions",
      "codeartifact:DescribePackageVersion",
      "codeartifact:GetPackageVersionAsset",
      "codeartifact:GetPackageVersionReadme",
      "codeartifact:PublishPackageVersion",
      "codeartifact:PutPackageMetadata",
      "codeartifact:ListPackageVersions",
      "codeartifact:ListPackageVersionAssets",
    ]
    resources = concat(
      local.source_package_arn_wildcards,
      [local.target_package_arn_wildcard],
    )
  }

  # Service-linked bearer token used by the boto3 CodeArtifact client.
  statement {
    sid       = "ServiceBearerToken"
    actions   = ["sts:GetServiceBearerToken"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "sts:AWSServiceName"
      values   = ["codeartifact.amazonaws.com"]
    }
  }

  # Domain KMS key for asset encryption/decryption.
  statement {
    sid       = "DomainKMS"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.domain_kms_key_arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "promote" {
  role   = aws_iam_role.promote.id
  policy = data.aws_iam_policy_document.promote.json
}

# ---- Audit: DynamoDB writes ------------------------------------------------

resource "aws_iam_role" "audit" {
  name               = "${var.name}-audit"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "audit" {
  statement {
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.audit.arn]
  }
  statement {
    actions   = ["kms:Encrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.audit.arn]
  }
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "audit" {
  role   = aws_iam_role.audit.id
  policy = data.aws_iam_policy_document.audit.json
}

# ---- Expedite: start SFN with skip-cooldown ------------------------------

resource "aws_iam_role" "expedite" {
  name               = "${var.name}-expedite"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "expedite" {
  statement {
    sid       = "StartQuarantine"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.quarantine.arn]
  }

  # The expedite handler validates the named version exists in the source repo
  # before kicking off the SFN, so it needs DescribePackageVersion on packages.
  statement {
    sid = "VerifyPackageExists"
    actions = [
      "codeartifact:DescribePackageVersion",
      "codeartifact:ReadFromRepository",
    ]
    resources = concat(
      local.source_package_arn_wildcards,
      var.source_repo_arns,
    )
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "expedite" {
  role   = aws_iam_role.expedite.id
  policy = data.aws_iam_policy_document.expedite.json
}

# ---- Approve: SendTaskSuccess/Failure callbacks ---------------------------

resource "aws_iam_role" "approve" {
  name               = "${var.name}-approve"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "approve" {
  statement {
    actions   = ["states:SendTaskSuccess", "states:SendTaskFailure", "states:SendTaskHeartbeat"]
    resources = ["*"]
  }
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "approve" {
  role   = aws_iam_role.approve.id
  policy = data.aws_iam_policy_document.approve.json
}

# ---- Step Functions execution role -----------------------------------------

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.name}-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "sfn" {
  statement {
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.scan.arn,
      aws_lambda_function.promote.arn,
      aws_lambda_function.audit.arn,
    ]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [local.notification_arn]
  }

  # When the module manages the notifications topic + CMK, the SFN role also
  # needs to GenerateDataKey/Decrypt on the CMK to publish encrypted messages.
  # When consumers supply their own topic ARN, they own the corresponding KMS
  # permissions on their topic's key.
  dynamic "statement" {
    for_each = local.create_notification_topic ? [1] : []
    content {
      actions = [
        "kms:GenerateDataKey*",
        "kms:Decrypt",
      ]
      resources = [aws_kms_key.notifications[0].arn]
    }
  }
  statement {
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sfn" {
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}
