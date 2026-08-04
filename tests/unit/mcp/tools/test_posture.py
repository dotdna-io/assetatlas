# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the list_posture_rules MCP tool."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from src.mcp.tools.posture import list_posture_rules_impl


def _join_row(cat_id, cat_name, applicable, *, sr_id=None, sr_name=None,
              sr_description=None, sr_enabled=None):
    """Build one row of the category × sub_rule LEFT JOIN result."""
    return {
        "cat_id": cat_id,
        "cat_name": cat_name,
        "applicable_categories": applicable,
        "sr_id": sr_id,
        "sr_name": sr_name,
        "sr_description": sr_description,
        "sr_enabled": sr_enabled,
    }


def test_list_posture_rules_renders_categories_and_sub_rules():
    db = MagicMock()
    db.execute_query.return_value = [
        _join_row("endpoint_protection", "Endpoint Protection",
                  ["workstation", "server"],
                  sr_id=str(uuid4()), sr_name="EDR installed",
                  sr_description="Asset has an EDR agent", sr_enabled=True),
        _join_row("endpoint_protection", "Endpoint Protection",
                  ["workstation", "server"],
                  sr_id=str(uuid4()), sr_name="AV running",
                  sr_description=None, sr_enabled=False),
        _join_row("patching", "Patching", None,
                  sr_id=str(uuid4()), sr_name="OS up to date",
                  sr_description="OS patched within 30 days", sr_enabled=True),
    ]

    out = list_posture_rules_impl(db)

    assert "Endpoint Protection" in out
    assert "Patching" in out
    assert "EDR installed" in out
    assert "AV running" in out
    assert "OS up to date" in out
    assert "workstation, server" in out
    assert "all" in out  # patching has no applicable_categories
    assert "enabled" in out
    assert "disabled" in out
    assert "no description" in out
    # Single JOIN query — no N+1.
    assert db.execute_query.call_count == 1


def test_list_posture_rules_enabled_only_filters_sql():
    db = MagicMock()
    db.execute_query.return_value = [
        _join_row("endpoint_protection", "Endpoint Protection", [],
                  sr_id=str(uuid4()), sr_name="EDR installed",
                  sr_description="x", sr_enabled=True),
    ]

    list_posture_rules_impl(db, enabled_only=True)

    call = db.execute_query.call_args_list[0]
    sql = call.args[0] if call.args else call.kwargs["query"]
    assert "sr.enabled = TRUE" in sql


def test_list_posture_rules_skips_categories_with_no_sub_rules_when_enabled_only():
    db = MagicMock()
    # Patching has no sub-rule rows in the filtered JOIN (LEFT JOIN w/ enabled=TRUE).
    db.execute_query.return_value = [
        _join_row("endpoint_protection", "Endpoint Protection", ["workstation"],
                  sr_id=str(uuid4()), sr_name="EDR installed",
                  sr_description="x", sr_enabled=True),
        _join_row("patching", "Patching", ["server"]),
    ]

    out = list_posture_rules_impl(db, enabled_only=True)
    assert "Endpoint Protection" in out
    assert "Patching" not in out


def test_list_posture_rules_category_with_no_sub_rules_kept_when_not_filtering():
    db = MagicMock()
    db.execute_query.return_value = [
        _join_row("patching", "Patching", ["server"]),  # no sub-rules
    ]

    out = list_posture_rules_impl(db)
    assert "Patching" in out
    assert "(none)" in out


def test_list_posture_rules_no_categories():
    db = MagicMock()
    db.execute_query.return_value = []
    out = list_posture_rules_impl(db)
    assert "No posture categories defined." in out


# --- format_posture_subrule_lines (full def + id; used by list_rules) ---

def test_format_posture_subrule_lines_surfaces_id_filters_and_provenance():
    from src.mcp.tools.posture import format_posture_subrule_lines
    out = "\n".join(format_posture_subrule_lines({
        "id": "sr1", "name": "Has EDR", "enabled": True, "display_order": 1,
        "_category_name": "Secured", "_applicable": ["server", "workstation"],
        "target_filters": [{"field_id": {"kind": "static", "name": "Category"},
                            "operator": "eq", "value": "server"}],
        "compliance_criteria": {"logic": "all", "conditions": []},
        "output_mode": "field",
        "output_value": {"mode": "field",
                         "field_id": {"kind": "static", "name": "Owner"}},
        "last_edit": {"action": "update", "changed_by_username": "admin",
                      "at": "2026-01-02"},
    }))
    assert "id=sr1" in out
    assert "Secured" in out
    assert "target_filters" in out and "Category" in out
    assert "server, workstation" in out  # applicable categories
    assert "output_mode" in out
    assert "last edit" in out and "admin" in out
