output "domain_name" {
  value = aws_codeartifact_domain.this.domain
}

output "domain_arn" {
  value = aws_codeartifact_domain.this.arn
}

output "domain_kms_key_arn" {
  value = local.domain_kms_key_arn
}

output "repositories" {
  description = "Map of repo key -> {name, arn, endpoint_npm, endpoint_pypi, endpoint_maven, endpoint_nuget}."
  value = {
    for k, r in aws_codeartifact_repository.this : k => {
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
    for k, r in aws_codeartifact_repository.this : k => {
      npm   = "https://${r.domain_owner}-${r.domain}.d.codeartifact.${data.aws_region.current.name}.amazonaws.com/npm/${r.repository}/"
      pypi  = "https://${r.domain_owner}-${r.domain}.d.codeartifact.${data.aws_region.current.name}.amazonaws.com/pypi/${r.repository}/simple/"
      maven = "https://${r.domain_owner}-${r.domain}.d.codeartifact.${data.aws_region.current.name}.amazonaws.com/maven/${r.repository}/"
      nuget = "https://${r.domain_owner}-${r.domain}.d.codeartifact.${data.aws_region.current.name}.amazonaws.com/nuget/${r.repository}/v3/index.json"
    }
  }
}

data "aws_region" "current" {}
