output "domain_name" {
  description = "CodeArtifact domain name."
  value       = module.codeartifact.domain_name
}

output "domain_arn" {
  description = "CodeArtifact domain ARN."
  value       = module.codeartifact.domain_arn
}

output "domain_owner" {
  description = "Account ID that owns the CodeArtifact domain."
  value       = data.aws_caller_identity.current.account_id
}

output "domain_kms_key_arn" {
  description = "KMS key ARN used by the CodeArtifact domain."
  value       = module.codeartifact.domain_kms_key_arn
}

output "repositories" {
  description = "Map of repository key -> {name, arn, endpoint_npm, endpoint_pypi, endpoint_maven, endpoint_nuget}."
  value       = module.codeartifact.repositories
}

output "repository_endpoints" {
  description = "Map of repository key -> {npm, pypi, maven, nuget} endpoint URLs."
  value       = module.codeartifact.repository_endpoints
}

output "audit_table_name" {
  description = "DynamoDB table name for the audit trail."
  value       = module.pipeline.audit_table_name
}

output "audit_table_arn" {
  description = "DynamoDB table ARN for the audit trail."
  value       = module.pipeline.audit_table_arn
}

output "state_machine_arn" {
  description = "Step Functions state machine ARN. Used by manual expedite invocations."
  value       = module.pipeline.state_machine_arn
}

output "expedite_lambda_arn" {
  description = "ARN of the Lambda that triggers the expedite path. Invoke this for security patches."
  value       = module.pipeline.expedite_lambda_arn
}

output "expedite_lambda_function_name" {
  description = "Function name of the expedite Lambda (for `aws lambda invoke`)."
  value       = module.pipeline.expedite_lambda_function_name
}

output "approval_lambda_arn" {
  description = "ARN of the Lambda that handles SendTaskSuccess/Failure callbacks. Wire your Slack/Teams bot to invoke this."
  value       = module.pipeline.approval_lambda_arn
}

output "sqs_queue_arn" {
  description = "ARN of the SQS queue bridging EventBridge to the ingestion Lambda."
  value       = module.pipeline.sqs_queue_arn
}

output "sqs_dlq_arn" {
  description = "ARN of the dead-letter queue for failed event processing."
  value       = module.pipeline.sqs_dlq_arn
}
