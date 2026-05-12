variable "name" {
  description = "Prefix applied to all created AWS resources. Use something descriptive like 'platform-pkg-quarantine'."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,62}$", var.name))
    error_message = "name must be lowercase alphanumeric with hyphens, 2-63 chars, starting with a letter or digit."
  }
}

variable "domain_name" {
  description = "CodeArtifact domain name. Must be globally unique within the AWS account."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,49}$", var.domain_name))
    error_message = "domain_name must be lowercase, start with a letter, and be 2-50 chars."
  }
}

variable "domain_kms_key_arn" {
  description = "KMS key ARN for the CodeArtifact domain. If null, a customer-managed key is created."
  type        = string
  default     = null
}

variable "repositories" {
  description = <<-EOT
    Map of CodeArtifact repositories to create. The 'prod' key is required (consumers read from
    here). One or more source/staging repositories (any key) must be listed in
    var.pipeline.source_repositories. Add 'internal' or other keys as needed.

    - external_connection: a single string like "public:npmjs" or "public:pypi". CodeArtifact
      allows AT MOST ONE external connection per repository. If you need multiple public sources,
      create multiple staging repositories (one per source) and list them all in
      pipeline.source_repositories; prod can upstream all of them.
    - upstreams: list of other repository keys in this map. Resolution follows list order.
  EOT
  type = map(object({
    description         = optional(string)
    external_connection = optional(string)
    upstreams           = optional(list(string), [])
  }))

  validation {
    condition     = contains(keys(var.repositories), "prod")
    error_message = "repositories must include a 'prod' key (the consumer-facing repository)."
  }

  validation {
    condition = alltrue([
      for k, v in var.repositories : alltrue([for u in v.upstreams : contains(keys(var.repositories), u)])
    ])
    error_message = "all upstream references must point to keys defined in this map."
  }
}

variable "pipeline" {
  description = <<-EOT
    Quarantine pipeline configuration.

    - source_repositories: list of keys from var.repositories. EventBridge fires the pipeline on
      ingestion into any of these. Typical setup: one staging repo per package format
      (staging-npm, staging-pypi, etc.) since CodeArtifact only allows one external connection
      per repo.
    - target_repository: key from var.repositories. Successful versions are promoted here.
    - cooldown: ISO-8601 duration (e.g. "PT24H", "P7D"). Step Functions Wait state.
    - scanner.type: "inspector" (Amazon Inspector v2), "lambda" (custom Lambda ARN), or "none".
    - scanner.block_on_severity: Inspector severities that trigger findings.
    - approval.required_when: "always" | "findings" | "never".
    - approval.notification_arn: SNS topic for findings + approval requests.
  EOT
  type = object({
    source_repositories = list(string)
    target_repository   = string
    cooldown            = optional(string, "PT24H")
    scanner = object({
      type              = string
      block_on_severity = optional(list(string), ["HIGH", "CRITICAL"])
      lambda_arn        = optional(string)
    })
    approval = object({
      required_when    = string
      timeout          = optional(string, "P14D")
      notification_arn = string
    })
    logging = optional(object({
      # Step Functions log level: OFF | ALL | ERROR | FATAL. Default ERROR keeps
      # log volume and information disclosure minimal (only failure transitions
      # are logged). Set ALL only when you need forensic execution traces.
      level = optional(string, "ERROR")
      # Include full state input/output in CloudWatch Logs. WARNING: when true,
      # logs contain Inspector findings (CVE IDs and details), package names,
      # and (during human-approval states) the SFN task token. Default false.
      include_execution_data = optional(bool, false)
    }), {})
  })

  validation {
    condition     = length(var.pipeline.source_repositories) > 0
    error_message = "pipeline.source_repositories must contain at least one repository key."
  }

  validation {
    condition     = !contains(var.pipeline.source_repositories, var.pipeline.target_repository)
    error_message = "pipeline.target_repository must not appear in pipeline.source_repositories."
  }

  validation {
    condition     = contains(["inspector", "lambda", "none"], var.pipeline.scanner.type)
    error_message = "pipeline.scanner.type must be 'inspector', 'lambda', or 'none'."
  }

  validation {
    condition     = var.pipeline.scanner.type != "lambda" || var.pipeline.scanner.lambda_arn != null
    error_message = "pipeline.scanner.lambda_arn is required when scanner.type is 'lambda'."
  }

  validation {
    condition     = contains(["always", "findings", "never"], var.pipeline.approval.required_when)
    error_message = "pipeline.approval.required_when must be 'always', 'findings', or 'never'."
  }

  validation {
    condition     = contains(["OFF", "ALL", "ERROR", "FATAL"], var.pipeline.logging.level)
    error_message = "pipeline.logging.level must be one of OFF, ALL, ERROR, FATAL."
  }
}

variable "package_groups" {
  description = <<-EOT
    Package group origin control rules. Codifies the dependency-confusion defense.

    Each entry: {repository, pattern, publish, upstream}
      - repository: key from var.repositories
      - pattern: CodeArtifact package group pattern (e.g. "/pypi/myorg-" or "/npm/@myorg/")
      - publish: "ALLOW" | "BLOCK" (default ALLOW)
      - upstream: "ALLOW" | "BLOCK" | "ALLOW_SPECIFIC_REPOSITORIES" (default ALLOW)
  EOT
  type = list(object({
    repository = string
    pattern    = string
    publish    = optional(string, "ALLOW")
    upstream   = optional(string, "ALLOW")
  }))
  default = []

  validation {
    condition = alltrue([
      for g in var.package_groups : contains(["ALLOW", "BLOCK"], g.publish)
    ])
    error_message = "package_groups[*].publish must be 'ALLOW' or 'BLOCK'."
  }

  validation {
    condition = alltrue([
      for g in var.package_groups : contains(["ALLOW", "BLOCK", "ALLOW_SPECIFIC_REPOSITORIES"], g.upstream)
    ])
    error_message = "package_groups[*].upstream must be 'ALLOW', 'BLOCK', or 'ALLOW_SPECIFIC_REPOSITORIES'."
  }
}

variable "consumer_principals" {
  description = "List of AWS principal ARNs allowed to read from the target repository cross-account."
  type        = list(string)
  default     = []
}

variable "enable_inspector" {
  description = <<-EOT
    Enable Inspector v2 CodeArtifact scanning. Account-wide side effect.

    Leave false if another module already owns Inspector v2 enablement for this account.
  EOT
  type        = bool
  default     = false
}

variable "pre_promote_lambda_arn" {
  description = "Optional Lambda invoked after scan, before copy. Can veto promotion by returning non-zero status."
  type        = string
  default     = null
}

variable "post_promote_lambda_arn" {
  description = "Optional Lambda invoked after successful promotion. Best-effort; failures do not roll back."
  type        = string
  default     = null
}

variable "policy_storage" {
  description = <<-EOT
    Where the pipeline policy lives.
    - "env": baked into Lambda env vars (immutable until next terraform apply)
    - "ssm": stored in SSM Parameter Store (editable without re-applying)
  EOT
  type        = string
  default     = "env"

  validation {
    condition     = contains(["env", "ssm"], var.policy_storage)
    error_message = "policy_storage must be 'env' or 'ssm'."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for all Lambda log groups."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags applied to all created resources."
  type        = map(string)
  default     = {}
}
