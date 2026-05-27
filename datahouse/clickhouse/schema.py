from __future__ import annotations

import hashlib

LOW_CARDINALITY_COLUMNS: frozenset[str] = frozenset({
    "product_servicecode",
    "product_region",
    "product_instance_type",
    "product_operation",
    "line_item_product_code",
    "line_item_usage_type",
    "line_item_line_item_type",
    "bill_billing_entity",
    "bill_payer_account_id",
    "line_item_usage_account_id",
})

_ORDER_BY_COLUMNS = (
    "line_item_usage_start_date",
    "line_item_product_code",
    "line_item_usage_account_id",
    "line_item_resource_id",
)


def map_cur_type(cur_type: str, column_name: str) -> str:
    """CUR 2.0 manifest 타입을 ClickHouse 타입으로 매핑."""
    if cur_type == "String":
        if column_name in LOW_CARDINALITY_COLUMNS:
            return "LowCardinality(String)"
        return "String"
    if cur_type == "OptionalString":
        return "String"  # NULL을 빈 문자열로
    if cur_type == "DateTime":
        return "DateTime64(3)"
    if cur_type == "OptionalDateTime":
        return "Nullable(DateTime64(3))"
    if cur_type == "BigDecimal":
        return "Decimal(38, 12)"
    if cur_type == "OptionalBigDecimal":
        return "Nullable(Decimal(38, 12))"
    if cur_type == "Interval":
        return "String"
    if cur_type == "Json":
        return "String"
    if cur_type == "Map<String,String>":
        return "Map(String, String)"
    raise ValueError(f"Unknown CUR type: {cur_type!r}")


def manifest_to_ddl(manifest: dict, database: str, table: str) -> str:
    columns = manifest.get("dataColumns", [])
    if not columns:
        raise ValueError("manifest has no dataColumns")

    column_lines: list[str] = []
    for col in columns:
        name = col["name"]
        ch_type = map_cur_type(col["type"], name)
        column_lines.append(f"  `{name}` {ch_type}")

    column_lines.append("  `_billing_period` String")
    column_lines.append("  `_ingested_at` DateTime64(3) DEFAULT now64(3)")

    order_by = ", ".join(_ORDER_BY_COLUMNS)

    return (
        f"CREATE TABLE IF NOT EXISTS {database}.{table} (\n"
        + ",\n".join(column_lines)
        + "\n)\n"
        + "ENGINE = MergeTree\n"
        + "PARTITION BY _billing_period\n"
        + f"ORDER BY ({order_by})\n"
        + "SETTINGS index_granularity = 8192"
    )


def ddl_hash(ddl: str) -> str:
    return "sha256:" + hashlib.sha256(ddl.encode("utf-8")).hexdigest()
