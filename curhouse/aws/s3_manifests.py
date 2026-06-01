from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_BILLING_PERIOD_RE = re.compile(r"BILLING_PERIOD=(\d{4}-\d{2})")


@dataclass(frozen=True)
class ManifestInfo:
    billing_period: str
    key: str
    etag: str
    last_modified: str  # ISO8601 UTC


def extract_billing_period(key: str) -> str | None:
    m = _BILLING_PERIOD_RE.search(key)
    return m.group(1) if m else None


def is_period_manifest(key: str, export_name: str) -> bool:
    """canonical period manifest인지 확인.

    pattern: {prefix}/{export}/metadata/BILLING_PERIOD=YYYY-MM/{export}-Manifest.json
    execution-id 포함된 manifest와 data parquet 파일은 제외.
    """
    if "/metadata/" not in key:
        return False
    if not key.endswith(f"{export_name}-Manifest.json"):
        return False
    if extract_billing_period(key) is None:
        return False
    return True


def list_manifests(
    s3_client: Any, bucket: str, prefix: str, export_name: str
) -> list[ManifestInfo]:
    metadata_prefix = f"{prefix}/{export_name}/metadata/"
    paginator = s3_client.get_paginator("list_objects_v2")
    out: list[ManifestInfo] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=metadata_prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not is_period_manifest(key, export_name):
                continue
            period = extract_billing_period(key)
            assert period is not None  # is_period_manifest가 보장
            out.append(
                ManifestInfo(
                    billing_period=period,
                    key=key,
                    etag=obj["ETag"],
                    last_modified=obj["LastModified"].isoformat(),
                )
            )
    return out


def get_manifest_json(s3_client: Any, bucket: str, key: str) -> dict:
    resp = s3_client.get_object(Bucket=bucket, Key=key)
    return json.loads(resp["Body"].read())
