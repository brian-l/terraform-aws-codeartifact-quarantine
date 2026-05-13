"""Tests for common.audit."""

from __future__ import annotations

from botocore.stub import Stubber


def test_record_writes_canonical_schema(fresh_module):
    audit = fresh_module("common.audit")
    # Stubber.expected_params is exact-match, which doesn't play nicely with the
    # generated timestamp inside version_ts. Assert the call landed via
    # assert_no_pending_responses; field-level assertions live in the test below.
    stubber = Stubber(audit._dynamodb.meta.client)
    stubber.add_response("put_item", {})

    with stubber:
        audit.record(
            "test-audit",
            package_arn="arn:aws:codeartifact:us-east-1:111111111111:package/d/r/npm//lodash",
            version="1.2.3",
            decision="promoted",
            execution="exec-name",
            extra={"format": "npm", "name": "lodash"},
        )

    stubber.assert_no_pending_responses()


def test_record_merges_extra_fields(fresh_module, mocker):
    audit = fresh_module("common.audit")

    # Capture the Item passed to put_item via patching the resource's Table.put_item.
    captured: dict = {}

    def fake_put_item(**kwargs):
        captured.update(kwargs)
        return {}

    mocker.patch.object(
        audit._dynamodb,
        "Table",
        autospec=False,
        side_effect=lambda name: type("FakeTable", (), {"put_item": staticmethod(fake_put_item)})(),
    )

    audit.record(
        "test-audit",
        package_arn="arn:...:package/d/r/npm//lodash",
        version="1.2.3",
        decision="blocked",
        execution="exec-abc",
        extra={"findings": [{"severity": "HIGH"}], "name": "lodash"},
    )

    item = captured["Item"]
    assert item["package_arn"] == "arn:...:package/d/r/npm//lodash"
    assert item["version"] == "1.2.3"
    assert item["decision"] == "blocked"
    assert item["execution"] == "exec-abc"
    assert item["findings"] == [{"severity": "HIGH"}]
    assert item["name"] == "lodash"
    # version_ts must combine version + ISO timestamp.
    assert item["version_ts"].startswith("1.2.3#")
    assert "T" in item["version_ts"]  # ISO format
    # ts field is the same timestamp standalone.
    assert item["ts"] in item["version_ts"]


def test_record_without_extra(fresh_module, mocker):
    audit = fresh_module("common.audit")
    captured: dict = {}

    def fake_put_item(**kwargs):
        captured.update(kwargs)
        return {}

    mocker.patch.object(
        audit._dynamodb,
        "Table",
        side_effect=lambda name: type("FakeTable", (), {"put_item": staticmethod(fake_put_item)})(),
    )

    audit.record(
        "test-audit",
        package_arn="arn:...:package/d/r/npm//lodash",
        version="1.2.3",
        decision="promoted",
        execution="exec",
    )

    item = captured["Item"]
    # Required fields only (record_type defaults to "promotion").
    assert set(item.keys()) == {
        "package_arn",
        "version_ts",
        "version",
        "decision",
        "execution",
        "record_type",
        "ts",
    }
    assert item["record_type"] == "promotion"


def test_record_type_can_be_overridden(fresh_module, mocker):
    audit = fresh_module("common.audit")
    captured: dict = {}

    def fake_put_item(**kwargs):
        captured.update(kwargs)
        return {}

    mocker.patch.object(
        audit._dynamodb,
        "Table",
        side_effect=lambda name: type("FakeTable", (), {"put_item": staticmethod(fake_put_item)})(),
    )

    audit.record(
        "test-audit",
        package_arn="arn:...:package/d/r/npm//lodash",
        version="1.2.3",
        decision="fetched",
        execution="proactive-fill",
        record_type="proactive_fill",
    )
    assert captured["Item"]["record_type"] == "proactive_fill"
