import json

import boto3
import pytest
from moto import mock_aws

from curhouse.aws.s3_manifests import (
    ManifestInfo,
    extract_billing_period,
    is_period_manifest,
    list_manifests,
)


def test_extract_billing_period() -> None:
    key = "cur2/exp/metadata/BILLING_PERIOD=2026-04/exp-Manifest.json"
    assert extract_billing_period(key) == "2026-04"


def test_extract_billing_period_returns_none_if_missing() -> None:
    assert extract_billing_period("cur2/exp/metadata/exp-Manifest.json") is None


def test_is_period_manifest_accepts_canonical() -> None:
    assert is_period_manifest(
        "cur2/exp/metadata/BILLING_PERIOD=2026-04/exp-Manifest.json", "exp"
    )


def test_is_period_manifest_rejects_execution_id_manifest() -> None:
    assert not is_period_manifest(
        "cur2/exp/metadata/BILLING_PERIOD=2026-04/"
        "exp-abc123-CostandUsageReport-Manifest.json",
        "exp",
    )


def test_is_period_manifest_rejects_data_file() -> None:
    assert not is_period_manifest(
        "cur2/exp/data/BILLING_PERIOD=2026-04/exp-00001.snappy.parquet", "exp"
    )


@mock_aws
def test_list_manifests_filters_and_extracts() -> None:
    s3 = boto3.client("s3", region_name="us-east-1")
    bucket = "test-bucket"
    s3.create_bucket(Bucket=bucket)

    base = "cur2/exp"
    s3.put_object(
        Bucket=bucket,
        Key=f"{base}/metadata/BILLING_PERIOD=2026-04/exp-Manifest.json",
        Body=json.dumps({"dataFiles": []}),
    )
    s3.put_object(
        Bucket=bucket,
        Key=f"{base}/metadata/BILLING_PERIOD=2026-05/exp-Manifest.json",
        Body=json.dumps({"dataFiles": []}),
    )
    s3.put_object(
        Bucket=bucket,
        Key=f"{base}/metadata/BILLING_PERIOD=2026-05/"
        "exp-exec1-CostandUsageReport-Manifest.json",
        Body="{}",
    )
    s3.put_object(
        Bucket=bucket,
        Key=f"{base}/data/BILLING_PERIOD=2026-05/exp-00001.snappy.parquet",
        Body=b"x",
    )

    manifests = list_manifests(s3, bucket=bucket, prefix="cur2", export_name="exp")
    periods = sorted(m.billing_period for m in manifests)
    assert periods == ["2026-04", "2026-05"]

    for m in manifests:
        assert isinstance(m, ManifestInfo)
        assert m.etag  # non-empty
        assert m.last_modified  # non-empty
