from unittest.mock import MagicMock

import pytest

from curhouse.clickhouse.loader import (
    build_drop_partition_sql,
    build_insert_from_s3_sql,
    build_s3_precheck_count_sql,
    build_s3_url,
    reload_partition,
)


def test_build_drop_partition_sql() -> None:
    sql = build_drop_partition_sql("aws_billing", "cur_line_items", "2026-04")
    assert sql == (
        "ALTER TABLE aws_billing.cur_line_items DROP PARTITION '2026-04'"
    )


def test_build_s3_url_uses_virtual_hosted_style() -> None:
    url = build_s3_url(
        bucket="my-bucket",
        region="us-east-1",
        prefix="cur2",
        export_name="exp",
        billing_period="2026-04",
    )
    assert url == (
        "https://my-bucket.s3.us-east-1.amazonaws.com/"
        "cur2/exp/data/BILLING_PERIOD=2026-04/*.parquet"
    )


def test_build_insert_sql_uses_parameter_placeholders() -> None:
    sql = build_insert_from_s3_sql(
        database="aws_billing",
        table="cur_line_items",
        billing_period="2026-04",
    )
    assert "{url:String}" in sql
    assert "{access_key:String}" in sql
    assert "{secret_key:String}" in sql
    assert "'2026-04' AS _billing_period" in sql
    assert "INSERT INTO aws_billing.cur_line_items" in sql


def test_precheck_reads_parquet_footer_only() -> None:
    # count() 로 Parquet 풋터만 읽어 값 확인 → 스캔·메모리 오버헤드 없음
    sql = build_s3_precheck_count_sql()
    assert "count()" in sql
    assert "FROM s3(" in sql
    assert "{url:String}" in sql
    assert "'Parquet'" in sql


def _fake_client(precheck_rows: int, main_rows: int = 0) -> MagicMock:
    client = MagicMock()
    calls = {"n": 0}

    def _query_side_effect(*args, **kwargs):
        calls["n"] += 1
        result = MagicMock()
        # 1번째 query = s3() precheck, 2번째 query = main table count
        result.result_rows = [(precheck_rows if calls["n"] == 1 else main_rows,)]
        return result

    client.query.side_effect = _query_side_effect
    return client


def test_reload_partition_drops_and_inserts_when_s3_has_rows() -> None:
    client = _fake_client(precheck_rows=200_400, main_rows=200_400)
    count = reload_partition(
        client,
        database="d",
        table="t",
        billing_period="2026-08",
        s3_url="https://x/*.parquet",
        access_key="AK",
        secret_key="SK",
    )
    assert count == 200_400
    commands = [c.args[0] for c in client.command.call_args_list]
    # DROP PARTITION → INSERT (precheck 통과했으므로 진행)
    assert commands[0] == (
        "ALTER TABLE d.t DROP PARTITION '2026-08'"
    )
    assert "INSERT INTO d.t" in commands[1]


def test_reload_partition_raises_and_skips_drop_when_s3_empty() -> None:
    # 8/30·9/6 회귀 테스트: s3() precheck가 0을 반환하면 DROP 자체를 안 함
    client = _fake_client(precheck_rows=0)
    with pytest.raises(RuntimeError, match="S3 returned 0 rows"):
        reload_partition(
            client,
            database="d",
            table="t",
            billing_period="2026-09",
            s3_url="https://x/*.parquet",
            access_key="AK",
            secret_key="SK",
        )
    # DROP PARTITION 절대 호출 안 됨 (원본 보존의 핵심)
    commands = [c.args[0] for c in client.command.call_args_list]
    assert not any("DROP PARTITION" in c for c in commands), (
        f"DROP PARTITION must not run when precheck sees 0 rows; got: {commands}"
    )
    # INSERT도 당연히 안 함
    assert not any("INSERT" in c for c in commands)
