from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Granularity = Literal["HOURLY", "DAILY", "MONTHLY"]
ALLOWED_GRANULARITIES: tuple[Granularity, ...] = ("HOURLY", "DAILY", "MONTHLY")


@dataclass(frozen=True)
class AwsConfig:
    profile: str
    region: str
    account_id: str


@dataclass(frozen=True)
class CurConfig:
    bucket_name: str
    export_name: str
    prefix: str
    time_granularity: Granularity
    include_resources: bool
    include_split_cost_allocation: bool


@dataclass(frozen=True)
class ClickhouseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    table: str
    secure: bool


@dataclass(frozen=True)
class StateConfig:
    path: str


@dataclass(frozen=True)
class Config:
    aws: AwsConfig
    cur: CurConfig
    clickhouse: ClickhouseConfig
    state: StateConfig


def load_config(path: Path | str) -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    with path.open("rb") as f:
        data = tomllib.load(f)

    granularity = data["cur"]["time_granularity"]
    if granularity not in ALLOWED_GRANULARITIES:
        raise ValueError(
            f"time_granularity must be one of {ALLOWED_GRANULARITIES}, got {granularity!r}"
        )

    ch_password = os.environ.get("CURHOUSE_CH_PASSWORD", data["clickhouse"]["password"])
    ch_host = os.environ.get("CURHOUSE_CH_HOST", data["clickhouse"]["host"])
    ch_port = int(os.environ.get("CURHOUSE_CH_PORT", data["clickhouse"]["port"]))
    ch_user = os.environ.get("CURHOUSE_CH_USER", data["clickhouse"]["user"])
    state_path = os.environ.get("CURHOUSE_STATE_PATH", data["state"]["path"])

    return Config(
        aws=AwsConfig(
            profile=data["aws"].get("profile", ""),
            region=data["aws"]["region"],
            account_id=str(data["aws"]["account_id"]),
        ),
        cur=CurConfig(**data["cur"]),
        clickhouse=ClickhouseConfig(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_password,
            database=data["clickhouse"]["database"],
            table=data["clickhouse"]["table"],
            secure=bool(data["clickhouse"]["secure"]),
        ),
        state=StateConfig(path=state_path),
    )
