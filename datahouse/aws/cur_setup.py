from __future__ import annotations

import json
import logging
from typing import Any

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def build_bucket_policy(*, bucket: str, account_id: str, region: str) -> str:
    """CUR 2.0 (bcm-data-exports)가 PutObject 가능하도록 하는 버킷 정책 JSON.

    Legacy CUR 1.0의 principal은 `billingreports.amazonaws.com`이지만
    CUR 2.0 / Data Exports는 `bcm-data-exports.amazonaws.com`을 쓴다.
    """
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowDataExportsDelivery",
                "Effect": "Allow",
                "Principal": {"Service": "bcm-data-exports.amazonaws.com"},
                "Action": [
                    "s3:PutObject",
                    "s3:GetBucketPolicy",
                ],
                "Resource": [
                    f"arn:aws:s3:::{bucket}",
                    f"arn:aws:s3:::{bucket}/*",
                ],
                "Condition": {
                    "StringLike": {
                        "aws:SourceAccount": account_id,
                        "aws:SourceArn": (
                            f"arn:aws:bcm-data-exports:{region}:{account_id}:export/*"
                        ),
                    }
                },
            }
        ],
    }
    return json.dumps(policy)


def _table_properties(
    *,
    time_granularity: str,
    include_resources: bool,
    include_split_cost_allocation: bool,
) -> dict[str, str]:
    return {
        "TIME_GRANULARITY": time_granularity,
        "INCLUDE_RESOURCES": "TRUE" if include_resources else "FALSE",
        "INCLUDE_SPLIT_COST_ALLOCATION_DATA": (
            "TRUE" if include_split_cost_allocation else "FALSE"
        ),
        "INCLUDE_MANUAL_DISCOUNT_COMPATIBILITY": "FALSE",
    }


def discover_cur_columns(
    exports_client: Any,
    *,
    time_granularity: str,
    include_resources: bool,
    include_split_cost_allocation: bool,
) -> list[str]:
    """COST_AND_USAGE_REPORT 테이블의 컬럼 이름 목록을 AWS에서 조회.

    TableProperties에 따라 컬럼 구성이 달라지므로 우리 설정과 동일한
    properties로 get_table을 호출한다.
    """
    props = _table_properties(
        time_granularity=time_granularity,
        include_resources=include_resources,
        include_split_cost_allocation=include_split_cost_allocation,
    )
    resp = exports_client.get_table(
        TableName="COST_AND_USAGE_REPORT",
        TableProperties=props,
    )
    return [col["Name"] for col in resp["Schema"]]


def build_export_definition(
    *,
    export_name: str,
    bucket: str,
    prefix: str,
    region: str,
    time_granularity: str,
    include_resources: bool,
    include_split_cost_allocation: bool,
    columns: list[str],
) -> dict:
    if not columns:
        raise ValueError("columns must be non-empty")
    select = ", ".join(columns)
    return {
        "Name": export_name,
        "Description": "datahouse CUR 2.0 export",
        "DataQuery": {
            "QueryStatement": f"SELECT {select} FROM COST_AND_USAGE_REPORT",
            "TableConfigurations": {
                "COST_AND_USAGE_REPORT": _table_properties(
                    time_granularity=time_granularity,
                    include_resources=include_resources,
                    include_split_cost_allocation=include_split_cost_allocation,
                )
            },
        },
        "DestinationConfigurations": {
            "S3Destination": {
                "S3Bucket": bucket,
                "S3Prefix": prefix,
                "S3Region": region,
                "S3OutputConfigurations": {
                    "OutputType": "CUSTOM",
                    "Format": "PARQUET",
                    "Compression": "PARQUET",
                    "Overwrite": "OVERWRITE_REPORT",
                },
            }
        },
        "RefreshCadence": {"Frequency": "SYNCHRONOUS"},
    }


