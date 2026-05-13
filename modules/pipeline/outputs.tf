output "state_machine_arn" {
  value = aws_sfn_state_machine.quarantine.arn
}

output "audit_table_name" {
  value = aws_dynamodb_table.audit.name
}

output "audit_table_arn" {
  value = aws_dynamodb_table.audit.arn
}

output "sqs_queue_arn" {
  value = aws_sqs_queue.ingestion.arn
}

output "sqs_dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "expedite_lambda_arn" {
  value = aws_lambda_function.expedite.arn
}

output "expedite_lambda_function_name" {
  value = aws_lambda_function.expedite.function_name
}

output "approval_lambda_arn" {
  value = aws_lambda_function.approve.arn
}

output "notification_topic_arn" {
  description = "Resolved ARN of the SNS topic the state machine publishes to. Module-created when var.approval.notification_arn was null, otherwise the consumer-supplied value."
  value       = local.notification_arn
}

output "notification_topic_kms_key_arn" {
  description = "KMS CMK ARN for the module-managed notifications topic. Null when the topic was supplied by the consumer."
  value       = try(aws_kms_key.notifications[0].arn, null)
}
