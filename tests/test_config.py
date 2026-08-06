import os
from pathlib import Path

import pytest

from curhouse.config import Config, load_config


@pytest.fixture
def sample_config_path(tmp_path: Path) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(
        """
[aws]
profile = "test-profile"
region = "us-east-1"
account_id = "111111111111"

[cur]
bucket_name = "test-bucket"
export_name = "test-export"
prefix = "cur2"
time_granularity = "HOURLY"
include_resources = true
include_split_cost_allocation = true

[clickhouse]
host = "ch.local"
port = 8123
user = "default"
password = "filepw"
database = "aws_billing"
table = "cur_line_items"
secure = false

[state]
path = ".state.json"
"""
    )
    return p


def test_load_config_basic(sample_config_path: Path) -> None:
    cfg = load_config(sample_config_path)
    assert isinstance(cfg, Config)
    assert cfg.aws.profile == "test-profile"
    assert cfg.cur.bucket_name == "test-bucket"
    assert cfg.cur.time_granularity == "HOURLY"
    assert cfg.clickhouse.host == "ch.local"
    assert cfg.clickhouse.password == "filepw"
    assert cfg.state.path == ".state.json"


def test_env_password_overrides_file(
    sample_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURHOUSE_CH_PASSWORD", "envpw")
    cfg = load_config(sample_config_path)
    assert cfg.clickhouse.password == "envpw"


def test_invalid_granularity_rejected(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        """
[aws]
profile = "x"
region = "us-east-1"
account_id = "111"
[cur]
bucket_name = "b"
export_name = "e"
prefix = "p"
time_granularity = "WEEKLY"
include_resources = true
include_split_cost_allocation = true
[clickhouse]
host = "h"
port = 8123
user = "u"
password = ""
database = "d"
table = "t"
secure = false
[state]
path = "s"
"""
    )
    with pytest.raises(ValueError, match="time_granularity"):
        load_config(p)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_missing_profile_defaults_empty(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        """
[aws]
region = "us-east-1"
account_id = "111"
[cur]
bucket_name = "b"
export_name = "e"
prefix = "p"
time_granularity = "HOURLY"
include_resources = true
include_split_cost_allocation = true
[clickhouse]
host = "h"
port = 8123
user = "u"
password = ""
database = "d"
table = "t"
secure = false
[state]
path = "s"
"""
    )
    cfg = load_config(p)
    assert cfg.aws.profile == ""


def test_env_ch_host_port_override(
    sample_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURHOUSE_CH_HOST", "clickhouse")
    monkeypatch.setenv("CURHOUSE_CH_PORT", "9000")
    cfg = load_config(sample_config_path)
    assert cfg.clickhouse.host == "clickhouse"
    assert cfg.clickhouse.port == 9000


def test_env_state_path_override(
    sample_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURHOUSE_STATE_PATH", "/state/state.json")
    cfg = load_config(sample_config_path)
    assert cfg.state.path == "/state/state.json"
