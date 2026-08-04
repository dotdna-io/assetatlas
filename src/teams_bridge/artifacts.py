# SPDX-License-Identifier: AGPL-3.0-or-later
"""AssetAtlas artifact envelope → Teams Adaptive Card.

Three artifact types: donut, stacked_bar, table. Anything else returns
a TextBlock fallback so unknown payloads degrade gracefully.

Each chart card includes a TextBlock fallback summary so older Teams
clients (or mobile fallbacks) render readable text instead of nothing.

Envelope shapes (must match src/api/blueprints/chat/artifacts.py):
  donut      — series: [{label, value}]
  stacked_bar — series: [{x_label, stacks: [{label, value}]}]
  table      — columns: [{key, label}], series: [{key: val}]
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.teams_bridge._cards import adaptive_card, text_block


def build_artifact_card(
    artifact: Dict[str, Any], *, max_rows: int = 25,
) -> Dict[str, Any]:
    kind = artifact.get("type")
    title = artifact.get("title", "Result")
    if kind == "donut":
        return _donut_card(title, artifact)
    if kind == "stacked_bar":
        return _stacked_bar_card(title, artifact)
    if kind == "table":
        return _table_card(title, artifact, max_rows=max_rows)
    return adaptive_card([
        text_block(title, weight="Bolder"),
        text_block(f"Unsupported artifact type: {kind}", isSubtle=True),
    ])


def _donut_card(title: str, a: Dict[str, Any]) -> Dict[str, Any]:
    data = [
        {"legend": s.get("label", ""),
         "value": float(s.get("value", 0))}
        for s in (a.get("series") or [])
    ]
    summary = ", ".join(
        f"{d['legend']}: {int(d['value'])}" for d in data
    )
    chart = {
        "type": "Chart.Donut",
        "fallback": text_block(
            f"Donut chart — {title}: {summary}",
        ),
        "colorSet": "categorical",
        "data": data,
    }
    return adaptive_card([
        text_block(title, weight="Bolder"),
        chart,
    ])


def _stacked_bar_card(title: str, a: Dict[str, Any]) -> Dict[str, Any]:
    data = []
    for row in (a.get("series") or []):
        # Chat emits "x_label"; tolerate "x" as a defensive fallback.
        x = row.get("x_label", row.get("x", ""))
        for s in (row.get("stacks") or []):
            data.append({
                "x": x,
                "y": float(s.get("value", 0)),
                "legend": s.get("label", ""),
            })
    chart = {
        "type": "Chart.VerticalBar.Grouped",
        "fallback": text_block(f"Stacked bar chart: {title}"),
        "stacked": True,
        "colorSet": "categorical",
        "data": data,
    }
    return adaptive_card([
        text_block(title, weight="Bolder"),
        chart,
    ])


def _table_card(
    title: str, a: Dict[str, Any], *, max_rows: int,
) -> Dict[str, Any]:
    col_keys, col_labels = _split_columns(a.get("columns") or [])
    rows: List[Dict[str, Any]] = list(a.get("series") or [])
    truncated = rows[:max_rows]
    overflow = max(0, len(rows) - max_rows)

    header_row = {
        "type": "TableRow",
        "cells": [
            {"type": "TableCell",
             "items": [{"type": "TextBlock", "wrap": True,
                        "weight": "Bolder", "text": label}]}
            for label in col_labels
        ],
    }
    data_rows = [
        {"type": "TableRow",
         "cells": [
             {"type": "TableCell",
              "items": [{"type": "TextBlock", "wrap": True,
                         "text": str(r.get(key, ""))}]}
             for key in col_keys
         ]}
        for r in truncated
    ]
    table = {
        "type": "Table",
        "columns": [{"width": 1} for _ in col_keys],
        "rows": [header_row] + data_rows,
    }
    body: List[Dict[str, Any]] = [
        text_block(title, weight="Bolder"),
        table,
    ]
    if overflow:
        body.append(text_block(
            f"+ {overflow} more rows — view full results in AssetAtlas",
            isSubtle=True,
        ))
    return adaptive_card(body)


def _split_columns(
    columns: List[Any],
) -> Tuple[List[str], List[str]]:
    """Accept both [{"key", "label"}] dicts and plain strings; treat
    strings as key=label."""
    keys: List[str] = []
    labels: List[str] = []
    for c in columns:
        if isinstance(c, dict):
            key = str(c.get("key", ""))
            keys.append(key)
            labels.append(str(c.get("label", key)))
        else:
            keys.append(str(c))
            labels.append(str(c))
    return keys, labels
