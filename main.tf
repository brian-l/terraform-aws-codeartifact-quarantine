module "codeartifact" {
  source = "./modules/codeartifact"

  name                = var.name
  domain_name         = var.domain_name
  domain_kms_key_arn  = var.domain_kms_key_arn
  repositories        = var.repositories
  package_groups      = var.package_groups
  consumer_principals = var.consumer_principals

  create_consumer_policy = var.create_consumer_policy
  consumer_policy_name   = var.consumer_policy_name

  tags = local.tags
}

module "pipeline" {
  source = "./modules/pipeline"

  name = var.name

  domain_name        = module.codeartifact.domain_name
  domain_owner       = data.aws_caller_identity.current.account_id
  source_repo_names  = [for k in var.pipeline.source_repositories : module.codeartifact.repositories[k].name]
  target_repo_name   = module.codeartifact.repositories[var.pipeline.target_repository].name
  source_repo_arns   = [for k in var.pipeline.source_repositories : module.codeartifact.repositories[k].arn]
  target_repo_arn    = module.codeartifact.repositories[var.pipeline.target_repository].arn
  domain_kms_key_arn = module.codeartifact.domain_kms_key_arn

  cooldown = var.pipeline.cooldown
  scanner  = var.pipeline.scanner
  approval = var.pipeline.approval
  logging  = var.pipeline.logging

  log_retention_days = var.log_retention_days

  tags = local.tags
}

module "inspector" {
  source = "./modules/inspector"
  count  = var.enable_inspector ? 1 : 0
}
