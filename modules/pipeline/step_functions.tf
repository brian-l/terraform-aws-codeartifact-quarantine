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

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${var.name}-quarantine"
  retention_in_days = var.log_retention_days
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
    cooldown_duration  = var.cooldown
    approval_required  = var.approval.required_when
    approval_timeout   = var.approval.timeout
    notification_arn   = var.approval.notification_arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }

  tags = var.tags
}
