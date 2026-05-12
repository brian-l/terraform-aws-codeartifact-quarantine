# ---------------------------------------------------------------------------
# SQS queue + DLQ bridging EventBridge to the ingestion Lambda.
#
# Why SQS instead of EventBridge → Lambda direct: gives us a DLQ, replay,
# and graceful degradation if the ingestion Lambda fails repeatedly.
# ---------------------------------------------------------------------------

resource "aws_kms_key" "sqs" {
  description             = "Encryption for ${var.name} ingestion queues"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = var.tags
}

resource "aws_kms_alias" "sqs" {
  name          = "alias/${var.name}-sqs"
  target_key_id = aws_kms_key.sqs.id
}

resource "aws_sqs_queue" "dlq" {
  name                              = "${var.name}-ingestion-dlq"
  message_retention_seconds         = 1209600 # 14 days
  kms_master_key_id                 = aws_kms_key.sqs.arn
  kms_data_key_reuse_period_seconds = 300

  tags = var.tags
}

resource "aws_sqs_queue" "ingestion" {
  name                              = "${var.name}-ingestion"
  message_retention_seconds         = 345600 # 4 days
  visibility_timeout_seconds        = 180    # > ingestion Lambda timeout (30s) by safety margin
  kms_master_key_id                 = aws_kms_key.sqs.arn
  kms_data_key_reuse_period_seconds = 300

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 5
  })

  tags = var.tags
}

# Allow EventBridge to deliver to the queue.
data "aws_iam_policy_document" "ingestion_queue_policy" {
  statement {
    sid     = "AllowEventBridge"
    actions = ["sqs:SendMessage"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    resources = [aws_sqs_queue.ingestion.arn]
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.codeartifact_publish.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "ingestion" {
  queue_url = aws_sqs_queue.ingestion.id
  policy    = data.aws_iam_policy_document.ingestion_queue_policy.json
}
