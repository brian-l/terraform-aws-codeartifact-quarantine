# ---------------------------------------------------------------------------
# Yank / unpublish / malware-advisory detection.
#
# A scheduled Lambda enumerates cached package versions across the staging
# repos, checks each against the configured sources (upstream registry +
# OSV.dev), and on a newly observed yank takes the configured response
# (alert / unlist / dispose / delete). See
# lambda/yank_check/handler.py for the full handler contract.
#
# All resources here are gated on var.yank_detection.enabled. When disabled,
# the feature creates zero AWS resources.
# ---------------------------------------------------------------------------

locals {
  yank_enabled = var.yank_detection.enabled
}

# ---- State table -----------------------------------------------------------
# Tracks last_checked + status per (package_arn, version) so the hourly run
# only re-checks versions whose status may have changed. Composite key matches
# the package_arn schema used in the promotion audit table for joinability.

resource "aws_dynamodb_table" "yank_state" {
  count = local.yank_enabled ? 1 : 0

  name         = "${var.name}-yank-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "package_arn"
  range_key    = "version"

  attribute {
    name = "package_arn"
    type = "S"
  }
  attribute {
    name = "version"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.audit.arn
  }

  tags = var.tags
}

# ---- Sibling audit table ---------------------------------------------------
# Schema mirrors the promotion audit table (PK package_arn, SK version_ts) but
# kept as a separate table — columns diverge enough that a record_type
# discriminator on a shared table would be uglier than the duplication here.

resource "aws_dynamodb_table" "yank_audit" {
  count = local.yank_enabled ? 1 : 0

  name         = "${var.name}-yank-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "package_arn"
  range_key    = "version_ts"

  attribute {
    name = "package_arn"
    type = "S"
  }
  attribute {
    name = "version_ts"
    type = "S"
  }
  attribute {
    name = "status"
    type = "S"
  }

  global_secondary_index {
    name            = "status-version_ts-index"
    hash_key        = "status"
    range_key       = "version_ts"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.audit.arn
  }

  tags = var.tags
}

# ---- Lambda zip ------------------------------------------------------------

data "archive_file" "yank_check" {
  count = local.yank_enabled ? 1 : 0

  type        = "zip"
  output_path = "${path.module}/.terraform.tmp/yank_check.zip"

  source {
    content  = file("${local.lambda_root}/yank_check/handler.py")
    filename = "handler.py"
  }
  dynamic "source" {
    for_each = local.common_files
    content {
      content  = file("${local.lambda_root}/common/${source.value}")
      filename = "common/${source.value}"
    }
  }
}

resource "aws_cloudwatch_log_group" "yank_check" {
  count = local.yank_enabled ? 1 : 0

  name              = "/aws/lambda/${var.name}-yank-check"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# ---- IAM -------------------------------------------------------------------

resource "aws_iam_role" "yank_check" {
  count = local.yank_enabled ? 1 : 0

  name               = "${var.name}-yank-check"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "yank_check" {
  count = local.yank_enabled ? 1 : 0

  # Enumerate every package version in staging + apply lifecycle actions in
  # both staging and prod (promoted copies live in prod, not staging).
  statement {
    sid = "CodeArtifactList"
    actions = [
      "codeartifact:ListPackages",
      "codeartifact:ListPackageVersions",
      "codeartifact:DescribePackageVersion",
    ]
    resources = concat(
      var.source_repo_arns,
      [var.target_repo_arn],
      local.source_package_arn_wildcards,
      [local.target_package_arn_wildcard],
    )
  }

  statement {
    sid = "CodeArtifactLifecycle"
    actions = [
      "codeartifact:UpdatePackageVersionsStatus",
      "codeartifact:DisposePackageVersions",
      "codeartifact:DeletePackageVersions",
    ]
    resources = concat(
      local.source_package_arn_wildcards,
      [local.target_package_arn_wildcard],
    )
  }

  statement {
    sid       = "DomainAuth"
    actions   = ["codeartifact:GetAuthorizationToken"]
    resources = [local.domain_arn]
  }

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

  statement {
    sid = "StateAndAudit"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]
    resources = [
      aws_dynamodb_table.yank_state[0].arn,
      aws_dynamodb_table.yank_audit[0].arn,
    ]
  }

  statement {
    sid       = "AuditKMS"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.audit.arn]
  }

  statement {
    sid       = "Notify"
    actions   = ["sns:Publish"]
    resources = [local.notification_arn]
  }

  # When the module manages the notifications topic + CMK, the yank Lambda
  # also needs key access to publish encrypted messages — same pattern as the
  # SFN execution role.
  dynamic "statement" {
    for_each = local.create_notification_topic ? [1] : []
    content {
      sid       = "NotifyKMS"
      actions   = ["kms:GenerateDataKey*", "kms:Decrypt"]
      resources = [aws_kms_key.notifications[0].arn]
    }
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "yank_check" {
  count = local.yank_enabled ? 1 : 0

  role   = aws_iam_role.yank_check[0].id
  policy = data.aws_iam_policy_document.yank_check[0].json
}

# ---- Lambda function -------------------------------------------------------

resource "aws_lambda_function" "yank_check" {
  count = local.yank_enabled ? 1 : 0

  function_name = "${var.name}-yank-check"
  role          = aws_iam_role.yank_check[0].arn
  runtime       = local.lambda_runtime
  handler       = "handler.lambda_handler"

  filename         = data.archive_file.yank_check[0].output_path
  source_code_hash = data.archive_file.yank_check[0].output_base64sha256

  # Worst-case wall time for a large staging cache: 10k versions × ~200ms
  # serial = 33min. With concurrency=10 → ~3.3min. Leave generous headroom
  # but stay well under the 15min Lambda hard limit.
  timeout     = 600
  memory_size = 512

  environment {
    variables = merge(local.common_env, {
      YANK_STATE_TABLE          = aws_dynamodb_table.yank_state[0].name
      YANK_AUDIT_TABLE          = aws_dynamodb_table.yank_audit[0].name
      YANK_SOURCES              = join(",", var.yank_detection.sources)
      YANK_RESPONSE_YANKED      = var.yank_detection.response.yanked
      YANK_RESPONSE_UNPUBLISHED = var.yank_detection.response.unpublished
      YANK_RESPONSE_MALICIOUS   = var.yank_detection.response.malicious
      NOTIFICATION_TOPIC_ARN    = local.notification_arn
    })
  }

  depends_on = [aws_cloudwatch_log_group.yank_check]
  tags       = var.tags
}

# ---- EventBridge schedule --------------------------------------------------

resource "aws_cloudwatch_event_rule" "yank_check" {
  count = local.yank_enabled ? 1 : 0

  name                = "${var.name}-yank-check"
  description         = "Scheduled yank / unpublish / malware-advisory check for ${var.name}"
  schedule_expression = var.yank_detection.schedule
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "yank_check" {
  count = local.yank_enabled ? 1 : 0

  rule = aws_cloudwatch_event_rule.yank_check[0].name
  arn  = aws_lambda_function.yank_check[0].arn
}

resource "aws_lambda_permission" "yank_check_eventbridge" {
  count = local.yank_enabled ? 1 : 0

  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.yank_check[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.yank_check[0].arn
}