def ensure_s3_bucket(s3_client: Any, *, bucket: str, region: str) -> None:
    """버킷이 없으면 생성, 있고 우리 소유면 no-op, 다른 계정 소유면 예외."""
    try:
        s3_client.head_bucket(Bucket=bucket)
        logger.info("S3 bucket %s already exists", bucket)
        return
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code == "403" or status == 403:
            raise RuntimeError(
                f"S3 bucket {bucket!r} exists but is owned by another account. "
                "Choose a different bucket_name in config.toml."
            ) from e
        if code not in ("404", "NoSuchBucket") and status != 404:
            raise

    # us-east-1은 LocationConstraint 없이 생성
    if region == "us-east-1":
        s3_client.create_bucket(Bucket=bucket)
    else:
        s3_client.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
    logger.info("Created S3 bucket %s in %s", bucket, region)


def ensure_bucket_policy(
    s3_client: Any, *, bucket: str, account_id: str, region: str
) -> None:
    desired = build_bucket_policy(bucket=bucket, account_id=account_id, region=region)
    try:
        current = s3_client.get_bucket_policy(Bucket=bucket).get("Policy")
        if current and json.loads(current) == json.loads(desired):
            logger.info("Bucket policy on %s already up to date", bucket)
            return
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code != "NoSuchBucketPolicy":
            raise
    s3_client.put_bucket_policy(Bucket=bucket, Policy=desired)
    logger.info("Applied bucket policy to %s", bucket)


def find_existing_export(exports_client: Any, export_name: str) -> dict | None:
    paginator = exports_client.get_paginator("list_exports")
    for page in paginator.paginate():
        for ref in page.get("Exports", []):
            if ref.get("ExportName") == export_name:
                arn = ref["ExportArn"]
                full = exports_client.get_export(ExportArn=arn)
                return full.get("Export")
    return None


def ensure_cur_export(
    exports_client: Any,
    *,
    export_name: str,
    bucket: str,
    prefix: str,
    region: str,
    time_granularity: str,
    include_resources: bool,
    include_split_cost_allocation: bool,
) -> str:
    """export가 없으면 생성, 있으면 비교 후 update 또는 no-op. ExportArn 반환."""
    columns = discover_cur_columns(
        exports_client,
        time_granularity=time_granularity,
        include_resources=include_resources,
        include_split_cost_allocation=include_split_cost_allocation,
    )
    logger.info("Discovered %d CUR columns", len(columns))
    desired = build_export_definition(
        export_name=export_name,
        bucket=bucket,
        prefix=prefix,
        region=region,
        time_granularity=time_granularity,
        include_resources=include_resources,
        include_split_cost_allocation=include_split_cost_allocation,
        columns=columns,
    )

    existing = find_existing_export(exports_client, export_name)
    if existing is not None:
        existing_arn = existing.get("ExportArn") or existing.get("Arn")
        if _exports_match(existing, desired):
            logger.info("CUR export %s already up to date", export_name)
            return existing_arn  # type: ignore[return-value]
        if not _can_update_in_place(existing, desired):
            raise RuntimeError(
                f"Existing export {export_name!r} has incompatible destination. "
                "Delete it manually in AWS console or change export_name in config."
            )
        exports_client.update_export(
            ExportArn=existing_arn, Export=desired
        )
        logger.info("Updated CUR export %s", export_name)
        return existing_arn  # type: ignore[return-value]

    resp = exports_client.create_export(Export=desired)
    arn = resp["ExportArn"]
    logger.info("Created CUR export %s (%s)", export_name, arn)
    return arn


def _exports_match(existing: dict, desired: dict) -> bool:
    """주요 필드만 비교 (AWS가 추가하는 메타데이터 무시)."""
    keys = ("DataQuery", "DestinationConfigurations", "RefreshCadence")
    return all(existing.get(k) == desired.get(k) for k in keys)


def _can_update_in_place(existing: dict, desired: dict) -> bool:
    """S3 경로(bucket/prefix/region)가 바뀌면 update 불가."""
    e = existing.get("DestinationConfigurations", {}).get("S3Destination", {})
    d = desired.get("DestinationConfigurations", {}).get("S3Destination", {})
    return all(e.get(k) == d.get(k) for k in ("S3Bucket", "S3Prefix", "S3Region"))
