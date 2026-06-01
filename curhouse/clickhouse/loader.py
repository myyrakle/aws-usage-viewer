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
    """파티션 통째 교체 후 row 수 반환."""
    client.command(build_drop_partition_sql(database, table, billing_period))
    client.command(
        build_insert_from_s3_sql(
            database=database, table=table, billing_period=billing_period
        ),
        parameters={
            "url": s3_url,
            "access_key": access_key,
            "secret_key": secret_key,
        },
    )
    count_sql = (
        f"SELECT count() FROM {database}.{table} "
        "WHERE _billing_period = {bp:String}"
    )
    result = client.query(count_sql, parameters={"bp": billing_period})
    return int(result.result_rows[0][0])
