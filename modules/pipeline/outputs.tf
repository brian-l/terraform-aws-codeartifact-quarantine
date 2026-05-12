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
