# ---------------------------------------------------------------------------
# Lambda functions, packaged inline from ../../lambda/ subdirs.
#
# Each function has its own zip built from {handler.py, common/} + an optional
# requirements.txt vendored at build time.
#
# CAVEAT: The current scheme assumes consumers run `make build` (or equivalent)
# before terraform apply if any handler has third-party deps. Pure-stdlib
# handlers work out of the box.
# ---------------------------------------------------------------------------

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  lambda_runtime = "python3.12"
  lambda_root    = "${path.module}/../../lambda"

  # State machine ARN computed deterministically rather than referenced from
  # aws_sfn_state_machine.quarantine.arn. The state machine has Lambda ARNs in
  # its definition, and the Lambdas have STATE_MACHINE_ARN in their env vars,
  # so a direct reference would create a dependency cycle. The ARN format is
  # stable and predictable; the SFN resource uses the same `${var.name}-quarantine`
  # name local we derive here.
  state_machine_arn = "arn:${data.aws_partition.current.partition}:states:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.name}-quarantine"

  # Convert var.cooldown (ISO-8601 duration like "PT24H", "P7D") to integer
  # seconds. Step Functions Wait state only accepts Seconds / SecondsPath /
  # Timestamp / TimestampPath — there's no Duration field. We support the
  # subset of ISO-8601 our config needs: P<D>D and PT<H>H / PT<M>M / PT<S>S
  # (and combinations like "P1DT12H"). Fail loudly on anything else.
  _cooldown_match = regex("^P(?:(?P<d>[0-9]+)D)?(?:T(?:(?P<h>[0-9]+)H)?(?:(?P<m>[0-9]+)M)?(?:(?P<s>[0-9]+)S)?)?$", var.cooldown)
  cooldown_seconds = (
    tonumber(coalesce(local._cooldown_match.d, "0")) * 86400
    + tonumber(coalesce(local._cooldown_match.h, "0")) * 3600
    + tonumber(coalesce(local._cooldown_match.m, "0")) * 60
    + tonumber(coalesce(local._cooldown_match.s, "0"))
  )

  # Common env vars passed to every Lambda. Specific Lambdas merge their own keys.
  #
  # NOTE: source-repo identity is intentionally absent. EventBridge events carry
  # detail.repositoryName, which Lambdas use directly. This keeps the pipeline
  # multi-source-repo-aware without env var fan-out.
  common_env = {
    DOMAIN_NAME       = var.domain_name
    DOMAIN_OWNER      = var.domain_owner
    SOURCE_REPO_NAMES = join(",", var.source_repo_names)
    TARGET_REPO_NAME  = var.target_repo_name
    AUDIT_TABLE_NAME  = aws_dynamodb_table.audit.name
    STATE_MACHINE_ARN = local.state_machine_arn
    LOG_LEVEL         = "INFO"
  }
}

data "aws_partition" "current" {}

# ---------- Ingestion -------------------------------------------------------

data "archive_file" "ingestion" {
  type        = "zip"
  source_dir  = "${local.lambda_root}/ingestion"
  output_path = "${path.module}/.terraform.tmp/ingestion.zip"
}

