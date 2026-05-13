"""Shared utilities for quarantine pipeline Lambdas.

Modules:
    codeartifact -- thin wrappers around boto3 codeartifact client calls
    audit        -- write entries to the DynamoDB audit table
    config       -- read-once env var parsing
    logging      -- structured JSON logging configured for CloudWatch
"""
