# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for chat artifact builders."""

from src.api.blueprints.chat.artifacts import (
    DEFAULT_MAX_SLICES,
    DEFAULT_MAX_STACKS,
    DEFAULT_MAX_TABLE_ROWS,
    DEFAULT_MAX_X_BUCKETS,
    build_donut_from_aggregate,
    build_stacked_bar_from_aggregate,
    build_table_from_aggregate,
    build_table_from_asset,
    build_table_from_assets,
    validate_envelope,
)


def _result(rows, data, grand_total, columns=None):
    return {
        "rows": list(rows),
        "columns": list(columns or []),
        "data": {r: {"count": c} for r, c in data.items()},
        "grand_total": grand_total,
    }


# --- Negative cases (no artifact) -----------------------------------------

def test_empty_rows_returns_none():
    result = _result([], {}, 0)
    assert build_donut_from_aggregate(result, "category") is None


def test_two_dimensional_returns_none():
    # columns non-empty => 2-D pivot; v1 donut only covers 1-D.
    result = _result(
        ["server"], {"server": 3}, 3, columns=["win", "linux"],
    )
    assert build_donut_from_aggregate(result, "category") is None


def test_zero_grand_total_returns_none():
    result = _result(["server"], {"server": 0}, 0)
    assert build_donut_from_aggregate(result, "category") is None


def test_missing_grand_total_returns_none():
    assert build_donut_from_aggregate(
        {"rows": ["server"], "columns": [], "data": {"server": {"count": 1}}},
        "category",
    ) is None


def test_none_result_returns_none():
    assert build_donut_from_aggregate({}, "category") is None


def test_all_rows_zero_with_nonzero_total_returns_none():
    # Pathological: grand_total disagrees with per-row counts. Never ship
    # an empty chart.
    raw = {
        "rows": ["server", "workstation"],
        "columns": [],
        "data": {"server": {"count": 0}, "workstation": {"count": 0}},
        "grand_total": 5,
    }
    assert build_donut_from_aggregate(raw, "category") is None


# --- Positive cases -------------------------------------------------------

def test_single_slice_builds_minimal_envelope():
    result = _result(["server"], {"server": 5}, 5)
    out = build_donut_from_aggregate(result, "category")
    assert out is not None
    assert out["type"] == "donut"
    assert out["title"] == "Assets by Category"
    assert out["series"] == [{"label": "server", "value": 5}]
    assert out["meta"]["total"] == 5
    assert out["meta"]["other_count"] == 0
    assert out["meta"]["source_tool"] == "aggregate_assets"
    assert out["meta"]["row_field"] == "category"


def test_exactly_max_slices_produces_no_other_bucket():
    data = {
        f"row{i}": (DEFAULT_MAX_SLICES - i) for i in range(DEFAULT_MAX_SLICES)
    }
    result = _result(data.keys(), data, sum(data.values()))
    out = build_donut_from_aggregate(result, "category")
    assert out is not None
    assert len(out["series"]) == DEFAULT_MAX_SLICES
    assert all(s["label"] != "Other" for s in out["series"])
    assert out["meta"]["other_count"] == 0


def test_one_past_max_slices_rolls_last_into_other():
    data = {
        f"row{i}": (DEFAULT_MAX_SLICES + 1 - i)
        for i in range(DEFAULT_MAX_SLICES + 1)
    }
    result = _result(data.keys(), data, sum(data.values()))
    out = build_donut_from_aggregate(result, "category")
    assert out is not None
    # 6 top slices + 1 "Other"
    assert len(out["series"]) == DEFAULT_MAX_SLICES + 1
    assert out["series"][-1]["label"] == "Other"
    assert out["series"][-1]["value"] == 1  # the sole tail row's count
    assert out["meta"]["other_count"] == 1


