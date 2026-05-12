locals {
  # Canonical tag set merged into every resource.
  tags = merge(
    {
      "module"      = "terraform-aws-codeartifact-quarantine"
      "module-name" = var.name
    },
    var.tags,
  )

  # Cross-check pipeline source/target against the repositories map.
  sources_valid = alltrue([
    for k in var.pipeline.source_repositories : contains(keys(var.repositories), k)
  ])
  target_repo_valid = contains(keys(var.repositories), var.pipeline.target_repository)

  # Pre-check assertions surface as plan-time errors (Terraform 1.5+ check blocks).
  # Validation lives here rather than in variable blocks so we can cross-reference
  # multiple variables in a single condition.
}

check "pipeline_repos_exist" {
  assert {
    condition     = local.sources_valid && local.target_repo_valid
    error_message = "All entries in pipeline.source_repositories and pipeline.target_repository must be keys in var.repositories."
  }
}
