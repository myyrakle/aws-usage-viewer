from __future__ import annotations

from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from datahouse.config import ClickhouseConfig


def get_client(cfg: ClickhouseConfig) -> Client:
    # Don't bind to cfg.database — it may not exist yet on first run.
    # All queries qualify database.table explicitly; ensure_database creates it.
    return clickhouse_connect.get_client(
        host=cfg.host,
        port=cfg.port,
        username=cfg.user,
        password=cfg.password,
        secure=cfg.secure,
    )


def ensure_database(client: Client, database: str) -> None:
    client.command(f"CREATE DATABASE IF NOT EXISTS {database}")


def list_table_columns(client: Client, database: str, table: str) -> list[dict[str, Any]]:
    """ClickHouse system.columns에서 현재 테이블 컬럼 정보 조회. 없으면 빈 리스트."""
    result = client.query(
        "SELECT name, type FROM system.columns "
        "WHERE database = {db:String} AND table = {tbl:String} "
        "ORDER BY position",
        parameters={"db": database, "tbl": table},
    )
    return [{"name": row[0], "type": row[1]} for row in result.result_rows]