def test_many_slices_rolls_all_tail_into_one_other_slice():
    # 20 rows -> 6 top + 1 "Other" summing the remaining 14
    counts = list(range(20, 0, -1))  # 20, 19, ... 1
    data = {f"row{i}": c for i, c in enumerate(counts)}
    result = _result(data.keys(), data, sum(counts))
    out = build_donut_from_aggregate(result, "category")
    assert out is not None
    assert len(out["series"]) == DEFAULT_MAX_SLICES + 1
    # Top-6 by count = 20,19,18,17,16,15; "Other" = sum(14..1) = 105
    assert out["series"][-1] == {"label": "Other", "value": 105}
    assert out["meta"]["other_count"] == 14


def test_series_sorted_desc_by_value():
    # Out-of-order input should come out sorted so the top slice is first.
    raw = {
        "rows": ["c", "a", "b"],
        "columns": [],
        "data": {
            "c": {"count": 1},
            "a": {"count": 10},
            "b": {"count": 5},
        },
        "grand_total": 16,
    }
    out = build_donut_from_aggregate(raw, "category")
    assert out is not None
    assert [s["label"] for s in out["series"]] == ["a", "b", "c"]


def test_tail_all_zero_does_not_add_empty_other_bucket():
    # Tail rows exist but all count=0 — no meaningful "Other" slice.
    data = {f"row{i}": 5 for i in range(DEFAULT_MAX_SLICES)}
    data["zero1"] = 0
    data["zero2"] = 0
    result = _result(data.keys(), data, DEFAULT_MAX_SLICES * 5)
    out = build_donut_from_aggregate(result, "category")
    assert out is not None
    assert all(s["label"] != "Other" for s in out["series"])
    assert out["meta"]["other_count"] == 0


def test_snake_case_row_field_resolves_to_catalog_display_name():
    # Catalog lookup must preserve acronym casing — "os_normalized"
    # resolves to "OS Normalized", not ".title()"'s "Os Normalized".
    result = _result(["windows"], {"windows": 3}, 3)
    out = build_donut_from_aggregate(result, "os_normalized")
    assert out is not None
    assert out["title"] == "Assets by OS Normalized"
    # row_field meta is preserved verbatim for debugging.
    assert out["meta"]["row_field"] == "os_normalized"


def test_display_name_row_field_preserved_as_is():
    result = _result(["workstation"], {"workstation": 3}, 3)
    out = build_donut_from_aggregate(result, "OS Normalized")
    assert out is not None
    assert out["title"] == "Assets by OS Normalized"


def test_unknown_field_falls_back_to_title_case():
    # For fields outside the catalog (ext:/tag:/ad-hoc), fall back to
    # naive snake→title so the chart still renders readable text.
    result = _result(["x"], {"x": 1}, 1)
    out = build_donut_from_aggregate(result, "totally_made_up_field")
    assert out is not None
    assert out["title"] == "Assets by Totally Made Up Field"


def test_custom_max_slices_respected():
    counts = list(range(10, 0, -1))
    data = {f"row{i}": c for i, c in enumerate(counts)}
    result = _result(data.keys(), data, sum(counts))
    out = build_donut_from_aggregate(result, "category", max_slices=3)
    assert out is not None
    assert len(out["series"]) == 4  # 3 top + 1 Other
    assert out["series"][-1]["label"] == "Other"
    assert out["meta"]["other_count"] == 7


# --- Stacked bar: helpers and tests ---------------------------------------

def _pivot(rows, cols, data, grand_total):
    """Build a 2-D pivot result. ``data`` is {row: {col: count}}."""
    return {"rows": list(rows), "columns": list(cols),
            "data": {r: dict(d) for r, d in data.items()},
            "grand_total": grand_total}


def test_stacked_bar_one_dim_returns_none():
    r = _pivot(["server"], [], {"server": {"count": 5}}, 5)
    assert build_stacked_bar_from_aggregate(r, "category", "os") is None


def test_stacked_bar_empty_returns_none():
    assert build_stacked_bar_from_aggregate({}, "category", "os") is None


