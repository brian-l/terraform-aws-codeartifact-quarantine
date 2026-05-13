"""Write audit entries to the DynamoDB audit table."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource

_dynamodb: DynamoDBServiceResource = boto3.resource("dynamodb")


def record(
    table_name: str,
    *,
    package_arn: str,
    version: str,
    decision: str,
    execution: str,
    record_type: str = "promotion",
    extra: dict[str, Any] | None = None,
) -> None:
    """Insert an audit row.

    Schema:
        PK: package_arn
        SK: version_ts = "{version}#{ISO-timestamp}"

    record_type discriminates row shape — "promotion" for the standard pipeline
    outcome (the default; pre-0.1.2 rows have no record_type and should be
    treated as "promotion" by readers), "proactive_fill" for cache pre-warm
    fetches written by the proactive_fill Lambda. Reserved for future event
    classes.
    """
    table = _dynamodb.Table(table_name)
    now = dt.datetime.now(dt.UTC).isoformat()
    item: dict[str, Any] = {
        "package_arn": package_arn,
        "version_ts": f"{version}#{now}",
        "version": version,
        "decision": decision,
        "execution": execution,
        "record_type": record_type,
        "ts": now,
    }
    if extra:
        item.update(extra)
    table.put_item(Item=item)
