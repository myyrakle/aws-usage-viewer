from __future__ import annotations

import argparse
import datetime as _dt
import logging
import sys

from datahouse.aws.cur_setup import (
    ensure_bucket_policy,
    ensure_cur_export,
    ensure_s3_bucket,
)
from datahouse.aws.s3_manifests import (
    ManifestInfo,
    get_manifest_json,
    list_manifests,
)
from datahouse.aws.session import get_session
from datahouse.clickhouse.client import (
    ensure_database,
    get_client,
    list_table_columns,
)
from datahouse.clickhouse.loader import build_s3_url, reload_partition
from datahouse.clickhouse.schema import ddl_hash, manifest_to_ddl
from datahouse.config import Config
from datahouse.state import ManifestRecord, State, load_state, save_state

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def cmd_setup(cfg: Config, _args: argparse.Namespace) -> int:
    session = get_session(cfg)
    s3 = session.client("s3", region_name=cfg.aws.region)
    exports = session.client("bcm-data-exports", region_name=cfg.aws.region)

    ensure_s3_bucket(s3, bucket=cfg.cur.bucket_name, region=cfg.aws.region)
    ensure_bucket_policy(
        s3,
        bucket=cfg.cur.bucket_name,
        account_id=cfg.aws.account_id,
        region=cfg.aws.region,
    )
    arn = ensure_cur_export(
        exports,
        export_name=cfg.cur.export_name,
        bucket=cfg.cur.bucket_name,
        prefix=cfg.cur.prefix,
        region=cfg.aws.region,
        time_granularity=cfg.cur.time_granularity,
        include_resources=cfg.cur.include_resources,
        include_split_cost_allocation=cfg.cur.include_split_cost_allocation,
    )

    print(
        f"✓ S3 bucket: {cfg.cur.bucket_name}\n"
        f"✓ Bucket policy: applied\n"
        f"✓ CUR export: {cfg.cur.export_name} ({arn})\n"
        "ℹ Initial data delivery may take up to 24 hours.\n"
        f"ℹ Run `datahouse sync` after data appears in "
        f"s3://{cfg.cur.bucket_name}/{cfg.cur.prefix}/{cfg.cur.export_name}/data/"
    )
    return 0


def _diff_manifests(
    manifests: list[ManifestInfo], state: State
) -> list[ManifestInfo]:
    changed: list[ManifestInfo] = []
    for m in manifests:
        rec = state.manifests.get(m.billing_period)
        if rec is None or rec.etag != m.etag:
            changed.append(m)
    return changed


def _ensure_table(
    client, cfg: Config, manifest_json: dict, state: State
) -> tuple[State, bool]:
    """테이블 생성 (없으면). state 업데이트본 반환. 새로 만들어졌으면 True."""
    ensure_database(client, cfg.clickhouse.database)
    cols = list_table_columns(client, cfg.clickhouse.database, cfg.clickhouse.table)
    if cols:
        return state, False

    ddl = manifest_to_ddl(manifest_json, cfg.clickhouse.database, cfg.clickhouse.table)
    logger.info("Creating table %s.%s", cfg.clickhouse.database, cfg.clickhouse.table)
    logger.debug("DDL:\n%s", ddl)
    client.command(ddl)
    new_state = State(
        schema_version=state.schema_version,
        table_created_at=_now_iso(),
        ddl_hash=ddl_hash(ddl),
        manifests=state.manifests,
    )
    return new_state, True


