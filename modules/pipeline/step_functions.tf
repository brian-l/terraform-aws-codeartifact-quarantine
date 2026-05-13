# ---------------------------------------------------------------------------
# Step Functions state machine — the quarantine orchestrator.
#
# States:
#   1. CheckSkipCooldown — if input has skipCooldown=true (expedite path),
#      skip the Wait. Otherwise go to Cooldown.
#   2. Cooldown          — Wait state with var.cooldown duration.
#   3. Scan              — invoke scan Lambda; returns {decision: clean|findings|blocked}.
#   4. EvaluateDecision  — Choice state branching on scan output.
#   5. AwaitApproval     — Task with waitForTaskToken; resumed by approve Lambda.
#                          Only reached when required_when=findings + findings present,
#                          or required_when=always.
#   6. Promote           — invoke promote Lambda to copy-package-versions.
#   7. Audit             — record outcome in DynamoDB.
#   8. Terminal states   — Succeed / Fail.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Dedicated KMS key for the Step Functions log group.
#
# The log group can contain Inspector findings, package metadata, and (when
# logging.include_execution_data = true) the SFN task token. Encrypting the
# log group with a customer-managed key requires (a) the key, (b) a key
# policy allowing CloudWatch Logs to encrypt/decrypt with a tight encryption
# context, and (c) referencing kms_key_id on the log group.
# ---------------------------------------------------------------------------

resource "aws_kms_key" "sfn_logs" {
  description             = "Encryption for ${var.name} Step Functions log group"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.sfn_logs_kms.json
  tags                    = var.tags
}

resource "aws_kms_alias" "sfn_logs" {
  name          = "alias/${var.name}-sfn-logs"
  target_key_id = aws_kms_key.sfn_logs.id
}

data "aws_iam_policy_document" "sfn_logs_kms" {
  # Root account default access — required so the key can be managed by IAM
  # admins. Without this statement the key becomes unmanageable.
  statement {
    sid       = "EnableRoot"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  # CloudWatch Logs service uses this key to encrypt the log group. Scoped via
  # encryption context to *this specific log group ARN*, so the key cannot be
  # used to encrypt other log groups in the account.
  statement {
    sid = "AllowCloudWatchLogs"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${data.aws_region.current.region}.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${data.aws_partition.current.partition}:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/vendedlogs/states/${var.name}-quarantine"]
    }
  }
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${var.name}-quarantine"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.sfn_logs.arn
  tags              = var.tags
}

resource "aws_sfn_state_machine" "quarantine" {
  name     = "${var.name}-quarantine"
  role_arn = aws_iam_role.sfn.arn

  type = "STANDARD"

  definition = templatefile("${path.module}/state_machine.json.tftpl", {
    scan_lambda_arn    = aws_lambda_function.scan.arn
    promote_lambda_arn = aws_lambda_function.promote.arn
    audit_lambda_arn   = aws_lambda_function.audit.arn
    cooldown_seconds   = local.cooldown_seconds
    approval_required  = var.approval.required_when
    approval_timeout   = var.approval.timeout
    notification_arn   = local.notification_arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = var.logging.include_execution_data
    level                  = var.logging.level
  }

  tracing_configuration {
    enabled = true
  }

  tags = var.tags
}
