#!/usr/bin/env python3
"""Provision Metabase with the curhouse CUR data source, saved questions,
and the AWS cost dashboard. Idempotent — safe to re-run.

Reads credentials from env vars:
  MB_URL            (default: http://localhost:3000)
  MB_ADMIN_EMAIL    (required)
  MB_ADMIN_PASSWORD (required; min 8 chars on first run)
  MB_FIRST_NAME     (default: admin)
  MB_LAST_NAME      (default: curhouse)
  CH_HOST           (default: host.docker.internal)
  CH_PORT           (default: 8123)
  CH_DB             (default: aws_billing)
  CH_USER           (default: default)
  CH_PASSWORD       (default: empty)

Usage:
    MB_ADMIN_EMAIL=you@example.com MB_ADMIN_PASSWORD='Strong#Pass1' \\
        python3 scripts/provision_metabase.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

MB_URL = os.environ.get("MB_URL", "http://localhost:3000").rstrip("/")
MB_EMAIL = os.environ.get("MB_ADMIN_EMAIL")
MB_PASSWORD = os.environ.get("MB_ADMIN_PASSWORD")
MB_FIRST = os.environ.get("MB_FIRST_NAME", "admin")
MB_LAST = os.environ.get("MB_LAST_NAME", "curhouse")

CH_HOST = os.environ.get("CH_HOST", "host.docker.internal")
CH_PORT = int(os.environ.get("CH_PORT", "8123"))
CH_DB = os.environ.get("CH_DB", "aws_billing")
CH_USER = os.environ.get("CH_USER", "default")
CH_PASSWORD = os.environ.get("CH_PASSWORD", "")

DB_NAME = "curhouse-clickhouse"
DASHBOARD_NAME = "AWS 비용 대시보드"

COMMON_FILTER = "line_item_line_item_type NOT IN ('Credit', 'Tax', 'Refund')"

USD_FMT = {
    "number_style": "currency",
    "currency": "USD",
    "currency_style": "symbol",
    "currency_in_header": False,
}


def _request(method: str, path: str, session: str | None, body=None) -> dict:
    headers = {"Content-Type": "application/json"}
    if session:
        headers["X-Metabase-Session"] = session
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(MB_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} -> {e.code}: {err}") from None


def authenticate() -> str:
    """Return a Metabase session token. Run initial setup if needed."""
    props = _request("GET", "/api/session/properties", None)
    setup_token = props.get("setup-token")
    has_user = props.get("has-user-setup", False)

    if setup_token and not has_user:
        if not MB_EMAIL or not MB_PASSWORD:
            sys.exit(
                "First-time setup: set MB_ADMIN_EMAIL and MB_ADMIN_PASSWORD env vars."
            )
        print(f"  fresh Metabase — running /api/setup as {MB_EMAIL}")
        resp = _request("POST", "/api/setup", None, {
            "token": setup_token,
            "user": {
                "first_name": MB_FIRST,
                "last_name": MB_LAST,
                "email": MB_EMAIL,
                "password": MB_PASSWORD,
                "site_name": "curhouse",
            },
            "prefs": {"site_name": "curhouse", "allow_tracking": "false"},
        })
        return resp["id"]

    if not MB_EMAIL or not MB_PASSWORD:
        sys.exit("Set MB_ADMIN_EMAIL and MB_ADMIN_PASSWORD env vars to authenticate.")
    resp = _request("POST", "/api/session", None, {
        "username": MB_EMAIL,
        "password": MB_PASSWORD,
    })
    return resp["id"]


def upsert_database(session: str) -> int:
    dbs = _request("GET", "/api/database", session).get("data", [])
    existing = next((d for d in dbs if d["name"] == DB_NAME), None)
    payload_details = {
        "host": CH_HOST,
        "port": CH_PORT,
        "user": CH_USER,
        "password": CH_PASSWORD,
        "dbname": CH_DB,
        "ssl": False,
        "tunnel-enabled": False,
    }
    if existing:
        _request("PUT", f"/api/database/{existing['id']}", session, {
            "name": DB_NAME,
            "engine": "clickhouse",
            "details": payload_details,
        })
        print(f"  database[{existing['id']}] updated")
        return existing["id"]
    created = _request("POST", "/api/database", session, {
        "engine": "clickhouse",
        "name": DB_NAME,
        "details": payload_details,
        "is_full_sync": True,
        "is_on_demand": False,
    })
    print(f"  database[{created['id']}] created")
    return created["id"]


def _wait_for_sync(session: str, db_id: int) -> None:
    import time
    for _ in range(30):
        d = _request("GET", f"/api/database/{db_id}", session)
        if d.get("initial_sync_status") == "complete":
            return
        time.sleep(2)
    print("  warning: db sync did not complete within 60s")


def _field_id(session: str, db_id: int, table: str, column: str) -> int:
    meta = _request("GET", f"/api/database/{db_id}/metadata", session)
    for t in meta.get("tables", []):
        if t["name"] != table:
            continue
        for f in t["fields"]:
            if f["name"] == column:
                return f["id"]
    raise RuntimeError(f"field {table}.{column} not found in db {db_id}")


def build_template_tags(
    usage_date_field: int,
    period_field: int,
    service_field: int,
    resource_field: int,
    account_field: int,
) -> dict:
    return {
        "range_days": {
            "id": str(uuid.uuid4()),
            "name": "range_days",
            "display-name": "기간 (범위)",
            "type": "dimension",
            "dimension": ["field", usage_date_field, {"base-type": "type/DateTime"}],
            "widget-type": "date/range",
            "default": "thismonth",
        },
        "month": {
            "id": str(uuid.uuid4()),
            "name": "month",
            "display-name": "월",
            "type": "dimension",
            "dimension": ["field", period_field, None],
            "widget-type": "string/=",
            "default": None,
        },
        "service": {
            "id": str(uuid.uuid4()),
            "name": "service",
            "display-name": "Service",
            "type": "dimension",
            "dimension": ["field", service_field, None],
            "widget-type": "string/=",
            "default": None,
        },
        "resource_id": {
            "id": str(uuid.uuid4()),
            "name": "resource_id",
            "display-name": "Resource",
            "type": "dimension",
            "dimension": ["field", resource_field, None],
            "widget-type": "string/=",
            "default": None,
        },
        "account_id": {
            "id": str(uuid.uuid4()),
            "name": "account_id",
            "display-name": "Account",
            "type": "dimension",
            "dimension": ["field", account_field, None],
            "widget-type": "string/=",
            "default": None,
        },
    }


def card_specs() -> list[dict]:
    """Each card definition: name, display, sql, viz_settings, dashboard layout."""
    cards = [
        {
            "name": "총 사용 비용",
            "display": "scalar",
            "sql": (
                "SELECT round(sum(line_item_unblended_cost), 2) AS usage_cost\n"
                "FROM aws_billing.cur_line_items\n"
                f"WHERE {COMMON_FILTER}\n"
                "  [[AND {{range_days}}]]\n"
                "  [[AND {{month}}]]\n"
                "  [[AND {{service}}]]\n"
                "  [[AND {{resource_id}}]]\n"
                "  [[AND {{account_id}}]]"
            ),
            "viz": {"column_settings": {json.dumps(["name", "usage_cost"]): USD_FMT}},
            "layout": (0, 0, 8, 4),
        },
        {
            "name": "서비스별 비용 Top 10",
            "display": "bar",
            "sql": (
                "SELECT line_item_product_code AS service,\n"
                "       round(sum(line_item_unblended_cost), 2) AS cost\n"
                "FROM aws_billing.cur_line_items\n"
                f"WHERE {COMMON_FILTER}\n"
                "  [[AND {{range_days}}]]\n"
                "  [[AND {{month}}]]\n"
                "  [[AND {{service}}]]\n"
                "  [[AND {{resource_id}}]]\n"
                "  [[AND {{account_id}}]]\n"
                "GROUP BY service\n"
                "ORDER BY cost DESC\n"
                "LIMIT 10"
            ),
            "viz": {
                "graph.dimensions": ["service"],
                "graph.metrics": ["cost"],
                "column_settings": {json.dumps(["name", "cost"]): USD_FMT},
            },
            "layout": (8, 0, 16, 6),
        },
        {
            "name": "일별 비용 추이",
            "display": "line",
            "sql": (
                "SELECT toDate(line_item_usage_start_date) AS day,\n"
                "       round(sum(line_item_unblended_cost), 2) AS cost\n"
                "FROM aws_billing.cur_line_items\n"
                f"WHERE {COMMON_FILTER}\n"
                "  [[AND {{range_days}}]]\n"
                "  [[AND {{month}}]]\n"
                "  [[AND {{service}}]]\n"
                "  [[AND {{resource_id}}]]\n"
                "  [[AND {{account_id}}]]\n"
                "GROUP BY day\n"
                "ORDER BY day"
            ),
            "viz": {
                "graph.dimensions": ["day"],
                "graph.metrics": ["cost"],
                "column_settings": {json.dumps(["name", "cost"]): USD_FMT},
            },
            "layout": (8, 6, 16, 6),
        },
        {
            "name": "계정별 비용",
            "display": "bar",
            "sql": (
                "SELECT line_item_usage_account_id AS account,\n"
                "       round(sum(line_item_unblended_cost), 2) AS cost\n"
                "FROM aws_billing.cur_line_items\n"
                f"WHERE {COMMON_FILTER}\n"
                "  [[AND {{range_days}}]]\n"
                "  [[AND {{month}}]]\n"
                "  [[AND {{service}}]]\n"
                "  [[AND {{resource_id}}]]\n"
                "  [[AND {{account_id}}]]\n"
                "GROUP BY account\n"
                "ORDER BY cost DESC"
            ),
            "viz": {
                "graph.dimensions": ["account"],
                "graph.metrics": ["cost"],
                "column_settings": {json.dumps(["name", "cost"]): USD_FMT},
            },
            "layout": (0, 4, 8, 8),
        },
        {
            "name": "리소스별 비용 Top 20",
            "display": "table",
            "sql": (
                "SELECT line_item_resource_id AS resource,\n"
                "       line_item_product_code AS service,\n"
                "       round(sum(line_item_unblended_cost), 2) AS cost\n"
                "FROM aws_billing.cur_line_items\n"
                f"WHERE {COMMON_FILTER}\n"
                "  AND line_item_resource_id != ''\n"
                "  [[AND {{range_days}}]]\n"
                "  [[AND {{month}}]]\n"
                "  [[AND {{service}}]]\n"
                "  [[AND {{resource_id}}]]\n"
                "  [[AND {{account_id}}]]\n"
                "GROUP BY resource, service\n"
                "ORDER BY cost DESC\n"
                "LIMIT 20"
            ),
            "viz": {"column_settings": {json.dumps(["name", "cost"]): USD_FMT}},
            "layout": (0, 12, 24, 8),
        },
        {
            "name": "user_Project 태그별 비용",
            "display": "bar",
            "sql": (
                "SELECT resource_tags['user_Project'] AS project,\n"
                "       round(sum(line_item_unblended_cost), 2) AS cost\n"
                "FROM aws_billing.cur_line_items\n"
                f"WHERE {COMMON_FILTER}\n"
                "  AND resource_tags['user_Project'] != ''\n"
                "  [[AND {{range_days}}]]\n"
                "  [[AND {{month}}]]\n"
                "  [[AND {{service}}]]\n"
                "  [[AND {{resource_id}}]]\n"
                "  [[AND {{account_id}}]]\n"
                "GROUP BY project\n"
                "ORDER BY cost DESC"
            ),
            "viz": {
                "graph.dimensions": ["project"],
                "graph.metrics": ["cost"],
                "column_settings": {json.dumps(["name", "cost"]): USD_FMT},
            },
            "layout": (0, 20, 24, 6),
        },
        {
            "name": "서비스 상세 (usage_type · operation)",
            "display": "table",
            "sql": (
                "SELECT line_item_usage_type AS usage_type,\n"
                "       line_item_operation AS operation,\n"
                "       round(sum(line_item_usage_amount), 4) AS usage_amount,\n"
                "       round(sum(line_item_unblended_cost), 2) AS cost\n"
                "FROM aws_billing.cur_line_items\n"
                f"WHERE {COMMON_FILTER}\n"
                "  [[AND {{range_days}}]]\n"
                "  [[AND {{month}}]]\n"
                "  [[AND {{service}}]]\n"
                "  [[AND {{resource_id}}]]\n"
                "  [[AND {{account_id}}]]\n"
                "GROUP BY usage_type, operation\n"
                "ORDER BY cost DESC\n"
                "LIMIT 100"
            ),
            "viz": {"column_settings": {json.dumps(["name", "cost"]): USD_FMT}},
            "layout": (0, 26, 24, 10),
        },
    ]
    return cards


def upsert_card(session: str, db_id: int, tags: dict, spec: dict) -> int:
    """Find a card by name and update, or create new."""
    existing = _request("GET", "/api/card", session)
    # /api/card returns a list (not wrapped); but some versions wrap in {"data": ...}
    cards = existing if isinstance(existing, list) else existing.get("data", [])
    found = next((c for c in cards if c["name"] == spec["name"]), None)

    body = {
        "name": spec["name"],
        "display": spec["display"],
        "dataset_query": {
            "type": "native",
            "native": {"query": spec["sql"], "template-tags": tags},
            "database": db_id,
        },
        "visualization_settings": spec["viz"],
    }
    if found:
        _request("PUT", f"/api/card/{found['id']}", session, body)
        print(f"  card[{found['id']}] {spec['name']!r} updated")
        return found["id"]
    body["dataset_query"]["database"] = db_id
    created = _request("POST", "/api/card", session, body)
    print(f"  card[{created['id']}] {spec['name']!r} created")
    return created["id"]


def upsert_dashboard(
    session: str, specs: list[dict], card_ids: list[int]
) -> int:
    layouts = [s["layout"] for s in specs]
    dashboards = _request("GET", "/api/dashboard", session)
    items = dashboards if isinstance(dashboards, list) else dashboards.get("data", [])
    found = next((d for d in items if d["name"] == DASHBOARD_NAME), None)

    if found:
        dash_id = found["id"]
        print(f"  dashboard[{dash_id}] {DASHBOARD_NAME!r} updating")
    else:
        created = _request("POST", "/api/dashboard", session, {
            "name": DASHBOARD_NAME,
            "description": "CUR 2.0 기반 월별 비용 분석 (curhouse)",
        })
        dash_id = created["id"]
        print(f"  dashboard[{dash_id}] {DASHBOARD_NAME!r} created")

    range_pid = str(uuid.uuid4())[:8]
    month_pid = str(uuid.uuid4())[:8]
    service_pid = str(uuid.uuid4())[:8]
    resource_pid = str(uuid.uuid4())[:8]
    account_pid = str(uuid.uuid4())[:8]
    parameters = [
        {
            "id": range_pid,
            "name": "기간 (범위)",
            "slug": "range_days",
            "type": "date/range",
            "sectionId": "date",
            "default": "thismonth",
        },
        {
            "id": month_pid,
            "name": "월",
            "slug": "month",
            "type": "string/=",
            "sectionId": "string",
            "values_query_type": "list",
            "isMultiSelect": False,
        },
        {
            "id": service_pid,
            "name": "서비스",
            "slug": "service",
            "type": "string/=",
            "sectionId": "string",
            "values_query_type": "list",
            "isMultiSelect": False,
        },
        {
            "id": resource_pid,
            "name": "리소스",
            "slug": "resource_id",
            "type": "string/=",
            "sectionId": "string",
            # high-cardinality ARNs: search by typing rather than dropdown
            "values_query_type": "search",
            "isMultiSelect": False,
        },
        {
            "id": account_pid,
            "name": "계정",
            "slug": "account_id",
            "type": "string/=",
            "sectionId": "string",
            "values_query_type": "list",
            "isMultiSelect": False,
        },
    ]

    mapping_pairs = [
        (range_pid, "range_days"),
        (month_pid, "month"),
        (service_pid, "service"),
        (resource_pid, "resource_id"),
        (account_pid, "account_id"),
    ]

    # Card-name → (param_id, source_column_name) for crossfilter click behavior
    crossfilter_by_card = {
        "서비스별 비용 Top 10": (service_pid, "service"),
        "리소스별 비용 Top 20": (resource_pid, "resource"),
        "계정별 비용": (account_pid, "account"),
    }
    id_to_name = {cid: spec["name"] for spec, cid in zip(specs, card_ids)}

    dashcards = []
    for i, (cid, (col, row, sx, sy)) in enumerate(zip(card_ids, layouts)):
        viz = {}
        cross = crossfilter_by_card.get(id_to_name.get(cid))
        if cross:
            param_id, col_name = cross
            viz["click_behavior"] = {
                "type": "crossfilter",
                "parameterMapping": {
                    param_id: {
                        "id": param_id,
                        "source": {
                            "type": "column",
                            "id": col_name,
                            "name": col_name,
                        },
                        "target": {
                            "type": "parameter",
                            "id": param_id,
                        },
                    }
                },
            }
        dashcards.append({
            "id": -(i + 1),
            "card_id": cid,
            "col": col,
            "row": row,
            "size_x": sx,
            "size_y": sy,
            "parameter_mappings": [
                {
                    "parameter_id": pid,
                    "card_id": cid,
                    "target": ["dimension", ["template-tag", tag]],
                }
                for pid, tag in mapping_pairs
            ],
            "visualization_settings": viz,
        })

    _request("PUT", f"/api/dashboard/{dash_id}", session, {
        "parameters": parameters,
        "dashcards": dashcards,
    })
    print(f"  dashboard[{dash_id}] wired {len(dashcards)} cards + {len(parameters)} params")
    return dash_id


def main() -> int:
    print(f"Connecting to {MB_URL}")
    session = authenticate()
    print("  authenticated")

    db_id = upsert_database(session)
    _wait_for_sync(session, db_id)

    usage_date_field = _field_id(session, db_id, "cur_line_items", "line_item_usage_start_date")
    period_field = _field_id(session, db_id, "cur_line_items", "_billing_period")
    service_field = _field_id(session, db_id, "cur_line_items", "line_item_product_code")
    resource_field = _field_id(session, db_id, "cur_line_items", "line_item_resource_id")
    account_field = _field_id(session, db_id, "cur_line_items", "line_item_usage_account_id")
    print(
        f"  fields: usage_start={usage_date_field}, period={period_field}, "
        f"service={service_field}, resource={resource_field}, account={account_field}"
    )

    tags = build_template_tags(
        usage_date_field, period_field, service_field, resource_field, account_field
    )
    specs = card_specs()

    card_ids = [upsert_card(session, db_id, tags, s) for s in specs]

    dash_id = upsert_dashboard(session, specs, card_ids)

    print("  triggering field-value rescan…")
    _request("POST", f"/api/database/{db_id}/rescan_values", session)

    print(f"\nDone. Dashboard: {MB_URL}/dashboard/{dash_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
