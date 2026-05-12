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

variable "tags" {
  type    = map(string)
  default = {}
}
