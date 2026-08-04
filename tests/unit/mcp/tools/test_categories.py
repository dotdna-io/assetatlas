# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the get_asset_categories MCP tool."""

import json

from src.mcp.tools.categories import get_asset_categories_impl


def _cats():
    return [
        {"name": "server", "subcategories": ["server", "hypervisor"]},
        {"name": "workstation", "subcategories": ["laptop", "workstation"]},
        {"name": "network", "subcategories": []},
    ]


def test_get_asset_categories_formats_output_with_subs():
    out = get_asset_categories_impl(categories=_cats())
    assert "# Asset categories" in out
    assert "**server**" in out
    assert "hypervisor" in out
    assert "**workstation**" in out
    assert "laptop" in out


def test_get_asset_categories_handles_category_without_subs():
    out = get_asset_categories_impl(categories=_cats())
    # "network" has no subcategories -> rendered as bare bullet
    assert "**network**" in out
    # Should NOT have a colon+spaces after network because subs is empty
    network_line = next(line for line in out.splitlines() if "**network**" in line)
    assert network_line.endswith("**network**")


def test_get_asset_categories_from_missing_file(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    out = get_asset_categories_impl(path=str(missing))
    assert "No asset categories defined" in out


def test_get_asset_categories_reads_from_path(tmp_path):
    p = tmp_path / "categories.json"
    p.write_text(json.dumps({"categories": _cats()}))
    out = get_asset_categories_impl(path=str(p))
    assert "# Asset categories" in out
    assert "**server**" in out


def test_get_asset_categories_default_path_reads_real_file():
    # Sanity: the default data/categories.json ships with the repo and should load.
    out = get_asset_categories_impl()
    assert "# Asset categories" in out
    assert "**server**" in out or "**workstation**" in out
