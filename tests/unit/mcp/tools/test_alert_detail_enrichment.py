# SPDX-License-Identifier: AGPL-3.0-or-later
"""get_alert_detail + list_alerts_for_asset enrichment (chat parity 2a §2.D).

get_alert_detail now surfaces the firing reason, source (system/custom/
prometheus), threshold + current value, assets-by-category, contributing data
sources, and the affected assets (ip/model + id) PAGED with a true total.
list_alerts_for_asset enriches its rows with ip/model + created/resolved.
"""
from unittest.mock import MagicMock
from uuid import uuid4

from src.mcp.tools.alerts import (
    get_alert_detail_impl,
    list_alerts_for_asset_impl,
)


def _alert(**ov):
    base = {
        "id": str(uuid4()), "title": "EDR Coverage Drop", "severity": "high",
        "type": "security", "status": "active",
        "description": "30 workstations missing EDR",
        "source": "user",  # DB 'user' must surface as 'custom'
        "threshold": {"condition": "falls_below", "value": 85,
                      "unit": "percent", "current_value": 76.0},
        "assets_by_category": {"workstation": 2, "server": 1},
        "data_sources": ["CrowdStrike", "Intune"],
        "created_at": "2026-04-20T10:00:00Z",
        "updated_at": "2026-04-21T10:00:00Z",
        "resolved_at": None,
        "asset_ids": [str(uuid4()) for _ in range(3)],
        "asset_count": 3,
    }
    base.update(ov)
    return base


def test_detail_surfaces_reason_source_threshold_and_breakdowns():
    db = MagicMock()
    db.execute_query.side_effect = [
        [_alert()],
        [{"id": "a1", "hostname": "wks-1", "category": "workstation",
          "ip": "10.0.0.1", "model": "Latitude"}],
    ]
    out = get_alert_detail_impl(db, alert_id=str(uuid4()))
    assert "source: custom" in out           # 'user' → 'custom'
    assert "reason:" in out                    # computed (no DB column)
    assert "current 76.0%" in out              # current_value from threshold
    assert "threshold 85%" in out
    assert "CrowdStrike" in out                # data_sources
    assert "workstation: 2" in out             # assets_by_category
    assert "updated:" in out
    # affected asset renders ip + model + its id (drill target)
    assert "wks-1" in out and "10.0.0.1" in out and "Latitude" in out


def test_detail_pages_affected_assets_with_real_total():
    db = MagicMock()
    long = [{"id": f"a{i}", "hostname": f"h-{i}", "category": "server",
             "ip": f"10.0.0.{i}", "model": "m"} for i in range(60)]
    db.execute_query.side_effect = [
        [_alert(asset_ids=[str(uuid4()) for _ in range(60)], asset_count=60)],
        long,
    ]
    out = get_alert_detail_impl(db, alert_id=str(uuid4()), offset=0, limit=50)
    assert "60 total" in out
    assert "h-0" in out and "h-49" in out and "h-59" not in out
    # The page cap is now a real param + a true-total overflow signal.
    assert "and 10 more" in out


def test_detail_passes_offset_and_limit_to_the_assets_query():
    db = MagicMock()
    db.execute_query.side_effect = [
        [_alert()],
        [{"id": "a1", "hostname": "wks-1", "category": "workstation"}],
    ]
    get_alert_detail_impl(db, alert_id=str(uuid4()), offset=10, limit=25)
    # 2nd call is the affected-assets query.
    asset_call = db.execute_query.call_args_list[1]
    assert asset_call.args[1]["offset"] == 10
    assert asset_call.args[1]["limit"] == 25