resource "aws_cloudwatch_log_group" "ingestion" {
  name              = "/aws/lambda/${var.name}-ingestion"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "ingestion" {
  function_name = "${var.name}-ingestion"
  role          = aws_iam_role.ingestion.arn
  runtime       = local.lambda_runtime
  handler       = "handler.lambda_handler"

  filename         = data.archive_file.ingestion.output_path
  source_code_hash = data.archive_file.ingestion.output_base64sha256

  timeout     = 30
  memory_size = 256

  environment {
    variables = local.common_env
  }

  depends_on = [aws_cloudwatch_log_group.ingestion]
  tags       = var.tags
}

resource "aws_lambda_event_source_mapping" "ingestion" {
  event_source_arn = aws_sqs_queue.ingestion.arn
  function_name    = aws_lambda_function.ingestion.arn
  batch_size       = 10
  enabled          = true

  # AWS Lambda validates that the function's role has SQS permissions at the
  # moment the event source mapping is created. The Lambda function resource
  # depends only on aws_iam_role, NOT on aws_iam_role_policy — so without an
  # explicit edge here, Terraform may create the mapping before the role
  # policy attach completes, and AWS rejects with "function execution role
  # does not have permissions to call ReceiveMessage on SQS".
  depends_on = [aws_iam_role_policy.ingestion]
}

# ---------- Scan ------------------------------------------------------------

data "archive_file" "scan" {
  type        = "zip"
  source_dir  = "${local.lambda_root}/scan"
  output_path = "${path.module}/.terraform.tmp/scan.zip"
}

resource "aws_cloudwatch_log_group" "scan" {
  name              = "/aws/lambda/${var.name}-scan"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "scan" {
  function_name = "${var.name}-scan"
  role          = aws_iam_role.scan.arn
  runtime       = local.lambda_runtime
  handler       = "handler.lambda_handler"

  filename         = data.archive_file.scan.output_path
  source_code_hash = data.archive_file.scan.output_base64sha256

  timeout     = 120
  memory_size = 512

  environment {
    variables = merge(local.common_env, {
      SCANNER_TYPE = var.scanner.type
      # coalesce() errors when ALL args are null/empty; use a conditional instead
      # so scanner.lambda_arn = null (the default for "inspector" mode) is fine.
      SCANNER_LAMBDA_ARN = var.scanner.lambda_arn == null ? "" : var.scanner.lambda_arn
      BLOCK_ON_SEVERITY  = join(",", var.scanner.block_on_severity)
    })
  }

  depends_on = [aws_cloudwatch_log_group.scan]
  tags       = var.tags
}

# ---------- Promote ---------------------------------------------------------

data "archive_file" "promote" {
  type        = "zip"
  source_dir  = "${local.lambda_root}/promote"
  output_path = "${path.module}/.terraform.tmp/promote.zip"
}

resource "aws_cloudwatch_log_group" "promote" {
  name              = "/aws/lambda/${var.name}-promote"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "promote" {
  function_name = "${var.name}-promote"
  role          = aws_iam_role.promote.arn
  runtime       = local.lambda_runtime
  handler       = "handler.lambda_handler"

  filename         = data.archive_file.promote.output_path
  source_code_hash = data.archive_file.promote.output_base64sha256

  timeout     = 60
  memory_size = 256

  environment {
    variables = local.common_env
  }

  depends_on = [aws_cloudwatch_log_group.promote]
  tags       = var.tags
}

# ---------- Audit -----------------------------------------------------------

data "archive_file" "audit" {
  type        = "zip"
  source_dir  = "${local.lambda_root}/audit"
  output_path = "${path.module}/.terraform.tmp/audit.zip"
}

resource "aws_cloudwatch_log_group" "audit" {
  name              = "/aws/lambda/${var.name}-audit"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "audit" {
  function_name = "${var.name}-audit"
  role          = aws_iam_role.audit.arn
  runtime       = local.lambda_runtime
  handler       = "handler.lambda_handler"

  filename         = data.archive_file.audit.output_path
  source_code_hash = data.archive_file.audit.output_base64sha256

  timeout     = 30
  memory_size = 256

  environment {
    variables = local.common_env
  }

  depends_on = [aws_cloudwatch_log_group.audit]
  tags       = var.tags
}

# ---------- Expedite --------------------------------------------------------

data "archive_file" "expedite" {
  type        = "zip"
  source_dir  = "${local.lambda_root}/expedite"
  output_path = "${path.module}/.terraform.tmp/expedite.zip"
}

resource "aws_cloudwatch_log_group" "expedite" {
  name              = "/aws/lambda/${var.name}-expedite"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "expedite" {
  function_name = "${var.name}-expedite"
  role          = aws_iam_role.expedite.arn
  runtime       = local.lambda_runtime
  handler       = "handler.lambda_handler"

  filename         = data.archive_file.expedite.output_path
  source_code_hash = data.archive_file.expedite.output_base64sha256

  timeout     = 60
  memory_size = 256

  environment {
    variables = local.common_env
  }

  depends_on = [aws_cloudwatch_log_group.expedite]
  tags       = var.tags
}

# ---------- Approve ---------------------------------------------------------

data "archive_file" "approve" {
  type        = "zip"
  source_dir  = "${local.lambda_root}/approve"
  output_path = "${path.module}/.terraform.tmp/approve.zip"
}

resource "aws_cloudwatch_log_group" "approve" {
  name              = "/aws/lambda/${var.name}-approve"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "approve" {
  function_name = "${var.name}-approve"
  role          = aws_iam_role.approve.arn
  runtime       = local.lambda_runtime
  handler       = "handler.lambda_handler"

  filename         = data.archive_file.approve.output_path
  source_code_hash = data.archive_file.approve.output_base64sha256

  timeout     = 30
  memory_size = 256

  environment {
    variables = local.common_env
  }

  depends_on = [aws_cloudwatch_log_group.approve]
  tags       = var.tags
}
