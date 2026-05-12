# Lambda handlers

Each subdirectory is a Python 3.12 Lambda function packaged inline by the parent
Terraform module via `archive_file`.

## Layout

```
lambda/
├── common/         # Shared utilities; copied into each handler before zip
├── ingestion/      # EventBridge -> SQS receiver, starts SFN execution
├── scan/           # Inspector ListFindings or custom scanner invocation
├── promote/        # copy-package-versions staging -> prod
├── audit/          # DynamoDB put_item
├── expedite/       # Manual override entry point
├── approve/        # Slack/web callback target for SFN waitForTaskToken
└── Makefile        # Bundles common/ into each handler dir
```

## Build

Before `terraform apply`:

```bash
make -C lambda all
```

This copies `lambda/common/` into each handler dir so `archive_file` can zip
the handler + its shared utilities as a single deployable bundle. CI should
run this as part of the plan step (see `.github/workflows/ci.yml`).

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
