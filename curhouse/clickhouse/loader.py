from __future__ import annotations

from clickhouse_connect.driver.client import Client


def build_s3_url(
    *, bucket: str, region: str, prefix: str, export_name: str, billing_period: str
) -> str:
    return (
        f"https://{bucket}.s3.{region}.amazonaws.com/"
        f"{prefix}/{export_name}/data/BILLING_PERIOD={billing_period}/*.parquet"
    )


def build_insert_from_s3_sql(
    *, database: str, table: str, billing_period: str
) -> str:
    return (
        f"INSERT INTO {database}.{table}\n"
        "SELECT\n"
        "  *,\n"
        f"  '{billing_period}' AS _billing_period,\n"
        "  now64(3) AS _ingested_at\n"
        "FROM s3(\n"
        "  {url:String},\n"
        "  {access_key:String},\n"
        "  {secret_key:String},\n"
        "  'Parquet'\n"
        ")"
    )


def staging_table_name(table: str, billing_period: str) -> str:
    # ClickHouse identifier에 하이픈 불가 → 2026-08 → 2026_08
    return f"{table}__stage_{billing_period.replace('-', '_')}"


def build_drop_stage_table_sql(database: str, stage_table: str) -> str:
    return f"DROP TABLE IF EXISTS {database}.{stage_table}"


def build_create_stage_table_sql(
    database: str, stage_table: str, source_table: str
) -> str:
    # CREATE ... AS 는 ENGINE/PARTITION BY/ORDER BY까지 복제 → REPLACE PARTITION 호환
    return f"CREATE TABLE {database}.{stage_table} AS {database}.{source_table}"


def build_replace_partition_sql(
    database: str, table: str, stage_table: str, billing_period: str
) -> str:
    return (
        f"ALTER TABLE {database}.{table} "
        f"REPLACE PARTITION '{billing_period}' "
        f"FROM {database}.{stage_table}"
    )


def reload_partition(
    client: Client,
    *,
    database: str,
    table: str,
    billing_period: str,
    s3_url: str,
    access_key: str,
    secret_key: str,
) -> int:
    """파티션을 원자적으로 교체하고 row 수 반환.

    이전 구현은 DROP PARTITION → INSERT FROM s3() 순차였고,
    s3() glob이 0개 파일을 매칭하면 예외 없이 0행이 들어와 파티션이 영구 유실됐다
    (AWS CUR이 월별 parquet를 재생성하는 순간 cron이 걸리면 재발).
    스테이징 테이블에 먼저 적재 → 0행이면 예외 → 원본 그대로 두고 종료.
    비-0행이면 REPLACE PARTITION FROM 으로 원자 교체.
    """
    stage = staging_table_name(table, billing_period)
    client.command(build_drop_stage_table_sql(database, stage))
    client.command(build_create_stage_table_sql(database, stage, table))
    try:
        client.command(
            build_insert_from_s3_sql(
                database=database, table=stage, billing_period=billing_period
            ),
            parameters={
                "url": s3_url,
                "access_key": access_key,
                "secret_key": secret_key,
            },
        )
        count_sql = (
            f"SELECT count() FROM {database}.{stage} "
            "WHERE _billing_period = {bp:String}"
        )
        result = client.query(count_sql, parameters={"bp": billing_period})
        row_count = int(result.result_rows[0][0])
        if row_count == 0:
            raise RuntimeError(
                f"S3 returned 0 rows for {billing_period}; "
                "refusing to replace main partition (would wipe it)."
            )
        client.command(
            build_replace_partition_sql(database, table, stage, billing_period)
        )
        return row_count
    finally:
        client.command(build_drop_stage_table_sql(database, stage))
