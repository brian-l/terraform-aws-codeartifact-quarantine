variable "name" {
  type = string
}

variable "domain_name" {
  type = string
}

variable "domain_owner" {
  type        = string
  description = "AWS account ID that owns the CodeArtifact domain."
}

variable "domain_kms_key_arn" {
  type = string
}

variable "source_repo_names" {
  type = list(string)
}

variable "source_repo_arns" {
  type = list(string)
}

variable "target_repo_name" {
  type = string
}

variable "target_repo_arn" {
  type = string
}

variable "cooldown" {
  type        = string
  description = "ISO-8601 duration (e.g. PT24H)."
  default     = "PT24H"
}

variable "scanner" {
  type = object({
    type              = string
    block_on_severity = optional(list(string), ["HIGH", "CRITICAL"])
    lambda_arn        = optional(string)
  })
}

variable "approval" {
  type = object({
    required_when    = string
    timeout          = optional(string, "P14D")
    notification_arn = optional(string)
  })
}

variable "logging" {
  type = object({
    level                  = optional(string, "ERROR")
    include_execution_data = optional(bool, false)
  })
  default = {}
}

variable "pre_promote_lambda_arn" {
  type    = string
  default = null
}

variable "post_promote_lambda_arn" {
  type    = string
  default = null
}

variable "policy_storage" {
  type    = string
  default = "env"
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "tags" {
  type    = map(string)
  default = {}
}
