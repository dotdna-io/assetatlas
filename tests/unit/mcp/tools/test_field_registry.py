# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the field_registry MCP tool."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp.tools.field_registry import get_field_registry_impl


def _field_row(**overrides):
    base = {
        "field_name": "hostname",
        "display_name": "Hostname",
        "description": "Primary hostname of the asset",
        "field_type": "static",
        "data_source": "jamf",
        "searchable": True,
    }
    base.update(overrides)
    return base


def test_list_form_renders_sections_and_rows():
    db = MagicMock()
    db.execute_query.return_value = [
        _field_row(field_name="hostname", field_type="static", searchable=True),
        _field_row(field_name="mac_address", field_type="static", searchable=False),
        _field_row(field_name="custom_tag", field_type="extended", searchable=True),
    ]

    out = get_field_registry_impl(db)

    assert "## static (2)" in out
    assert "## extended (1)" in out
    assert "hostname" in out
    assert "mac_address" in out
    assert "custom_tag" in out
    assert "✓" in out  # ✓
    assert "✗" in out  # ✗


def test_list_form_empty():
    db = MagicMock()
    db.execute_query.return_value = []
    out = get_field_registry_impl(db)
    assert out == "Field registry is empty."


def test_detail_form_found_renders_db_path_and_data_source():
    db = MagicMock()
    db.execute_query.return_value = [{
        "field_name": "hostname",
        "display_name": "Hostname",
        "description": "Primary hostname of the asset",
        "field_type": "static",
        "db_path": "assets.hostname",
        "data_source": "jamf",
        "description_source": "hardcoded",
        "searchable": True,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-02-01T00:00:00Z",
    }]

    out = get_field_registry_impl(db, field_name="hostname")

    assert "hostname" in out
    assert "assets.hostname" in out
    assert "jamf" in out
    assert "Primary hostname of the asset" in out


def test_detail_form_not_found():
    db = MagicMock()
    db.execute_query.return_value = []
    out = get_field_registry_impl(db, field_name="missing_field")
    assert "not found" in out.lower()
    assert "missing_field" in out


def test_list_form_truncates_long_description():
    db = MagicMock()
    long_desc = "x" * 200
    db.execute_query.return_value = [
        _field_row(field_name="hostname", description=long_desc),
    ]

    out = get_field_registry_impl(db)

    # Truncated to 60 chars — no full 200-x run in output.
    assert long_desc not in out
    assert "x" * 60 in out