def cmd_sync(cfg: Config, args: argparse.Namespace) -> int:
    session = get_session(cfg)
    s3 = session.client("s3", region_name=cfg.aws.region)

    manifests = list_manifests(
        s3,
        bucket=cfg.cur.bucket_name,
        prefix=cfg.cur.prefix,
        export_name=cfg.cur.export_name,
    )
    if not manifests:
        print(
            "Waiting for first delivery — no manifests found yet. "
            "AWS may take up to 24h after `setup` to produce first files."
        )
        return 0

    state = load_state(cfg.state.path)
    only_period = getattr(args, "only_period", None)
    if only_period:
        changed = [m for m in manifests if m.billing_period == only_period]
        if not changed:
            print(f"No manifest found for billing period {only_period}")
            return 1
    else:
        changed = _diff_manifests(manifests, state)

    if not changed:
        print("Nothing to do — all billing periods are up to date.")
        return 0

    client = get_client(cfg.clickhouse)
    first_manifest_json = get_manifest_json(s3, cfg.cur.bucket_name, changed[0].key)
    state, created = _ensure_table(client, cfg, first_manifest_json, state)
    if created:
        save_state(cfg.state.path, state)

    creds = session.get_credentials().get_frozen_credentials()
    failures = 0
    new_records = dict(state.manifests)
    for m in changed:
        url = build_s3_url(
            bucket=cfg.cur.bucket_name,
            region=cfg.aws.region,
            prefix=cfg.cur.prefix,
            export_name=cfg.cur.export_name,
            billing_period=m.billing_period,
        )
        try:
            row_count = reload_partition(
                client,
                database=cfg.clickhouse.database,
                table=cfg.clickhouse.table,
                billing_period=m.billing_period,
                s3_url=url,
                access_key=creds.access_key,
                secret_key=creds.secret_key,
            )
        except Exception as e:
            logger.exception(
                "Failed to load billing period %s: %s", m.billing_period, e
            )
            failures += 1
            continue

        new_records[m.billing_period] = ManifestRecord(
            etag=m.etag,
            last_modified=m.last_modified,
            last_synced_at=_now_iso(),
            row_count=row_count,
        )
        state = State(
            schema_version=state.schema_version,
            table_created_at=state.table_created_at,
            ddl_hash=state.ddl_hash,
            manifests=new_records,
        )
        save_state(cfg.state.path, state)
        print(f"✓ {m.billing_period}: {row_count:,} rows")

    if failures:
        print(f"Completed with {failures} failures.", file=sys.stderr)
        return 1
    print("Sync complete.")
    return 0


def cmd_status(cfg: Config, _args: argparse.Namespace) -> int:
    state = load_state(cfg.state.path)
    if not state.manifests:
        print("No state yet — run `datahouse sync` first.")
        return 0

    print(f"Table created: {state.table_created_at or '(unknown)'}")
    print(f"DDL hash: {state.ddl_hash or '(unknown)'}")
    print("Billing periods:")
    total = 0
    for period in sorted(state.manifests):
        rec = state.manifests[period]
        total += rec.row_count
        print(
            f"  {period}: {rec.row_count:>12,} rows  "
            f"(synced {rec.last_synced_at})"
        )
    print(f"Total: {total:,} rows across {len(state.manifests)} periods")
    return 0


def cmd_init_schema(cfg: Config, _args: argparse.Namespace) -> int:
    session = get_session(cfg)
    s3 = session.client("s3", region_name=cfg.aws.region)

    manifests = list_manifests(
        s3,
        bucket=cfg.cur.bucket_name,
        prefix=cfg.cur.prefix,
        export_name=cfg.cur.export_name,
    )
    if not manifests:
        print("No manifests found yet.", file=sys.stderr)
        return 1

    latest = max(manifests, key=lambda m: m.billing_period)
    manifest_json = get_manifest_json(s3, cfg.cur.bucket_name, latest.key)

    client = get_client(cfg.clickhouse)
    ensure_database(client, cfg.clickhouse.database)
    cols = list_table_columns(client, cfg.clickhouse.database, cfg.clickhouse.table)
    if cols:
        print(
            f"Table {cfg.clickhouse.database}.{cfg.clickhouse.table} already exists. "
            "Drop it first if you want to recreate."
        )
        return 0

    ddl = manifest_to_ddl(manifest_json, cfg.clickhouse.database, cfg.clickhouse.table)
    client.command(ddl)

    state = load_state(cfg.state.path)
    state = State(
        schema_version=state.schema_version,
        table_created_at=_now_iso(),
        ddl_hash=ddl_hash(ddl),
        manifests=state.manifests,
    )
    save_state(cfg.state.path, state)
    print(f"✓ Created {cfg.clickhouse.database}.{cfg.clickhouse.table}")
    return 0
