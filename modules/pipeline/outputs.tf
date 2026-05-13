output "state_machine_arn" {
  description = "ARN of the quarantine Step Functions state machine."
  value       = aws_sfn_state_machine.quarantine.arn
}

output "audit_table_name" {
  description = "Name of the DynamoDB audit table recording every promotion decision."
  value       = aws_dynamodb_table.audit.name
}

output "audit_table_arn" {
  description = "ARN of the DynamoDB audit table."
  value       = aws_dynamodb_table.audit.arn
}

output "sqs_queue_arn" {
  description = "ARN of the ingestion SQS queue (EventBridge target)."
  value       = aws_sqs_queue.ingestion.arn
}

output "sqs_dlq_arn" {
  description = "ARN of the ingestion dead-letter queue."
  value       = aws_sqs_queue.dlq.arn
}

output "expedite_lambda_arn" {
  description = "ARN of the expedite Lambda used to skip cooldown for an explicitly approved package version."
  value       = aws_lambda_function.expedite.arn
}

output "expedite_lambda_function_name" {
  description = "Function name of the expedite Lambda (use with `aws lambda invoke`)."
  value       = aws_lambda_function.expedite.function_name
}

output "approval_lambda_arn" {
  description = "ARN of the approval Lambda that resumes the state machine with a human decision."
  value       = aws_lambda_function.approve.arn
}

output "notification_topic_arn" {
  description = "Resolved ARN of the SNS topic the state machine publishes to. Module-created when var.approval.notification_arn was null, otherwise the consumer-supplied value."
  value       = local.notification_arn
}

output "notification_topic_kms_key_arn" {
  description = "KMS CMK ARN for the module-managed notifications topic. Null when the topic was supplied by the consumer."
  value       = try(aws_kms_key.notifications[0].arn, null)
}

output "yank_audit_table_name" {
  description = "Name of the sibling DynamoDB table recording yank/unpublish/malware detections. Null when yank_detection.enabled is false."
  value       = try(aws_dynamodb_table.yank_audit[0].name, null)
}

output "yank_audit_table_arn" {
  description = "ARN of the yank audit table. Null when yank_detection.enabled is false."
  value       = try(aws_dynamodb_table.yank_audit[0].arn, null)
}

output "yank_check_lambda_arn" {
  description = "ARN of the scheduled yank-check Lambda. Null when yank_detection.enabled is false."
  value       = try(aws_lambda_function.yank_check[0].arn, null)
}