def test_stacked_bar_basic_shape():
    r = _pivot(["server", "workstation"], ["win", "linux"], {
        "server": {"win": 3, "linux": 2},
        "workstation": {"win": 4, "linux": 1},
    }, 10)
    out = build_stacked_bar_from_aggregate(r, "category", "os_normalized")
    assert out["type"] == "stacked_bar"
    assert out["title"] == "Assets by Category × OS Normalized"
    # series: list of {x_label, stacks: [{label, value}, ...]}
    assert out["series"] == [
        {"x_label": "server",      "stacks": [
            {"label": "win", "value": 3}, {"label": "linux", "value": 2},
        ]},
        {"x_label": "workstation", "stacks": [
            {"label": "win", "value": 4}, {"label": "linux", "value": 1},
        ]},
    ]
    assert out["meta"]["source_tool"] == "aggregate_assets"
    assert out["meta"]["row_field"] == "category"
    assert out["meta"]["col_field"] == "os_normalized"
    assert out["meta"]["total"] == 10


def test_stacked_bar_caps_columns_at_max_stacks():
    cols = [f"c{i}" for i in range(DEFAULT_MAX_STACKS * 2)]
    data = {
        "server": {c: (DEFAULT_MAX_STACKS * 2 - i) for i, c in enumerate(cols)}
    }
    r = _pivot(["server"], cols, data, sum(data["server"].values()))
    out = build_stacked_bar_from_aggregate(r, "category", "os")
    # tail rolls into "Other".
    # top slices + 1 "Other"
    assert len(out["series"][0]["stacks"]) == DEFAULT_MAX_STACKS + 1
    assert out["series"][0]["stacks"][-1]["label"] == "Other"


def test_stacked_bar_caps_rows_at_max_x():
    rows = [f"r{i}" for i in range(DEFAULT_MAX_X_BUCKETS * 2)]
    data = {
        r: {"win": (DEFAULT_MAX_X_BUCKETS * 2 - i)} for i, r in enumerate(rows)
    }
    result = _pivot(rows, ["win"], data, sum(d["win"] for d in data.values()))
    out = build_stacked_bar_from_aggregate(result, "category", "os")
    # tail rolls into "Other" x_label.
    assert len(out["series"]) == DEFAULT_MAX_X_BUCKETS + 1
    assert out["series"][-1]["x_label"] == "Other"


# --- build_table_from_asset --------------------------------------------------

def test_table_from_single_asset_keyvalue_shape():
    asset = {
        "hostname": ["host1.example"], "ip": ["10.0.0.1"],
        "category": "server", "subcategory": "physical",
        "owner": "Alice", "confidence": 0.92,
    }
    out = build_table_from_asset(asset, asset_id="abc")
    assert out["type"] == "table"
    assert out["meta"]["source_tool"] == "get_asset"
    assert out["meta"]["layout"] == "keyvalue"
    assert out["meta"]["asset_id"] == "abc"
    # columns: ["Field", "Value"]
    assert out["columns"] == [
        {"key": "field", "label": "Field"},
        {"key": "value", "label": "Value"},
    ]
    labels = [r["field"] for r in out["series"]]
    assert "Hostname" in labels
    assert "Category" in labels
    # The asset's subcategory was provided — it should show up.
    assert "Subcategory" in labels


def test_table_from_single_asset_skips_empty_fields():
    asset = {"hostname": ["host1"], "category": None,
             "owner": "", "subcategory": []}
    out = build_table_from_asset(asset, asset_id="abc")
    labels = [r["field"] for r in out["series"]]
    # hostname present, others (None / '' / []) skipped.
    assert "Hostname" in labels
    assert "Category" not in labels
    assert "Owner" not in labels
    assert "Subcategory" not in labels


def test_table_from_single_asset_returns_none_when_missing():
    assert build_table_from_asset(None, asset_id="abc") is None


def test_table_from_single_asset_returns_none_when_empty():
    # All fields missing => no rows => return None
    assert build_table_from_asset({}, asset_id="abc") is None


# --- build_table_from_asset rich rows (UI fidelity) --------------------------

