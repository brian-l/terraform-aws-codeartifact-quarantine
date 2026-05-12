# ---------------------------------------------------------------------------
# Optional Amazon Inspector v2 enablement for CodeArtifact scanning.
#
# Inspector v2 enablement is account-wide. This module only manages it when
# var.enable_inspector is true at the root (which conditionally creates this
# submodule via count). If another module owns Inspector for this account,
# leave it disabled — Inspector findings will still be read by the scan Lambda
# regardless of who turned Inspector on.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

resource "aws_inspector2_enabler" "this" {
  account_ids    = [data.aws_caller_identity.current.account_id]
  resource_types = ["CODE_REPOSITORY"]
}
