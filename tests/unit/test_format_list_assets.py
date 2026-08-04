# SPDX-License-Identifier: AGPL-3.0-or-later
from src.mcp.tools.assets import format_list_assets

ROWS = [{"hostname": f"host{i}"} for i in range(5)]


def test_no_cap_renders_all_rows():
    text = format_list_assets(ROWS, total=5, page=1, limit=100)
    for i in range(5):
        assert f"host{i}" in text
    assert "more shown to the user" not in text


def test_cap_renders_subset_and_note():
    text = format_list_assets(ROWS, total=5, page=1, limit=100, max_rows=2)
    assert "host0" in text and "host1" in text
    assert "host4" not in text
    assert "host2" not in text and "host3" not in text
    assert "+3 more shown to the user" in text
    assert "download" in text.lower()


def test_cap_at_exact_length_no_note():
    text = format_list_assets(ROWS, total=5, page=1, limit=100, max_rows=5)
    for i in range(5):
        assert f"host{i}" in text
    assert "more shown to the user" not in text


def test_empty_unchanged():
    assert format_list_assets([], total=0, page=1, limit=100, max_rows=2) == \
        "No assets found matching the given criteria."