_RICH_ASSET = {
    "hostname": ["host01"], "ip": ["10.0.0.5", "8.8.8.8"],
    "category": "workstation", "mac_vendor_enriched": "Dell Inc.",
    "extended_attributes": {"AssetAtlas": {
        "ip_private": ["10.0.0.5"], "ip_public": ["8.8.8.8"],
        "geo_countries": ["United States"], "geo_regions": ["California"],
        "geo_cities": ["Mountain View"]}},
}
_RICH_PROVIDERS = [
    {"data_source": "CrowdStrike", "source_name": "CrowdStrike Falcon",
     "source_type": "edr", "source_category": "Endpoint",
     "last_sync_at": "2026-04-18T08:00:00", "hostname": "host01",
     "data_source_fields": {"cpu_model": "Xeon Gold", "ram_gb": 64}},
]


def test_table_from_asset_rich_rows_when_providers_given():
    """With providers, the key-value artifact gains the UI-fidelity rows: the
    private/public IP split, geo, MAC vendor, tags, the posture matrix, and
    extended-attribute VALUES (not a field count)."""
    out = build_table_from_asset(
        _RICH_ASSET, asset_id="abc", providers=_RICH_PROVIDERS,
        tags=[{"tag_name": "pii"}],
    )
    fields = {r["field"]: r["value"] for r in out["series"]}
    assert fields.get("Private IP") == "10.0.0.5"
    assert fields.get("Public IP") == "8.8.8.8"
    assert fields.get("Country") == "United States"
    assert fields.get("MAC Vendor") == "Dell Inc."
    assert fields.get("Tags") == "pii"
    assert any(r["field"].startswith("Posture:") for r in out["series"])
    # the extended-attribute VALUE itself is present (not "2 fields")
    assert any(r["value"] == "Xeon Gold" for r in out["series"])


def test_table_from_asset_backcompat_no_providers_unchanged():
    """The old 2-arg call (no providers, no extended_attributes) gets only the
    base key-value rows — no posture / no IP-split — so existing callers and the
    MCP path are unaffected."""
    asset = {"hostname": ["h1"], "category": "server", "owner": "Alice"}
    out = build_table_from_asset(asset, asset_id="abc")
    fields = [r["field"] for r in out["series"]]
    assert "Hostname" in fields and "Category" in fields
    assert not any(f.startswith("Posture:") for f in fields)
    assert "Private IP" not in fields


def test_table_from_asset_field_search_bounds_ext_attr_rows():
    out = build_table_from_asset(
        _RICH_ASSET, asset_id="abc", providers=_RICH_PROVIDERS,
        field_search="cpu",
    )
    vals = [r["value"] for r in out["series"]]
    assert "Xeon Gold" in vals     # cpu_model kept
    assert "64" not in vals        # ram_gb bounded out


def test_table_from_asset_source_bounds_provider_rows():
    providers = _RICH_PROVIDERS + [
        {"data_source": "Intune", "source_name": "Microsoft Intune",
         "source_type": "mdm",
         "data_source_fields": {"compliance_state": "compliant"}},
    ]
    out = build_table_from_asset(
        _RICH_ASSET, asset_id="abc", providers=providers, source="CrowdStrike",
    )
    vals = [r["value"] for r in out["series"]]
    assert "Xeon Gold" in vals
    assert "compliant" not in vals


# --- build_table_from_assets -------------------------------------------------

def test_table_from_assets_list_columns_and_rows():
    rows = [
        {"hostname": ["h1"], "category": "server",      "owner": "Alice"},
        {"hostname": ["h2"], "category": "workstation", "owner": "Bob"},
    ]
    out = build_table_from_assets(rows, total=2, source_tool="list_assets")
    assert out["type"] == "table"
    assert out["meta"]["source_tool"] == "list_assets"
    assert out["meta"]["layout"] == "list"
    assert out["meta"]["total"] == 2
    assert out["meta"]["truncated"] is False
    cols = [c["key"] for c in out["columns"]]
    assert "hostname" in cols and "category" in cols
    assert len(out["series"]) == 2


def test_table_from_assets_empty_returns_none():
    assert build_table_from_assets(
        [], total=0, source_tool="list_assets",
    ) is None


