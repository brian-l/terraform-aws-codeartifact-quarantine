# ---------------------------------------------------------------------------
# DynamoDB audit table.
#
# Schema:
#   PK: package_arn  (HASH)
#   SK: version_ts   (RANGE; "version#ISO8601-timestamp")
#
# Allows query-by-package and time-ordered scans. A GSI on `decision` supports
# "show all blocked versions in last N days" investigations.
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "audit" {
  name         = "${var.name}-audit"
  billing_mode = "PAY_PER_REQUEST"

  # NOTE: hash_key/range_key are slated for deprecation in favour of nested
  # key_schema blocks (provider main branch). The new syntax has not been
  # released as of aws provider 6.44 — switch when it ships.
  hash_key  = "package_arn"
  range_key = "version_ts"

  attribute {
    name = "package_arn"
    type = "S"
  }

  attribute {
    name = "version_ts"
    type = "S"
  }

  attribute {
    name = "decision"
    type = "S"
  }

  global_secondary_index {
    name            = "decision-version_ts-index"
    hash_key        = "decision"
    range_key       = "version_ts"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.audit.arn
  }

  tags = var.tags
}

resource "aws_kms_key" "audit" {
  description             = "Encryption for ${var.name} audit table"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = var.tags
}

resource "aws_kms_alias" "audit" {
  name          = "alias/${var.name}-audit"
  target_key_id = aws_kms_key.audit.id
}
