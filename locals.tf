locals {
  # Map of ecosystem → staging repo name handling that ecosystem's public
  # external connection. Used to route proactive_fill allowlist entries to the
  # right repo. one() errors if multiple repos claim the same upstream — by
  # design (CodeArtifact allows one external_connection per repo, but two
  # different repos could each declare "public:npmjs"; that's a config bug).
  npm_staging_repo = one([
    for k, v in var.repositories : module.codeartifact.repositories[k].name
    if try(v.external_connection, null) == "public:npmjs"
  ])
  pypi_staging_repo = one([
    for k, v in var.repositories : module.codeartifact.repositories[k].name
    if try(v.external_connection, null) == "public:pypi"
  ])
  repository_formats = merge(
    local.npm_staging_repo == null ? {} : { npm = local.npm_staging_repo },
    local.pypi_staging_repo == null ? {} : { pypi = local.pypi_staging_repo },
  )
}

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

  # Tag keys present after merging module defaults with user-supplied tags.
  # Used by the required_tag_keys check below to validate compliance schemas.
  present_tag_keys = keys(local.tags)

  missing_required_tags = [
    for k in var.required_tag_keys : k
    if !contains(local.present_tag_keys, k)
  ]

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

check "required_tags_present" {
  assert {
    condition     = length(local.missing_required_tags) == 0
    error_message = "var.tags is missing required keys: ${join(", ", local.missing_required_tags)}. Add them or remove the keys from var.required_tag_keys."
  }
}
