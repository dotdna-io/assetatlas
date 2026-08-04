# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the AA artifact → Adaptive Card transformer.

Envelope shapes match what `src/api/blueprints/chat/artifacts.py` emits:
  donut       — series: [{label, value}]
  stacked_bar — series: [{x_label, stacks: [{label, value}]}]
  table       — columns: [{key, label}], series: [{key: val}]
"""
from __future__ import annotations

from src.teams_bridge.artifacts import build_artifact_card


def test_donut_maps_to_chart_donut():
    artifact = {
        "type": "donut",
        "title": "OS distribution",
        "series": [
            {"label": "Windows", "value": 12},
            {"label": "macOS", "value": 4},
        ],
    }
    card = build_artifact_card(artifact)
    chart = next(b for b in card["body"] if b.get("type") == "Chart.Donut")
    assert chart["data"][0]["legend"] == "Windows"
    assert chart["data"][0]["value"] == 12
    assert any(b.get("type") == "TextBlock"
               and b.get("text") == "OS distribution"
               for b in card["body"])


def test_stacked_bar_uses_x_label_from_chat_envelope():
    """Chat emits series rows keyed on `x_label`. The bridge must read
    that key so x-axis labels actually populate."""
    artifact = {
        "type": "stacked_bar",
        "title": "Monthly",
        "series": [
            {"x_label": "Jan",
             "stacks": [{"label": "Win", "value": 1},
                        {"label": "Mac", "value": 2}]},
            {"x_label": "Feb",
             "stacks": [{"label": "Win", "value": 3},
                        {"label": "Mac", "value": 4}]},
        ],
    }
    card = build_artifact_card(artifact)
    chart = next(
        b for b in card["body"]
        if b.get("type") == "Chart.VerticalBar.Grouped"
    )
    assert chart.get("stacked") is True
    legends = {d["legend"] for d in chart["data"]}
    assert legends == {"Win", "Mac"}
    # x labels must come through, not be blank
    xs = {d["x"] for d in chart["data"]}
    assert xs == {"Jan", "Feb"}


def test_stacked_bar_falls_back_to_x_for_legacy_callers():
    """Defensive: if a future caller emits `x` instead of `x_label`,
    don't drop the labels."""
    artifact = {
        "type": "stacked_bar",
        "title": "M",
        "series": [
            {"x": "Jan",
             "stacks": [{"label": "Win", "value": 1}]},
        ],
    }
    card = build_artifact_card(artifact)
    chart = next(
        b for b in card["body"]
        if b.get("type") == "Chart.VerticalBar.Grouped"
    )
    assert chart["data"][0]["x"] == "Jan"


def test_table_uses_key_label_columns_from_chat_envelope():
    """Chat emits columns as [{"key", "label"}]. The bridge must show
    `label` in the header and use `key` to look up cell values."""
    artifact = {
        "type": "table",
        "title": "Hosts",
        "columns": [
            {"key": "host", "label": "Hostname"},
            {"key": "os_normalized", "label": "Operating system"},
        ],
        "series": [
            {"host": "h1", "os_normalized": "Windows 11"},
            {"host": "h2", "os_normalized": "macOS 14"},
        ],
    }
    card = build_artifact_card(artifact, max_rows=25)
    table = next(b for b in card["body"] if b.get("type") == "Table")
    header_texts = [
        cell["items"][0]["text"]
        for cell in table["rows"][0]["cells"]
    ]
    assert header_texts == ["Hostname", "Operating system"]
    first_data_row = [
        cell["items"][0]["text"]
        for cell in table["rows"][1]["cells"]
    ]
    assert first_data_row == ["h1", "Windows 11"]


def test_table_truncates_to_max_rows_with_overflow_footer():
    artifact = {
        "type": "table",
        "title": "Hosts",
        "columns": [
            {"key": "host", "label": "Hostname"},
            {"key": "os", "label": "OS"},
        ],
        "series": [{"host": f"h{i}", "os": "win"} for i in range(40)],
    }
    card = build_artifact_card(artifact, max_rows=25)
    table = next(b for b in card["body"] if b.get("type") == "Table")
    # 1 header row + 25 data rows = 26 total
    assert len(table["rows"]) == 26
    footer = next(
        b for b in card["body"]
        if b.get("type") == "TextBlock"
           and "more rows" in b.get("text", "")
    )
    assert "15 more rows" in footer["text"]


def test_table_accepts_plain_string_columns_for_back_compat():
    """If a caller ever sends columns as plain strings, the same
    string acts as both key and header label."""
    artifact = {
        "type": "table",
        "title": "Hosts",
        "columns": ["hostname"],
        "series": [{"hostname": "h1"}, {"hostname": "h2"}],
    }
    card = build_artifact_card(artifact, max_rows=25)
    table = next(b for b in card["body"] if b.get("type") == "Table")
    assert (table["rows"][0]["cells"][0]
            ["items"][0]["text"]) == "hostname"
    assert (table["rows"][1]["cells"][0]
            ["items"][0]["text"]) == "h1"


def test_table_no_footer_when_under_limit():
    artifact = {
        "type": "table",
        "title": "Hosts",
        "columns": [{"key": "host", "label": "Hostname"}],
        "series": [{"host": "h1"}, {"host": "h2"}],
    }
    card = build_artifact_card(artifact, max_rows=25)
    assert not any(
        "more rows" in (b.get("text") or "")
        for b in card["body"]
    )


def test_unknown_type_returns_text_fallback():
    card = build_artifact_card({"type": "alien", "title": "x"})
    assert card["type"] == "AdaptiveCard"
    text_blocks = [b for b in card["body"] if b.get("type") == "TextBlock"]
    fallback = " ".join(b.get("text", "") for b in text_blocks)
    assert "alien" in fallback or "Unsupported" in fallback


def test_donut_includes_fallback_textblock():
    artifact = {
        "type": "donut",
        "title": "OS",
        "series": [{"label": "Windows", "value": 12}],
    }
    card = build_artifact_card(artifact)
    chart = next(b for b in card["body"] if b.get("type") == "Chart.Donut")
    assert chart.get("fallback")
