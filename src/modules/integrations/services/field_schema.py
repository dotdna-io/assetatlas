# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compute field schema from raw source data payloads."""

import re
from typing import Any, Dict, List

MAX_DEPTH = 6


def compute_field_schema(records: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Analyze raw records and return discovered field paths with metadata.

    Args:
        records: List of dicts, each with a 'data' key containing the raw payload.

    Returns:
        List of field descriptors sorted by coverage (descending), each with:
        - path: dot-notation path (e.g. "computer.general.name")
        - type: detected JSON type ("string", "number", "boolean", "array", "object")
        - sample: first non-null sample value (truncated to 100 chars)
        - coverage: percentage of records containing this field (0-100)
    """
    if not records:
        return []

    field_stats: Dict[str, Dict[str, Any]] = {}
    total = len(records)

    for record in records:
        payload = record.get("data") or {}
        _collect_paths(payload, "", field_stats, depth=0)

    result = []
    for path, stats in field_stats.items():
        if stats["count"] == 0:
            continue  # Skip fields that were only seen as null
        sample_val = stats.get("sample")
        if sample_val is not None:
            sample_str = str(sample_val)
            if len(sample_str) > 100:
                sample_str = sample_str[:100] + "..."
        else:
            sample_str = None

        result.append({
            "path": path,
            "type": stats["type"],
            "sample": sample_str,
            "coverage": round((stats["count"] / total) * 100, 1),
        })

    result.sort(key=lambda f: f["coverage"], reverse=True)
    return result


def _collect_paths(
    obj: Any, prefix: str, stats: Dict[str, Dict[str, Any]], depth: int
) -> None:
    """Recursively flatten a nested dict into dot-notation paths."""
    if depth > MAX_DEPTH or not isinstance(obj, dict):
        return

    for key, value in obj.items():
        path = f"{prefix}{key}"

        if value is None:
            if path not in stats:
                stats[path] = {"count": 0, "type": "null", "sample": None}
            continue

        if isinstance(value, dict):
            _collect_paths(value, path + ".", stats, depth + 1)
            continue

        type_name = _detect_type(value)

        if path in stats:
            stats[path]["count"] += 1
            if stats[path]["type"] == "null":
                stats[path]["type"] = type_name
            if stats[path]["sample"] is None:
                stats[path]["sample"] = value
        else:
            stats[path] = {
                "count": 1,
                "type": type_name,
                "sample": value,
            }


_ISO8601_RE = re.compile(r'^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?')
_COMMON_DATE_RE = re.compile(
    r'^(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'          # 01/15/2024 or 15-01-2024
    r'|^(?:[A-Z][a-z]{2}\s+\d{1,2},?\s+\d{4})'     # Jan 15, 2024
    r'|^(?:\d{1,2}-[A-Z][a-z]{2}-\d{4})'            # 15-Jan-2024
)

# Epoch range: 2000-01-01 to 2050-01-01
_EPOCH_S_MIN = 946684800
_EPOCH_S_MAX = 2524608000


def _detect_type(value: Any) -> str:
    """Return a JSON-style type name for a Python value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        is_whole = isinstance(value, int) or value.is_integer()
        if is_whole:
            n = int(value)
            if (_EPOCH_S_MIN <= n <= _EPOCH_S_MAX
                    or _EPOCH_S_MIN * 1000 <= n <= _EPOCH_S_MAX * 1000):
                return "datetime"
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        if _ISO8601_RE.match(value) or _COMMON_DATE_RE.match(value):
            return "datetime"
        return "string"
    return "object"
