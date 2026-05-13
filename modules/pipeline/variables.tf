variable "name" {
  description = "Prefix applied to all pipeline resources (Lambdas, state machine, DynamoDB, SQS, log groups)."
  type        = string
}

variable "domain_name" {
  description = "CodeArtifact domain name the pipeline operates against."
  type        = string
}

variable "domain_owner" {
  type        = string
  description = "AWS account ID that owns the CodeArtifact domain."
}

variable "domain_kms_key_arn" {
  description = "KMS key ARN encrypting the CodeArtifact domain. Granted to pipeline Lambdas for decrypt."
  type        = string
}

variable "source_repo_names" {
  description = "CodeArtifact repository names EventBridge ingestion fires on (the staging/quarantine repos)."
  type        = list(string)
}

variable "repository_formats" {
  description = "Map of ecosystem (npm|pypi|...) to the staging repository name handling that ecosystem's external connection. Used by the proactive_fill Lambda to route allowlist entries to the correct repo."
  type        = map(string)
  default     = {}
}

variable "source_repo_arns" {
  description = "ARNs of the source repositories, used for IAM scoping."
  type        = list(string)
}

variable "target_repo_name" {
  description = "CodeArtifact repository name versions are promoted into (typically 'prod')."
  type        = string
}

variable "target_repo_arn" {
  description = "ARN of the target repository, used for IAM scoping."
  type        = string
}

variable "cooldown" {
  type        = string
  description = "ISO-8601 duration (e.g. PT24H)."
  default     = "PT24H"
}

variable "scanner" {
  description = "Scanner configuration: type ('inspector' | 'lambda' | 'none'), severities that block promotion, and the custom Lambda ARN when type is 'lambda'."
  type = object({
    type              = string
    block_on_severity = optional(list(string), ["HIGH", "CRITICAL"])
    lambda_arn        = optional(string)
  })
}

variable "approval" {
  description = "Human-approval configuration: required_when ('always' | 'findings' | 'never'), task-token timeout, and optional pre-existing SNS topic ARN."
  type = object({
    required_when    = string
    timeout          = optional(string, "P14D")
    notification_arn = optional(string)
  })
}

variable "logging" {
  description = "Step Functions logging configuration: level (OFF/ALL/ERROR/FATAL) and whether to include full state input/output."
  type = object({
    level                  = optional(string, "ERROR")
    include_execution_data = optional(bool, false)
  })
  default = {}
}

variable "yank_detection" {
  description = "Yank / unpublish / malware-advisory detection configuration. See root variables.tf for semantics."
  type = object({
    enabled  = optional(bool, true)
    schedule = optional(string, "rate(1 hour)")
    sources  = optional(list(string), ["upstream", "osv"])
    response = optional(object({
      yanked      = optional(string, "unlist")
      unpublished = optional(string, "dispose")
      malicious   = optional(string, "dispose")
    }), {})
  })
  default = {}
}

variable "proactive_fill" {
  description = "Proactive cache-fill configuration (follow-mode + optional allowlist). See root variables.tf for semantics."
  type = object({
    enabled             = optional(bool, true)
    schedule            = optional(string, "rate(1 hour)")
    include_prereleases = optional(bool, false)
    max_fetches_per_run = optional(number, 200)
    allowlist = optional(list(object({
      format              = string
      name                = string
      include_prereleases = optional(bool)
    })), [])
  })
  default = {}
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention (days) for all Lambda log groups in the pipeline."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags applied to every taggable resource the module creates."
  type        = map(string)
  default     = {}
}