def test_table_from_assets_caps_at_max_rows():
    rows = [{"hostname": [f"h{i}"], "category": "server"}
            for i in range(DEFAULT_MAX_TABLE_ROWS + 1)]
    out = build_table_from_assets(
        rows, total=DEFAULT_MAX_TABLE_ROWS + 1, source_tool="list_assets"
    )
    assert len(out["series"]) == DEFAULT_MAX_TABLE_ROWS
    assert out["meta"]["truncated"] is True
    assert out["meta"]["total"] == DEFAULT_MAX_TABLE_ROWS + 1


def test_table_from_assets_search_source_tool():
    rows = [{"hostname": ["h1"], "category": "server"}]
    out = build_table_from_assets(rows, total=1, source_tool="search_assets")
    assert out["meta"]["source_tool"] == "search_assets"
    assert out["meta"]["truncated"] is False


def test_table_from_assets_serializes_datetime_values_to_isoformat():
    """The chat repository persists artifacts via json.dumps, so
    datetime / date values from repo rows must be coerced to strings
    before they land in series."""
    import datetime as _dt
    import json as _json
    rows = [{
        "hostname": ["h1"], "category": "server",
        "last_seen": _dt.datetime(2026, 4, 25, 12, 30, 45),
    }]
    out = build_table_from_assets(rows, total=1, source_tool="list_assets")
    # Round-trip through json.dumps to confirm there's nothing
    # un-serializable left in the artifact.
    _json.dumps(out)
    assert out["series"][0]["last_seen"] == "2026-04-25T12:30:45"


def test_table_from_assets_is_column_driven_with_catalog_labels():
    """The table renders the SELECTED columns — including ext:/tag: fields —
    each with its CATALOG display label, never the opaque encoded key. This is
    the headline 'column-pick' fix: the table is no longer hardcoded to 8 static
    columns, so any field a user/the LLM selects actually shows up."""
    from unittest.mock import MagicMock
    from src.mcp.tools._query import resolve_list_columns

    # Resolver returns the catalog display name for the non-static fields
    # (static labels resolve with no resolver call).
    def _resolve(fid):
        rf = MagicMock()
        if getattr(fid, "attribute", None) == "cpu":
            rf.display_name = "Beszel - cpu"
        elif getattr(fid, "name", None) == "Critical":
            rf.display_name = "Tag: Critical"
        else:                                       # pragma: no cover - guard
            rf.display_name = "?"
        return rf

    resolver = MagicMock()
    resolver.resolve.side_effect = _resolve

    columns = resolve_list_columns(
        ["static:Hostname", "ext:Beszel:cpu", "tag:Critical"], resolver,
    )
    rows = [{
        "hostname": ["h1"],
        "ext:Beszel:cpu": "42%",
        "tag:Critical": True,
    }]
    out = build_table_from_assets(
        rows, total=1, source_tool="list_assets", columns=columns,
    )

    keys = [c["key"] for c in out["columns"]]
    labels = [c["label"] for c in out["columns"]]
    # Columns follow the selection order; row-dict keys line up (static is
    # snake-projected, ext/tag keep their raw encoded key).
    assert keys == ["hostname", "ext:Beszel:cpu", "tag:Critical"]
    assert labels == ["Hostname", "Beszel - cpu", "Tag: Critical"]
    # The opaque encoded key is NEVER surfaced as a header label.
    assert "ext:Beszel:cpu" not in labels
    # The selected ext/tag values render in the row cells.
    assert out["series"][0]["ext:Beszel:cpu"] == "42%"
    assert out["series"][0]["tag:Critical"] is True


# --- drilldown descriptor (plan §3.6) ----------------------------------------

def test_build_drilldown_shape_and_optional_col_value():
    from src.api.blueprints.chat.artifacts import build_drilldown

    one_d = build_drilldown([{"field": "Category", "op": "eq", "value": "Server"}])
    assert one_d == {
        "filters": [{"field": "Category", "op": "eq", "value": "Server"}],
    }
    # col_value is only present for a 2-D cell.
    assert "col_value" not in one_d

    two_d = build_drilldown(
        [{"field": "Category", "op": "eq", "value": "Server"}],
        col_value="Ubuntu",
    )
    assert two_d["col_value"] == "Ubuntu"


