output "domain_name" {
  description = "CodeArtifact domain name."
  value       = aws_codeartifact_domain.this.domain
}

output "domain_arn" {
  description = "CodeArtifact domain ARN."
  value       = aws_codeartifact_domain.this.arn
}

output "domain_kms_key_arn" {
  description = "KMS key ARN used to encrypt the CodeArtifact domain."
  value       = local.domain_kms_key_arn
}

output "repositories" {
  description = "Map of repo key -> {name, arn, endpoint_npm, endpoint_pypi, endpoint_maven, endpoint_nuget}."
  value = {
    for k, r in local.repositories : k => {
      name           = r.repository
      arn            = r.arn
      endpoint_npm   = "https://${r.domain_owner}-${r.domain}.d.codeartifact.${data.aws_region.current.name}.amazonaws.com/npm/${r.repository}/"
      endpoint_pypi  = "https://${r.domain_owner}-${r.domain}.d.codeartifact.${data.aws_region.current.name}.amazonaws.com/pypi/${r.repository}/simple/"
      endpoint_maven = "https://${r.domain_owner}-${r.domain}.d.codeartifact.${data.aws_region.current.name}.amazonaws.com/maven/${r.repository}/"
      endpoint_nuget = "https://${r.domain_owner}-${r.domain}.d.codeartifact.${data.aws_region.current.name}.amazonaws.com/nuget/${r.repository}/v3/index.json"
    }
  }
}

output "repository_endpoints" {
  description = "Map of repo key -> {npm, pypi, maven, nuget} endpoint URLs (shorthand for repositories[key].endpoint_*)."
  value = {
    for k, r in local.repositories : k => {
      npm   = "https://${r.domain_owner}-${r.domain}.d.codeartifact.${data.aws_region.current.name}.amazonaws.com/npm/${r.repository}/"
      pypi  = "https://${r.domain_owner}-${r.domain}.d.codeartifact.${data.aws_region.current.name}.amazonaws.com/pypi/${r.repository}/simple/"
      maven = "https://${r.domain_owner}-${r.domain}.d.codeartifact.${data.aws_region.current.name}.amazonaws.com/maven/${r.repository}/"
      nuget = "https://${r.domain_owner}-${r.domain}.d.codeartifact.${data.aws_region.current.name}.amazonaws.com/nuget/${r.repository}/v3/index.json"
    }
  }
}

data "aws_region" "current" {}

output "consumer_policy_document" {
  description = "JSON IAM policy granting read access to the prod repository. Attach to consumer roles inline, or use consumer_policy_arn for the managed policy."
  value       = data.aws_iam_policy_document.consumer.json
}

output "consumer_policy_arn" {
  description = "ARN of the managed IAM policy granting read access to the prod repository. Null when create_consumer_policy is false."
  value       = try(aws_iam_policy.consumer[0].arn, null)
}

output "consumer_policy_name" {
  description = "Name of the managed IAM policy. Null when create_consumer_policy is false."
  value       = try(aws_iam_policy.consumer[0].name, null)
}
