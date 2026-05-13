terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.50.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4.0"
    }
    # AWSCC (Cloud Control API) provider is used for resources the legacy aws
    # provider hasn't implemented yet — specifically aws_codeartifact_package_group,
    # which provides the dependency-confusion defense via package origin controls.
    awscc = {
      source  = "hashicorp/awscc"
      version = ">= 1.0.0"
    }
  }
}