def test_drilldown_for_aggregate_cell_one_and_two_dimensional():
    from src.api.blueprints.chat.artifacts import drilldown_for_aggregate_cell

    one_d = drilldown_for_aggregate_cell("Category", "Server")
    assert one_d == {
        "filters": [{"field": "Category", "op": "eq", "value": "Server"}],
    }

    two_d = drilldown_for_aggregate_cell(
        "Category", "Server", col_field="OS Normalized", col_value="Ubuntu",
    )
    assert two_d["filters"] == [
        {"field": "Category", "op": "eq", "value": "Server"},
        {"field": "OS Normalized", "op": "eq", "value": "Ubuntu"},
    ]
    assert two_d["col_value"] == "Ubuntu"


def test_drilldown_filters_are_re_runnable_through_the_query_grammar():
    """A drilldown is only useful if 'show me those N' re-runs deterministically:
    its filters MUST resolve through the same path query_assets uses. Proven by
    round-tripping them through _normalize_filters (no raise, correct field_ids)."""
    from src.api.blueprints.chat.artifacts import drilldown_for_aggregate_cell
    from src.mcp.tools._query import _normalize_filters

    dd = drilldown_for_aggregate_cell(
        "Category", "Server", col_field="OS Normalized", col_value="Ubuntu",
    )
    normalized = _normalize_filters(dd["filters"])
    assert normalized == [
        {"field_id": {"kind": "static", "name": "Category"},
         "op": "eq", "value": "Server"},
        {"field_id": {"kind": "static", "name": "OS Normalized"},
         "op": "eq", "value": "Ubuntu"},
    ]


def test_build_drilldown_deep_copies_mutable_value_lists():
    """The descriptor is persisted and re-run as-is, so a later mutation of a
    source filter's value list (the any/none/all/between ops take one) must NOT
    leak into it — a shallow copy would share the list and corrupt the cell's
    re-run filter."""
    from src.api.blueprints.chat.artifacts import build_drilldown

    src_vals = ["a", "b"]
    dd = build_drilldown([{"field": "os", "op": "any", "value": src_vals}])
    src_vals.append("c")
    assert dd["filters"][0]["value"] == ["a", "b"]


def test_jsonify_coerces_decimal_to_float_number():
    """NUMERIC columns (e.g. confidence) arrive as Decimal, which json.dumps
    can't serialize. _jsonify must coerce to a float NUMBER (not the str()
    fallback) so the artifact cell stays numeric for the frontend."""
    import json
    from decimal import Decimal
    from src.api.blueprints.chat.artifacts import _jsonify

    out = _jsonify(Decimal("0.85"))
    assert isinstance(out, float) and out == 0.85
    json.dumps(out)   # must be serializable


def test_table_from_asset_serializes_datetime_values_to_isoformat():
    import datetime as _dt
    import json as _json
    asset = {
        "hostname": ["h1"], "category": "server",
        "last_seen": _dt.datetime(2026, 4, 25, 12, 30, 45),
    }
    out = build_table_from_asset(asset, asset_id="abc")
    _json.dumps(out)
    last_seen_row = next(r for r in out["series"] if r["field"] == "Last Seen")
    assert last_seen_row["value"] == "2026-04-25T12:30:45"


# --- validate_envelope per-type tests ---

def test_validate_envelope_accepts_donut():
    payload = {"type": "donut", "title": "x", "series": []}
    assert validate_envelope(payload, tool_name="t") is payload


def test_validate_envelope_accepts_stacked_bar():
    payload = {"type": "stacked_bar", "title": "x", "series": []}
    assert validate_envelope(payload, tool_name="t") is payload


def test_validate_envelope_accepts_table_with_columns():
    payload = {
        "type": "table", "title": "x",
        "columns": [{"key": "a", "label": "A"}], "series": [],
    }
    assert validate_envelope(payload, tool_name="t") is payload


