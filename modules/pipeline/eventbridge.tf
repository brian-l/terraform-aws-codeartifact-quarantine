# ---------------------------------------------------------------------------
# EventBridge rule on CodeArtifact package version state changes.
#
# We filter to:
#   - source: aws.codeartifact
#   - detail-type: CodeArtifact Package Version State Change
#   - detail.operationType: Created (new versions only)
#   - detail.repositoryName: <staging repo>
#   - detail.packageVersionState: Published
#
# Anything matching gets forwarded to the ingestion SQS queue.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "codeartifact_publish" {
  name        = "${var.name}-codeartifact-publish"
  description = "Fires on new package version ingestion into any of: ${join(", ", var.source_repo_names)}"

  event_pattern = jsonencode({
    source        = ["aws.codeartifact"]
    "detail-type" = ["CodeArtifact Package Version State Change"]
    detail = {
      domainName          = [var.domain_name]
      repositoryName      = var.source_repo_names
      operationType       = ["Created"]
      packageVersionState = ["Published"]
    }
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "to_sqs" {
  rule = aws_cloudwatch_event_rule.codeartifact_publish.name
  arn  = aws_sqs_queue.ingestion.arn
}
