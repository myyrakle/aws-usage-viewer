import pytest

from datahouse.clickhouse.schema import (
    LOW_CARDINALITY_COLUMNS,
    map_cur_type,
    manifest_to_ddl,
)


def test_map_cur_type_basic() -> None:
    assert map_cur_type("DateTime", "any_col") == "DateTime64(3)"
    assert map_cur_type("OptionalDateTime", "any_col") == "Nullable(DateTime64(3))"
    assert map_cur_type("BigDecimal", "any_col") == "Decimal(38, 12)"
    assert map_cur_type("OptionalBigDecimal", "any_col") == "Nullable(Decimal(38, 12))"
    assert map_cur_type("Interval", "any_col") == "String"
    assert map_cur_type("Json", "any_col") == "String"
    assert map_cur_type("Map<String,String>", "resource_tags") == "Map(String, String)"


def test_map_cur_type_string_low_cardinality() -> None:
    for col in LOW_CARDINALITY_COLUMNS:
        assert map_cur_type("String", col) == "LowCardinality(String)"


def test_map_cur_type_string_normal() -> None:
    assert map_cur_type("String", "line_item_resource_id") == "String"


def test_map_cur_type_optional_string_collapses_to_string() -> None:
    assert map_cur_type("OptionalString", "anything") == "String"


def test_manifest_to_ddl_contains_partition_and_engine() -> None:
    manifest = {
        "dataColumns": [
            {"name": "line_item_usage_start_date", "type": "DateTime"},
            {"name": "line_item_usage_account_id", "type": "String"},
            {"name": "line_item_product_code", "type": "String"},
            {"name": "line_item_resource_id", "type": "OptionalString"},
            {"name": "line_item_unblended_cost", "type": "BigDecimal"},
            {"name": "resource_tags", "type": "Map<String,String>"},
        ]
    }
    ddl = manifest_to_ddl(manifest, "aws_billing", "cur_line_items")

    assert "CREATE TABLE IF NOT EXISTS aws_billing.cur_line_items" in ddl
    assert "`line_item_usage_start_date` DateTime64(3)" in ddl
    assert "`line_item_usage_account_id` LowCardinality(String)" in ddl
    assert "`line_item_product_code` LowCardinality(String)" in ddl
    assert "`line_item_resource_id` String" in ddl
    assert "`line_item_unblended_cost` Decimal(38, 12)" in ddl
    assert "`resource_tags` Map(String, String)" in ddl
    assert "`_billing_period` String" in ddl
    assert "`_ingested_at` DateTime64(3) DEFAULT now64(3)" in ddl
    assert "ENGINE = MergeTree" in ddl
    assert "PARTITION BY _billing_period" in ddl
    assert "ORDER BY" in ddl


def test_manifest_to_ddl_unknown_type_raises() -> None:
    manifest = {"dataColumns": [{"name": "x", "type": "Spaceship"}]}
    with pytest.raises(ValueError, match="Spaceship"):
        manifest_to_ddl(manifest, "db", "t")