def test_validate_envelope_drops_table_missing_columns():
    payload = {"type": "table", "title": "x", "series": []}
    assert validate_envelope(payload, tool_name="t") is None


def test_validate_envelope_drops_table_with_non_list_columns():
    payload = {"type": "table", "title": "x",
               "columns": "nope", "series": []}
    assert validate_envelope(payload, tool_name="t") is None


def test_validate_envelope_drops_unknown_type():
    payload = {"type": "scatter", "title": "x", "series": []}
    assert validate_envelope(payload, tool_name="t") is None


# --- build_table_from_aggregate ----------------------------------------------

def test_table_from_aggregate_1d_flat_count_table():
    r = _result(["server", "workstation"], {"server": 5, "workstation": 3}, 8)
    out = build_table_from_aggregate(r, "category", None)
    assert out["type"] == "table"
    assert out["meta"]["source_tool"] == "aggregate_assets"
    assert out["meta"]["layout"] == "list"
    cols = [c["key"] for c in out["columns"]]
    # Column keys: row_field name + "count"
    assert cols == ["category", "count"]
    labels = [row["category"] for row in out["series"]]
    assert "server" in labels and "workstation" in labels
    # Counts present
    server_row = next(row for row in out["series"]
                      if row["category"] == "server")
    assert server_row["count"] == 5


def test_table_from_aggregate_2d_pivot_table():
    r = _pivot(["server"], ["win", "linux"],
               {"server": {"win": 3, "linux": 2}}, 5)
    out = build_table_from_aggregate(r, "category", "os")
    assert out["type"] == "table"
    assert out["meta"]["layout"] == "list"
    cols = [c["key"] for c in out["columns"]]
    # Column keys: row_field, then one column per col_field value, then _total
    assert cols[0] == "category"
    assert "win" in cols and "linux" in cols
    assert cols[-1] == "_total"
    server_row = out["series"][0]
    assert server_row["category"] == "server"
    assert server_row["win"] == 3
    assert server_row["linux"] == 2
    assert server_row["_total"] == 5


def test_table_from_aggregate_empty_returns_none():
    assert build_table_from_aggregate({}, "category", None) is None
    # also: empty rows list
    assert build_table_from_aggregate(
        _result([], {}, 0), "category", None,
    ) is None


def test_table_from_aggregate_renders_zero_total_when_rows_present():
    # A zero / sub-1 / negative grand total is VALID for SUM/AVG/MIN/MAX (an
    # average that rounds below 1, a metric summing to 0, a MIN of negatives) —
    # presence of rows, not a positive grand total, is the "has data" signal.
    # The old grand_total<=0 guard wrongly dropped those; only a genuinely
    # empty result (test_empty_rows_returns_none) returns None.
    r = _result(["server"], {"server": 0}, 0)
    out = build_table_from_aggregate(r, "category", None)
    assert out is not None
    assert out["series"][0]["category"] == "server"


def test_table_from_aggregate_truncates_to_max_rows():
    # Use DEFAULT_MAX_TABLE_ROWS + 1 rows to ensure truncation fires.
    n = DEFAULT_MAX_TABLE_ROWS + 1
    rows_in = [f"r{i}" for i in range(n)]
    data = {r: 1 for r in rows_in}
    result = _result(rows_in, data, n)
    out = build_table_from_aggregate(result, "category", None)
    assert len(out["series"]) == DEFAULT_MAX_TABLE_ROWS
    assert out["meta"]["truncated"] is True
    assert out["meta"]["total"] == n


def test_table_from_aggregate_meta_carries_row_and_col_field():
    r = _result(["server"], {"server": 5}, 5)
    out = build_table_from_aggregate(r, "category", None)
    assert out["meta"]["row_field"] == "category"
    assert out["meta"]["col_field"] is None
    # 2-D
    p = _pivot(["server"], ["win"], {"server": {"win": 5}}, 5)
    out = build_table_from_aggregate(p, "category", "os")
    assert out["meta"]["row_field"] == "category"
    assert out["meta"]["col_field"] == "os"
