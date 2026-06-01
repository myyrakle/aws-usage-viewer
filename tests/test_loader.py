from curhouse.clickhouse.loader import (
    build_drop_partition_sql,
    build_insert_from_s3_sql,
    build_s3_url,
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
