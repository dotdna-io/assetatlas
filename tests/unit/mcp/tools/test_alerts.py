# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the get_alert_detail MCP tool."""

from unittest.mock import MagicMock
from uuid import uuid4

from src.mcp.tools.alerts import get_alert_detail_impl


def _alert_row(**overrides):
    base = {
        "id": str(uuid4()),
        "title": "EDR Coverage Drop",
        "severity": "high",
        "type": "security",
        "status": "active",
        "description": "30 workstations missing EDR",
        "created_at": "2026-04-20T10:00:00Z",
        "resolved_at": None,
        "asset_ids": [str(uuid4()) for _ in range(3)],
        "asset_count": 3,
    }
    base.update(overrides)
    return base


def test_get_alert_detail_formats_output():
    db = MagicMock()
    aid = str(uuid4())
    db.execute_query.side_effect = [
        [_alert_row(id=aid)],                                     # alert query -> list
        [{"id": "a1", "hostname": "srv-1", "category": "server",
          "ip": "10.0.0.1"},
         {"id": "a2", "hostname": "wks-2", "category": "workstation",
          "ip": "10.0.0.2"},
         {"id": "a3", "hostname": "wks-3", "category": "workstation",
          "ip": "10.0.0.3"}],                                    # assets query
    ]

    out = get_alert_detail_impl(db, alert_id=aid)
    assert "EDR Coverage Drop" in out
    assert "severity: high" in out
    assert "srv-1" in out
    assert "wks-2" in out
    assert "server" in out
    assert "workstation" in out


def test_get_alert_detail_not_found():
    db = MagicMock()
    db.execute_query.return_value = []
    out = get_alert_detail_impl(db, alert_id=str(uuid4()))
    assert "not found" in out.lower()


def test_get_alert_detail_with_no_affected_assets():
    db = MagicMock()
    db.execute_query.side_effect = [
        [_alert_row(asset_ids=[], asset_count=0)],
    ]
    out = get_alert_detail_impl(db, alert_id=str(uuid4()))
    assert "No affected assets recorded" in out


def test_get_alert_detail_truncates_long_asset_lists():
    db = MagicMock()
    long_list = [{"id": str(uuid4()), "hostname": f"h-{i}", "category": "server",
                  "ip": f"10.0.0.{i}"} for i in range(60)]
    db.execute_query.side_effect = [
        [_alert_row(asset_ids=[str(uuid4()) for _ in range(60)], asset_count=60)],
        long_list,
    ]
    out = get_alert_detail_impl(db, alert_id=str(uuid4()))
    assert "h-0" in out
    assert "h-49" in out
    assert "h-59" not in out
    assert "and 10 more" in out


def test_get_alert_detail_invalid_uuid_returns_helpful_message():
    db = MagicMock()
    out = get_alert_detail_impl(db, alert_id="not-a-uuid")
    assert "Invalid alert ID" in out
    assert "not-a-uuid" in out
    db.execute_query.assert_not_called()
