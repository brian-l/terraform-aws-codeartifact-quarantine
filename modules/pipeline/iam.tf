# ---------------------------------------------------------------------------
# IAM roles for each Lambda. Least-privilege per function.
# ---------------------------------------------------------------------------

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
    actions = [
      "inspector2:ListFindings",
      "inspector2:BatchGetFindingDetails",
    ]
    resources = ["*"]
  }
  statement {
    actions = [
      "codeartifact:DescribePackageVersion",
      "codeartifact:GetPackageVersionAsset",
    ]
    resources = flatten([
      for arn in var.source_repo_arns : [arn, "${arn}/*"]
    ])
  }
  dynamic "statement" {
    for_each = var.scanner.type == "lambda" && var.scanner.lambda_arn != null ? [1] : []
    content {
      actions   = ["lambda:InvokeFunction"]
      resources = [var.scanner.lambda_arn]
    }
  }
  statement {
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
  statement {
    actions = [
      "codeartifact:CopyPackageVersions",
      "codeartifact:DescribePackageVersion",
      "codeartifact:GetAuthorizationToken",
      "codeartifact:GetRepositoryEndpoint",
      "codeartifact:ReadFromRepository",
      "codeartifact:PublishPackageVersion",
      "codeartifact:PutPackageMetadata",
    ]
    resources = concat(
      flatten([for arn in var.source_repo_arns : [arn, "${arn}/*"]]),
      [var.target_repo_arn, "${var.target_repo_arn}/*"],
    )
  }
  statement {
    actions   = ["sts:GetServiceBearerToken"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "sts:AWSServiceName"
      values   = ["codeartifact.amazonaws.com"]
    }
  }
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.domain_kms_key_arn]
  }
  statement {
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
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.quarantine.arn]
  }
  statement {
    actions = ["codeartifact:DescribePackageVersion"]
    resources = flatten([
      for arn in var.source_repo_arns : [arn, "${arn}/*"]
    ])
  }
  statement {
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
    resources = [var.approval.notification_arn]
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