def test_detail_return_data_builds_a_valid_assets_artifact():
    from src.api.blueprints.chat.artifacts import (
        build_table_from_alert_assets, validate_envelope,
    )
    db = MagicMock()
    db.execute_query.side_effect = [
        [_alert()],
        [{"id": "a1", "hostname": "wks-1", "category": "workstation",
          "ip": "10.0.0.1", "model": "Latitude"}],
    ]
    text, data = get_alert_detail_impl(db, alert_id=str(uuid4()),
                                       return_data=True)
    assert data["asset_count"] == 3
    assert data["assets"][0]["id"] == "a1"
    art = build_table_from_alert_assets(
        data["assets"], total=data["asset_count"], title=data["title"])
    assert validate_envelope(art, tool_name="get_alert_detail") is not None
    assert art["series"][0]["asset_id"] == "a1"


def test_detail_back_compat_minimal_row_still_renders():
    """An alert row without the new columns (None reason/threshold/source) must
    still render — the enrichment is additive, never a hard dependency."""
    db = MagicMock()
    db.execute_query.side_effect = [
        [{"id": str(uuid4()), "title": "X", "severity": "low", "type": "compliance",
          "status": "active", "description": "", "created_at": "2026-01-01",
          "asset_ids": [], "asset_count": 0}],
    ]
    out = get_alert_detail_impl(db, alert_id=str(uuid4()))
    assert "source: system" in out  # missing source defaults to 'system'
    assert "No affected assets recorded" in out


def test_list_alerts_for_asset_rollup_includes_ip_and_model():
    db = MagicMock()
    db.execute_query.side_effect = [
        [{"id": "a1", "hostname": ["srv-1"], "category": "server",
          "ip": "10.0.0.5", "model": "PowerEdge", "n": 4}],   # roll-up
        [{"n": 4}],                                            # total links
    ]
    out = list_alerts_for_asset_impl(db, status="active")
    assert "srv-1" in out
    assert "10.0.0.5" in out and "PowerEdge" in out


def test_list_alerts_for_one_asset_shows_meta_and_row_dates():
    db = MagicMock()
    aid = str(uuid4())
    db.execute_query.side_effect = [
        [{"id": aid, "hostname": ["srv-1"], "category": "server",
          "ip": "10.0.0.5", "model": "PowerEdge"}],            # asset lookup
        [{"n": 1}],                                            # real total
        [{"id": "al1", "title": "Stale", "severity": "high",
          "type": "operational", "status": "active", "asset_count": 1,
          "created_at": "2026-06-01T00:00:00", "resolved_at": None}],  # alerts
    ]
    out = list_alerts_for_asset_impl(db, asset_id=aid, status="active")
    assert "10.0.0.5" in out and "PowerEdge" in out  # asset meta in header
    assert "created=2026-06-01" in out               # created in the alert row
    assert "not shown" not in out                    # nothing hidden at total=1


def test_list_alerts_for_one_asset_reports_real_total_and_overflow():
    # The COUNT(*) reported must be the asset's TRUE alert count, not the number
    # of rows we render. A capped page that mis-reports len(rows) as the total
    # lets the model treat the page as the whole truth — the §4/§8-#2 hazard.
    db = MagicMock()
    aid = str(uuid4())
    page = [
        {"id": f"al{i}", "title": f"Alert {i}", "severity": "high",
         "type": "operational", "status": "active", "asset_count": 1,
         "created_at": "2026-06-01T00:00:00", "resolved_at": None}
        for i in range(2)
    ]
    db.execute_query.side_effect = [
        [{"id": aid, "hostname": ["srv-1"], "category": "server",
          "ip": "10.0.0.5", "model": "PowerEdge"}],            # asset lookup
        [{"n": 7}],                                            # real total = 7
        page,                                                  # capped to 2 rows
    ]
    out = list_alerts_for_asset_impl(db, asset_id=aid, status="active", limit=2)
    assert "COUNT: 7 alerts" in out          # true total, not len(page)=2
    assert "5 more alerts not shown" in out  # 7 - 2 rendered
    # And the LIMIT was actually applied to the alerts query (not ignored).
    alerts_call = db.execute_query.call_args_list[-1]
    assert "LIMIT %(limit)s" in alerts_call.args[0]
    assert alerts_call.args[1]["limit"] == 2
