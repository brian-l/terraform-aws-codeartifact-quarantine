variable "name" {
  description = "Prefix applied to created resources (consumer IAM policy, etc.)."
  type        = string
}

variable "domain_name" {
  description = "CodeArtifact domain name. Must be unique within the AWS account."
  type        = string
}

variable "domain_kms_key_arn" {
  description = "KMS key ARN for the CodeArtifact domain. If null, a customer-managed key is created."
  type        = string
  default     = null
}

variable "repositories" {
  description = "Map of CodeArtifact repositories to create. Keys are repository names; values define description, optional external_connection, and upstream repository keys."
  type = map(object({
    description         = optional(string)
    external_connection = optional(string)
    upstreams           = optional(list(string), [])
  }))
}

variable "package_groups" {
  description = "Package group origin control rules. Each entry binds a repository + pattern with publish/upstream permissions."
  type = list(object({
    repository = string
    pattern    = string
    publish    = optional(string, "ALLOW")
    upstream   = optional(string, "ALLOW")
  }))
  default = []
}

variable "consumer_principals" {
  description = "List of AWS principal ARNs allowed to read from the prod repository cross-account."
  type        = list(string)
  default     = []
}

variable "create_consumer_policy" {
  description = <<-EOT
    Whether to create a managed IAM policy that grants the minimum permissions
    needed to read from the prod repository (GetAuthorizationToken /
    GetDomainPermissionsPolicy on the domain, read/list/describe on the prod
    repo, sts:GetServiceBearerToken for codeartifact). Consumers attach the
    policy to roles they own.
  EOT
  type        = bool
  default     = true
}

variable "consumer_policy_name" {
  description = "Override the IAM policy name. Defaults to '<name>-consumer'."
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags applied to every taggable resource the module creates."
  type        = map(string)
  default     = {}
}
