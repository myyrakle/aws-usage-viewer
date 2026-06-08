"""Athena 쿼리 히스토리 CSV를 ClickHouse(athena_audit.query_history)로 적재.

Athena 콘솔 → 쿼리 히스토리 export CSV를 받아, 비용 분석이 가능하도록 정규화한다.

- 스캔량(MB/GB/KB/TB) → bytes 로 정규화
- 비용 = Athena 과금 규칙(쿼리당 최소 10MB, MB 단위 올림) 기준 $5/TB(=1024^4 bytes)
- "쿼리 패턴" = 리터럴(문자열·숫자) 제거한 정규화 지문(pattern_id)으로 묶음
- 시작시간의 +09:00(KST) 기준 wall-clock 으로 저장 → query_date 는 한국 로컬 날짜

전체 재적재(idempotent): 테이블을 drop 후 재생성한다.

사용법:
    uv run python scripts/load_athena_queries.py [csv_path]   # default: query.csv
환경변수: CURHOUSE_CH_PASSWORD (config.toml 의 password 를 덮어씀)
"""

from __future__ import annotations

import csv
import hashlib
import math
import re
import sys
from datetime import datetime
from pathlib import Path

import clickhouse_connect

from curhouse.config import load_config

CSV_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("query.csv")
DB = "athena_audit"
TABLE = "query_history"

_MIB = 1024 * 1024
_TIB = 1024**4
_UNIT = {"B": 1, "KB": 1024, "MB": _MIB, "GB": 1024**3, "TB": _TIB}
_NUM_UNIT_RE = re.compile(r"([\d.]+)\s*([A-Za-z]+)")
_TABLE_RE = re.compile(r"(?:from|join|into|update|table)\s+([`\"\w.]+)", re.IGNORECASE)
_FIRST_KW_RE = re.compile(r"[a-z]+")


def parse_size_to_bytes(s: str) -> int:
    m = _NUM_UNIT_RE.search(s.strip())
    if not m:
        return 0
    val, unit = float(m.group(1)), m.group(2).upper()
    return int(val * _UNIT.get(unit, 1))


def parse_time_to_ms(s: str) -> int:
    m = _NUM_UNIT_RE.search(s.strip())
    if not m:
        return 0
    val, unit = float(m.group(1)), m.group(2).lower()
    factor = {"ms": 1, "sec": 1000, "s": 1000, "min": 60000}.get(unit, 1)
    return int(val * factor)


def billed_cost_usd(scanned_bytes: int) -> float:
    """Athena: MB 단위 올림, 쿼리당 최소 10MB, $5 / 1024^4 bytes."""
    billed = max(10 * _MIB, math.ceil(scanned_bytes / _MIB) * _MIB)
    return billed / _TIB * 5.0


def normalize_query(q: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", q, flags=re.DOTALL)  # block comments
    s = re.sub(r"--[^\n]*", " ", s)  # line comments
    s = s.lower()
    s = re.sub(r"'(?:''|[^'])*'", "?", s)  # string literals
    s = re.sub(r"\b\d+(?:\.\d+)?\b", "?", s)  # numbers
    s = re.sub(r"\s+", " ", s).strip()
    return s


def statement_type(norm: str) -> str:
    m = _FIRST_KW_RE.match(norm)
    kw = m.group(0).upper() if m else "?"
    if kw == "WITH":  # CTE: 뒤따르는 본 구문 타입을 찾아본다
        m2 = re.search(r"\b(select|insert|update|delete|merge|create)\b", norm)
        if m2:
            return m2.group(1).upper()
    return kw


def target_table(q: str) -> str:
    m = _TABLE_RE.search(q)
    if not m:
        return ""
    return m.group(1).strip('`"').lower()


def main() -> int:
    if not CSV_PATH.exists():
        sys.exit(f"CSV not found: {CSV_PATH}")

    cfg = load_config("config.toml").clickhouse
    client = clickhouse_connect.get_client(
        host=cfg.host, port=cfg.port, username=cfg.user,
        password=cfg.password, secure=cfg.secure,
    )

    client.command(f"CREATE DATABASE IF NOT EXISTS {DB}")
    client.command(f"DROP TABLE IF EXISTS {DB}.{TABLE}")
    client.command(
        f"CREATE TABLE {DB}.{TABLE} (\n"
        "  execution_id String,\n"
        "  start_time DateTime64(3),\n"
        "  query_date Date,\n"
        "  status LowCardinality(String),\n"
        "  statement_type LowCardinality(String),\n"
        "  target_table String,\n"
        "  pattern_id String,\n"
        "  pattern_preview String,\n"
        "  query_sample String,\n"
        "  query_full String,\n"
        "  exec_ms UInt32,\n"
        "  scanned_bytes UInt64,\n"
        "  cost_usd Float64,\n"
        "  engine_version LowCardinality(String),\n"
        "  encryption LowCardinality(String)\n"
        ") ENGINE = MergeTree ORDER BY (query_date, pattern_id)"
    )

    cols = [
        "execution_id", "start_time", "query_date", "status", "statement_type",
        "target_table", "pattern_id", "pattern_preview", "query_sample",
        "query_full", "exec_ms", "scanned_bytes", "cost_usd",
        "engine_version", "encryption",
    ]

    rows = []
    skipped = 0
    with CSV_PATH.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        for r in reader:
            if len(r) < 8:
                skipped += 1
                continue
            eid, query, start, status, et, scan, eng, enc = r[:8]
            try:
                dt = datetime.fromisoformat(start).replace(tzinfo=None)
            except ValueError:
                skipped += 1
                continue
            scanned = parse_size_to_bytes(scan)
            norm = normalize_query(query)
            pid = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]
            sample = re.sub(r"\s+", " ", query).strip()[:300]
            rows.append([
                eid, dt, dt.date(), status, statement_type(norm),
                target_table(query), pid, norm[:200], sample, query,
                parse_time_to_ms(et), scanned, round(billed_cost_usd(scanned), 6),
                eng, enc,
            ])

    client.insert(f"{DB}.{TABLE}", rows, column_names=cols)
    total_cost = sum(row[12] for row in rows)
    print(f"✓ Loaded {len(rows):,} queries into {DB}.{TABLE} (skipped {skipped})")
    print(f"  total estimated Athena cost: ${total_cost:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
