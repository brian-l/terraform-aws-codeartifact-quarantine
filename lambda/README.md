# Lambda handlers

Each subdirectory is a Python 3.12 Lambda function packaged inline by the parent
Terraform module via `archive_file`.

## Layout

```
lambda/
├── common/         # Shared utilities; bundled into every handler zip
├── ingestion/      # EventBridge -> SQS receiver, starts SFN execution
├── scan/           # Inspector ListFindings or custom scanner invocation
├── promote/        # copy-package-versions source -> target repo
├── audit/          # DynamoDB put_item
├── expedite/       # Manual override entry point
└── approve/        # Slack/web callback target for SFN waitForTaskToken
```

## Build

No build step is required. `archive_file` blocks in `modules/pipeline/lambdas.tf`
use explicit `source` blocks to include each handler's `handler.py` plus every
file in `lambda/common/` directly. The zip is reassembled on every `terraform
plan`, so adding or modifying any common module is picked up automatically.

(An earlier version of the module copied `common/` into each handler dir via
a Makefile; that approach was retired because forgetting to run `make` produced
"No module named 'common'" errors at Lambda runtime.)

## Dependencies

All handlers use only the boto3 SDK that the Lambda runtime provides — no
third-party dependencies. If a handler grows third-party deps:

1. Add them to that handler's `requirements.txt`.
2. Extend the Makefile target for that handler to run `pip install --target .`
   before the archive step.
3. Consider moving shared deps to a Lambda layer if more than one handler
   needs the same package (not implemented in v1).

## Logging

All handlers use `common/logging.py` for structured JSON output. Query via
CloudWatch Logs Insights:

```
fields @timestamp, level, package, version, decision
| filter decision = "blocked"
| sort @timestamp desc
```
