import json

import boto3
import pytest
from moto import mock_aws

from curhouse.aws.cur_setup import (
    build_bucket_policy,
    build_export_definition,
    ensure_s3_bucket,
)


def test_build_bucket_policy_contains_billing_principal() -> None:
    policy = build_bucket_policy(
        bucket="b", account_id="111111111111", region="us-east-1"
    )
    parsed = json.loads(policy)
    stmt = parsed["Statement"][0]
    assert stmt["Principal"] == {"Service": "bcm-data-exports.amazonaws.com"}
    assert "s3:PutObject" in stmt["Action"]
    cond = stmt["Condition"]["StringLike"]
    assert cond["aws:SourceAccount"] == "111111111111"
    assert cond["aws:SourceArn"].startswith(
        "arn:aws:bcm-data-exports:us-east-1:111111111111:export/"
    )


def test_build_export_definition_includes_all_options() -> None:
    defn = build_export_definition(
        export_name="exp",
        bucket="b",
        prefix="cur2",
        region="us-east-1",
        time_granularity="HOURLY",
        include_resources=True,
        include_split_cost_allocation=True,
        columns=["identity_line_item_id", "line_item_unblended_cost"],
    )
    assert defn["Name"] == "exp"
    q = defn["DataQuery"]
    assert q["QueryStatement"] == (
        "SELECT identity_line_item_id, line_item_unblended_cost "
        "FROM COST_AND_USAGE_REPORT"
    )
    tp = q["TableConfigurations"]["COST_AND_USAGE_REPORT"]
    assert tp["TIME_GRANULARITY"] == "HOURLY"
    assert tp["INCLUDE_RESOURCES"] == "TRUE"
    assert tp["INCLUDE_SPLIT_COST_ALLOCATION_DATA"] == "TRUE"
    dest = defn["DestinationConfigurations"]["S3Destination"]
    assert dest["S3Bucket"] == "b"
    assert dest["S3Prefix"] == "cur2"
    assert dest["S3Region"] == "us-east-1"
    assert dest["S3OutputConfigurations"]["Format"] == "PARQUET"
    assert defn["RefreshCadence"]["Frequency"] == "SYNCHRONOUS"


def test_build_export_definition_empty_columns_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="columns must be non-empty"):
        build_export_definition(
            export_name="exp",
            bucket="b",
            prefix="cur2",
            region="us-east-1",
            time_granularity="HOURLY",
            include_resources=True,
            include_split_cost_allocation=True,
            columns=[],
        )


@mock_aws
def test_ensure_s3_bucket_creates_when_missing() -> None:
    session = boto3.Session(region_name="us-east-1")
    s3 = session.client("s3")
    ensure_s3_bucket(s3, bucket="new-bucket", region="us-east-1")
    s3.head_bucket(Bucket="new-bucket")


@mock_aws
def test_ensure_s3_bucket_noop_when_exists_and_owned() -> None:
    session = boto3.Session(region_name="us-east-1")
    s3 = session.client("s3")
    s3.create_bucket(Bucket="mine")
    ensure_s3_bucket(s3, bucket="mine", region="us-east-1")
