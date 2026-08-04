# SPDX-License-Identifier: AGPL-3.0-or-later
"""P5 (#735): list_fields lets the bot discover data-source-specific field
names (display name -> encoded key) so it can then aggregate by them."""
from unittest.mock import MagicMock

from src.mcp.tools.field_registry import list_catalog_fields_impl


def _entry(display, encoded, data_type="string"):
    e = MagicMock()
    e.display_name = display
    e.data_type.value = data_type
    return e


def _resolver(entries, encoded_by_display):
    resolver = MagicMock()
    resolver.catalog.return_value.entries.return_value = entries
    resolver.catalog.return_value.field_id_for_display_name.side_effect = (
        lambda n: encoded_by_display.get(n)
    )
    return resolver


def test_search_filters_and_lists_encoded_keys(monkeypatch):
    entries = [_entry("Beszel - cpu", "group:Beszel:cpu"),
               _entry("Category", "static:Category")]
    # encode() is applied to the FieldId; stub it to return the display->key map.
    monkeypatch.setattr("src.mcp.tools.field_registry.encode",
                        lambda fid: fid)
    monkeypatch.setattr("src.mcp.tools.field_registry._entry_field_id",
                        lambda e: {"Beszel - cpu": "group:Beszel:cpu",
                                   "Category": "static:Category"}[e.display_name])
    out = list_catalog_fields_impl(_resolver(entries, {}), search="beszel")
    assert "Beszel - cpu" in out
    assert "group:Beszel:cpu" in out
    assert "Category" not in out


def test_empty_match_is_explicit(monkeypatch):
    monkeypatch.setattr("src.mcp.tools.field_registry.encode", lambda fid: fid)
    monkeypatch.setattr("src.mcp.tools.field_registry._entry_field_id",
                        lambda e: "x")
    out = list_catalog_fields_impl(_resolver([], {}), search="zzz")
    assert "No" in out or "no matching" in out.lower()
