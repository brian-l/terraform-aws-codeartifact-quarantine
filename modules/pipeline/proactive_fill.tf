# ---------------------------------------------------------------------------
# Proactive cache-fill.
#
# A scheduled Lambda enumerates staging repos + a configured allowlist, queries
# upstream registries for available versions, diffs against staging, and pulls
# any missing versions by issuing an authenticated HTTPS GET against the
# staging repo's npm/pypi endpoint (which is what `npm install` / `pip install`
# do — the bytes are cached server-side by CodeArtifact as a side effect of
# the request).
#
# The fetched version flows through the existing EventBridge → SQS → SFN
# pipeline. No additional state machine or new tables required.
#
# Each fetch writes an audit row into the existing promotion audit table with
# `record_type = "proactive_fill"`. The eventual promotion writes a separate
# row with `record_type = "promotion"`.
# ---------------------------------------------------------------------------

locals {
  proactive_enabled = var.proactive_fill.enabled
}

# ---- Lambda zip ------------------------------------------------------------

data "archive_file" "proactive_fill" {
  count = local.proactive_enabled ? 1 : 0

  type        = "zip"
  output_path = "${path.module}/.terraform.tmp/proactive_fill.zip"

  source {
    content  = file("${local.lambda_root}/proactive_fill/handler.py")
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

resource "aws_cloudwatch_log_group" "proactive_fill" {
  count = local.proactive_enabled ? 1 : 0

  name              = "/aws/lambda/${var.name}-proactive-fill"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# ---- IAM -------------------------------------------------------------------

resource "aws_iam_role" "proactive_fill" {
  count = local.proactive_enabled ? 1 : 0

  name               = "${var.name}-proactive-fill"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "proactive_fill" {
  count = local.proactive_enabled ? 1 : 0

  # Discover what's already in staging and where its endpoint lives.
  statement {
    sid = "CodeArtifactDiscover"
    actions = [
      "codeartifact:ListPackages",
      "codeartifact:ListPackageVersions",
      "codeartifact:DescribeRepository",
      "codeartifact:GetRepositoryEndpoint",
    ]
    resources = concat(
      var.source_repo_arns,
      local.source_package_arn_wildcards,
    )
  }

  # Fetch the bearer token used to authenticate the HTTPS GET against the
  # staging endpoint. The token is short-lived (we request 12h, well over the
  # Lambda's 15min max) and is the same primitive `aws codeartifact login` uses.
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

  # Audit rows land in the existing promotion audit table (record_type
  # discriminator separates proactive-fill rows from promotion rows).
  statement {
    sid       = "AuditWrite"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.audit.arn]
  }

  statement {
    sid       = "AuditKMS"
    actions   = ["kms:Encrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.audit.arn]
  }

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

resource "aws_iam_role_policy" "proactive_fill" {
  count = local.proactive_enabled ? 1 : 0

  role   = aws_iam_role.proactive_fill[0].id
  policy = data.aws_iam_policy_document.proactive_fill[0].json
}

# ---- Lambda function -------------------------------------------------------

resource "aws_lambda_function" "proactive_fill" {
  count = local.proactive_enabled ? 1 : 0

  function_name = "${var.name}-proactive-fill"
  role          = aws_iam_role.proactive_fill[0].arn
  runtime       = local.lambda_runtime
  handler       = "handler.lambda_handler"

  filename         = data.archive_file.proactive_fill[0].output_path
  source_code_hash = data.archive_file.proactive_fill[0].output_base64sha256

  # 200 fetches/run × ~2s/fetch ≈ 400s in the worst case; we cap at 200 by
  # default and serialise to keep upstream rate-limit friendly. Bump to 900s
  # if you raise max_fetches_per_run substantially.
  timeout     = 600
  memory_size = 512

  environment {
    variables = merge(local.common_env, {
      PROACTIVE_FILL_ALLOWLIST_JSON      = jsonencode(var.proactive_fill.allowlist)
      PROACTIVE_FILL_INCLUDE_PRERELEASES = tostring(var.proactive_fill.include_prereleases)
      PROACTIVE_FILL_MAX_FETCHES         = tostring(var.proactive_fill.max_fetches_per_run)
      PROACTIVE_FILL_REPOS_BY_FORMAT     = jsonencode(var.repository_formats)
    })
  }

  depends_on = [aws_cloudwatch_log_group.proactive_fill]
  tags       = var.tags
}

# ---- EventBridge schedule --------------------------------------------------

resource "aws_cloudwatch_event_rule" "proactive_fill" {
  count = local.proactive_enabled ? 1 : 0

  name                = "${var.name}-proactive-fill"
  description         = "Scheduled proactive cache-fill for ${var.name}"
  schedule_expression = var.proactive_fill.schedule
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "proactive_fill" {
  count = local.proactive_enabled ? 1 : 0

  rule = aws_cloudwatch_event_rule.proactive_fill[0].name
  arn  = aws_lambda_function.proactive_fill[0].arn
}

resource "aws_lambda_permission" "proactive_fill_eventbridge" {
  count = local.proactive_enabled ? 1 : 0

  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.proactive_fill[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.proactive_fill[0].arn
}
