# SPDX-License-Identifier: AGPL-3.0-or-later
"""Alert artifact builders (chat parity 2a).

The chat artifact type ceiling is donut/stacked_bar/table — validate_envelope
silently DROPS anything else (see reference-chat-artifact-type-ceiling). These
assert the alert builders emit a rendered shape (so a timeline can't be a
dropped 'line') and carry the drill ids the model needs.
"""
from src.api.blueprints.chat.artifacts import (
    build_alerts_timeline_chart,
    build_table_from_alert_assets,
    build_table_from_alerts,
    validate_envelope,
)


_ALERTS = [
    {"id": "11111111-1111-1111-1111-111111111111", "severity": "critical",
     "type": "security", "status": "active", "title": "EDR gap",
     "asset_count": 9, "source": "system", "created_at": "2026-06-09"},
    {"id": "22222222-2222-2222-2222-222222222222", "severity": "low",
     "type": "operational", "status": "resolved", "title": "Stale",
     "asset_count": 1, "source": "custom", "created_at": "2026-06-08"},
]


def test_alert_table_validates_and_carries_alert_ids():
    art = build_table_from_alerts(_ALERTS, total=2)
    assert validate_envelope(art, tool_name="get_alerts") is not None
    assert art["type"] == "table"
    assert {c["key"] for c in art["columns"]} >= {"severity", "title", "status"}
    # alert_id is the drill key (alerts link assets by id array, not a field).
    assert [r["alert_id"] for r in art["series"]] == [a["id"] for a in _ALERTS]


def test_alert_table_total_drives_title_overflow_signal():
    art = build_table_from_alerts(_ALERTS, total=57)
    assert "57 total" in art["title"]  # true total, not the page length


def test_alert_table_empty_is_none():
    assert build_table_from_alerts([], total=0) is None


def test_timeline_chart_is_a_severity_stacked_bar():
    timeline = [
        {"date": "2026-06-08", "total": 3,
         "by_severity": {"critical": 1, "high": 2, "medium": 0, "low": 0}},
        {"date": "2026-06-09", "total": 1,
         "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 1}},
    ]
    art = build_alerts_timeline_chart(timeline, bucket="day")
    # A 'line' type would be dropped; a stacked_bar renders.
    assert validate_envelope(art, tool_name="get_alerts") is not None
    assert art["type"] == "stacked_bar"
    assert [s["x_label"] for s in art["series"]] == ["2026-06-08", "2026-06-09"]
    first = {st["label"]: st["value"] for st in art["series"][0]["stacks"]}
    assert first == {"critical": 1, "high": 2}  # zero stacks dropped


def test_timeline_chart_empty_is_none():
    assert build_alerts_timeline_chart([], bucket="day") is None
    # All-zero buckets produce no chartable stacks.
    assert build_alerts_timeline_chart(
        [{"date": "x", "total": 0,
          "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0}}],
        bucket="day") is None


def test_alert_assets_table_validates_and_maps_asset_id():
    assets = [{"id": "a1", "hostname": "wks-1", "category": "workstation",
               "ip": "10.0.0.1", "model": "Latitude"}]
    art = build_table_from_alert_assets(assets, total=3, title="EDR gap")
    assert validate_envelope(art, tool_name="get_alert_detail") is not None
    assert "3 total" in art["title"]  # page < total → overflow in title
    assert art["series"][0]["asset_id"] == "a1"
    assert art["series"][0]["hostname"] == "wks-1"
