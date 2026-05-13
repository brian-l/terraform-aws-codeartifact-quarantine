# ---------------------------------------------------------------------------
# Notification SNS topic.
#
# Step Functions publishes here via the waitForTaskToken pattern. The message
# body contains the task token — see SECURITY.md for the access-control
# properties this topic must have.
#
# When var.approval.notification_arn is null (the default), the module creates
# a hardened topic: dedicated KMS CMK, topic policy locked to the SFN role,
# zero subscriptions. Consumers add their own IAM-controlled subscribers
# against the notification_topic_arn output.
#
# Override only when sharing a pre-existing topic across multiple pipelines —
# see SECURITY.md for the configuration that pre-existing topic must satisfy.
# ---------------------------------------------------------------------------

locals {
  create_notification_topic = var.approval.notification_arn == null
  notification_arn = coalesce(
    var.approval.notification_arn,
    try(aws_sns_topic.notifications[0].arn, ""),
  )
}

data "aws_iam_policy_document" "notifications_kms" {
  count = local.create_notification_topic ? 1 : 0

  statement {
    sid       = "EnableRoot"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  # The Step Functions role wraps message payloads in a per-message data key
  # before publishing to SNS. Without GenerateDataKey/Decrypt on the CMK,
  # Publish calls fail with KMSAccessDeniedException.
  statement {
    sid = "AllowSFNPublish"
    actions = [
      "kms:GenerateDataKey*",
      "kms:Decrypt",
    ]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.sfn.arn]
    }
  }
}

resource "aws_kms_key" "notifications" {
  count = local.create_notification_topic ? 1 : 0

  description             = "Encryption for ${var.name} approval notifications topic"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.notifications_kms[0].json
  tags                    = var.tags
}

resource "aws_kms_alias" "notifications" {
  count = local.create_notification_topic ? 1 : 0

  name          = "alias/${var.name}-notifications"
  target_key_id = aws_kms_key.notifications[0].id
}

resource "aws_sns_topic" "notifications" {
  count = local.create_notification_topic ? 1 : 0

  name              = "${var.name}-notifications"
  kms_master_key_id = aws_kms_key.notifications[0].arn

  tags = var.tags
}

# Restrict publish to the Step Functions role. Subscriptions are intentionally
# left to consumers (IAM-gated; no email/SMS) per SECURITY.md.
data "aws_iam_policy_document" "notifications_topic" {
  count = local.create_notification_topic ? 1 : 0

  statement {
    sid     = "AllowSFNPublish"
    actions = ["sns:Publish"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.sfn.arn]
    }
    resources = [aws_sns_topic.notifications[0].arn]
  }
}

resource "aws_sns_topic_policy" "notifications" {
  count = local.create_notification_topic ? 1 : 0

  arn    = aws_sns_topic.notifications[0].arn
  policy = data.aws_iam_policy_document.notifications_topic[0].json
}
