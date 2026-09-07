from __future__ import annotations

from clickhouse_connect.driver.client import Client


def build_drop_partition_sql(database: str, table: str, billing_period: str) -> str:
    # billing_period는 YYYY-MM 형식만 받음 (호출자가 보장)
    return f"ALTER TABLE {database}.{table} DROP PARTITION '{billing_period}'"


def build_s3_url(
    *, bucket: str, region: str, prefix: str, export_name: str, billing_period: str
) -> str:
    return (
        f"https://{bucket}.s3.{region}.amazonaws.com/"
        f"{prefix}/{export_name}/data/BILLING_PERIOD={billing_period}/*.parquet"
    )


def build_s3_precheck_count_sql() -> str:
    # Parquet 풋터만 읽어 row 수 반환 (스캔 없음). glob이 0개 파일 매칭이면 0 반환.
    return (
        "SELECT count() FROM s3(\n"
        "  {url:String},\n"
        "  {access_key:String},\n"
        "  {secret_key:String},\n"
        "  'Parquet'\n"
        ")"
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
    """파티션을 안전하게 교체하고 row 수 반환.

    이전 구현은 DROP → INSERT 순차라 s3() glob이 0행 반환하면 파티션이
    영구 유실됐다 (8/30·9/6 사고: AWS CUR 매니페스트 재생성 순간).
    사전에 s3() count 로 파일이 실제로 있는지 확인하고, 0이면 예외를 던져
    DROP 자체를 건너뛴다. Parquet count는 풋터만 읽어 거의 무료라
    메모리 오버헤드 없음.
    """
    s3_params = {"url": s3_url, "access_key": access_key, "secret_key": secret_key}
    precheck = client.query(build_s3_precheck_count_sql(), parameters=s3_params)
    s3_row_count = int(precheck.result_rows[0][0])
    if s3_row_count == 0:
        raise RuntimeError(
            f"S3 returned 0 rows for {billing_period}; "
            "refusing to drop main partition (would wipe it)."
        )
    client.command(build_drop_partition_sql(database, table, billing_period))
    client.command(
        build_insert_from_s3_sql(
            database=database, table=table, billing_period=billing_period
        ),
        parameters=s3_params,
    )
    count_sql = (
        f"SELECT count() FROM {database}.{table} "
        "WHERE _billing_period = {bp:String}"
    )
    result = client.query(count_sql, parameters={"bp": billing_period})
    return int(result.result_rows[0][0])
