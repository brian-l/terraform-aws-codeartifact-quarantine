# Outputs consumers wire into their .npmrc / pyproject.toml.

output "npm_registry" {
  description = "Set as `registry=` in .npmrc for npm/pnpm/yarn"
  value       = module.quarantine.repository_endpoints["prod"].npm
}

output "pypi_index_url" {
  description = "Set as `index-url` in pyproject.toml [tool.uv] / pip.conf"
  value       = module.quarantine.repository_endpoints["prod"].pypi
}

output "notification_topic_arn" {
  description = "SNS topic the state machine publishes approval requests and alerts to. Subscribe IAM-controlled compute (Lambda, SQS) here — see SECURITY.md."
  value       = module.quarantine.notification_topic_arn
}

output "expedite_command" {
  description = "Run this to manually expedite a security patch (skips cooldown)"
  value       = <<-EOT
    # For npm:
    aws lambda invoke \
      --function-name ${module.quarantine.expedite_lambda_function_name} \
      --payload '{"format":"npm","source_repository":"simple-pkg-quarantine-staging-npm","name":"<PKG>","version":"<VER>","reason":"<CVE/ticket>"}' \
      /dev/stdout

    # For PyPI:
    aws lambda invoke \
      --function-name ${module.quarantine.expedite_lambda_function_name} \
      --payload '{"format":"pypi","source_repository":"simple-pkg-quarantine-staging-pypi","name":"<PKG>","version":"<VER>","reason":"<CVE/ticket>"}' \
      /dev/stdout
  EOT
}
