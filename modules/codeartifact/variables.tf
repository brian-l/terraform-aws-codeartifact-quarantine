variable "name" {
  type = string
}

variable "domain_name" {
  type = string
}

variable "domain_kms_key_arn" {
  type    = string
  default = null
}

variable "repositories" {
  type = map(object({
    description         = optional(string)
    external_connection = optional(string)
    upstreams           = optional(list(string), [])
  }))
}

variable "package_groups" {
  type = list(object({
    repository = string
    pattern    = string
    publish    = optional(string, "ALLOW")
    upstream   = optional(string, "ALLOW")
  }))
  default = []
}

variable "consumer_principals" {
  type    = list(string)
  default = []
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
  type    = map(string)
  default = {}
}
