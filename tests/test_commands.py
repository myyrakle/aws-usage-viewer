import argparse
from unittest.mock import MagicMock, patch

import pytest

from curhouse.commands import _diff_manifests, cmd_status
from curhouse.config import (
    AwsConfig,
    ClickhouseConfig,
    Config,
    CurConfig,
    StateConfig,
)
from curhouse.state import ManifestRecord, State


def _make_cfg(tmp_path) -> Config:
    return Config(
        aws=AwsConfig(profile="p", region="us-east-1", account_id="111"),
        cur=CurConfig(
            bucket_name="b",
            export_name="e",
            prefix="cur2",
            time_granularity="HOURLY",
            include_resources=True,
            include_split_cost_allocation=True,
        ),
        clickhouse=ClickhouseConfig(
            host="h", port=8123, user="u", password="",
            database="d", table="t", secure=False,
        ),
        state=StateConfig(path=str(tmp_path / "state.json")),
    )


def test_diff_manifests_new_period() -> None:
    state = State()
    manifests = [
        MagicMock(billing_period="2026-04", etag='"abc"', last_modified="t1"),
    ]
    changed = _diff_manifests(manifests, state)
    assert [m.billing_period for m in changed] == ["2026-04"]


def test_diff_manifests_unchanged_skipped() -> None:
    state = State(
        manifests={
            "2026-04": ManifestRecord(
                etag='"abc"',
                last_modified="t1",
                last_synced_at="t1",
                row_count=10,
            )
        }
    )
    manifests = [
        MagicMock(billing_period="2026-04", etag='"abc"', last_modified="t1"),
    ]
    assert _diff_manifests(manifests, state) == []


def test_diff_manifests_etag_changed_returned() -> None:
    state = State(
        manifests={
            "2026-04": ManifestRecord(
                etag='"OLD"',
                last_modified="t1",
                last_synced_at="t1",
                row_count=10,
            )
        }
    )
    manifests = [
        MagicMock(billing_period="2026-04", etag='"NEW"', last_modified="t2"),
    ]
    changed = _diff_manifests(manifests, state)
    assert [m.billing_period for m in changed] == ["2026-04"]


def test_cmd_status_prints_empty_state(tmp_path, capsys) -> None:
    cfg = _make_cfg(tmp_path)
    rc = cmd_status(cfg, argparse.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert "No state yet" in out
