# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the list_alert_rules MCP tool."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp.tools.alert_rules import list_alert_rules_impl


def _rule(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "Rule A",
        "slug": "rule-a",
        "type": "security",
        "severity": "high",
        "description": "A security rule",
        "enabled": True,
        "source": "builtin",
    }
    base.update(overrides)
    return base


def test_empty_rules():
    svc = MagicMock(get_all_rules=MagicMock(return_value={"rules": []}))
    out = list_alert_rules_impl(svc)
    assert out == "No alert rules defined."


def test_two_types_rendered():
    rules = [
        _rule(
            id="00000000-0000-0000-0000-000000000001",
            name="Stale Assets",
            type="security",
            severity="high",
            enabled=True,
            description="Detects stale assets",
        ),
        _rule(
            id="00000000-0000-0000-0000-000000000002",
            name="Unknown MAC",
            type="security",
            severity="medium",
            enabled=False,
            description="Unknown MAC vendor",
        ),
        _rule(
            id="00000000-0000-0000-0000-000000000003",
            name="Missing Owner",
            type="compliance",
            severity="low",
            enabled=True,
            description="Asset has no owner",
        ),
        _rule(
            id="00000000-0000-0000-0000-000000000004",
            name="Expired OS",
            type="compliance",
            severity="high",
            enabled=False,
            description="Operating system past EOL",
        ),
    ]
    svc = MagicMock(get_all_rules=MagicMock(return_value={"rules": rules}))

    out = list_alert_rules_impl(svc)

    assert "# Alert rules" in out
    assert "## security (2)" in out
    assert "## compliance (2)" in out
    assert "Stale Assets" in out
    assert "Unknown MAC" in out
    assert "Missing Owner" in out
    assert "Expired OS" in out
    # enabled vs disabled markers
    assert "✓" in out  # ✓
    assert "✗" in out  # ✗


def test_service_exception():
    svc = MagicMock()
    svc.get_all_rules.side_effect = RuntimeError("boom")
    out = list_alert_rules_impl(svc)
    assert "Alert rules unavailable:" in out
    assert "boom" in out


def test_long_description_truncated():
    long_desc = "x" * 200
    rules = [_rule(description=long_desc)]
    svc = MagicMock(get_all_rules=MagicMock(return_value={"rules": rules}))

    out = list_alert_rules_impl(svc)

    # Assert the property (full value missing, truncation applied) rather than
    # the exact 77-char count — tweaking _DESC_TRUNC_CHARS shouldn't break this.
    assert long_desc not in out
    assert "..." in out


def test_none_service_returns_unavailable():
    out = list_alert_rules_impl(None)
    assert out == "Alerts service not available."


def test_missing_type_grouped_as_other():
    rules = [_rule(type=None, name="Orphan Rule")]
    svc = MagicMock(get_all_rules=MagicMock(return_value={"rules": rules}))

    out = list_alert_rules_impl(svc)

    assert "## other (1)" in out
    assert "Orphan Rule" in out


# --- format_alert_rule_lines (full def + id; used by list_rules) ---

def test_format_alert_rule_lines_surfaces_id_query_and_filters():
    """The unified list surfaces the id + reusable filters/query/threshold the
    granular table drops."""
    from src.mcp.tools.alert_rules import format_alert_rule_lines
    out = "\n".join(format_alert_rule_lines({
        "id": "a1", "name": "Stale", "slug": "stale", "type": "security",
        "severity": "high", "source": "system", "enabled": True,
        "evaluation_type": "filter",
        "target_filters": [{"field_id": {"kind": "static", "name": "Category"},
                            "operator": "eq", "value": "server"}],
        "threshold": {"condition": "exceeds", "value": 5, "unit": "number"},
        "silence_duration": 60, "created_by": "u1", "created_at": "2026-01-01",
    }))
    assert "id=a1" in out
    assert "target_filters" in out and "Category" in out
    assert "threshold" in out and "silence_duration" in out
    assert "created_by" in out
