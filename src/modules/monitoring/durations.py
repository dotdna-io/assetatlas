# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prometheus duration helpers for the monitoring retention/storage panel.

Two distinct concerns, deliberately separate:

- parse_prometheus_duration: a LENIENT reader. Prometheus reports the applied
  retention flag (/api/v1/status/flags) in a normalized, possibly compound form
  (e.g. "30d" can come back as "4w2d"). We parse both sides to seconds so the
  desired-vs-applied comparison is semantic — a string compare would false-flag
  "pending".
- is_writable_duration: a STRICT writer guard. The only values we ever WRITE
  (UI preset -> the setting -> assetatlas-cli -> .env) are single-unit durations.
  This is the exact rule the CLI regex enforces, so a value the UI accepts can
  never be one the host later refuses.
"""

from __future__ import annotations

import re
from typing import Optional

# Prometheus duration units -> seconds. Mirrors model.Duration: y=365d, w=7d,
# d=24h. ms is sub-second; we round down to whole seconds.
_UNIT_SECONDS = {
    "ms": 0.001,
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
    "y": 31536000,
}

# Compound: one or more <int><unit> segments, e.g. "1y2w3d4h5m6s7ms". [0-9]
# (not \d) so we stay ASCII-only and byte-identical to the CLI's writable regex
# ^[0-9]+(ms|s|m|h|d|w|y)$ — Python's \d also matches non-ASCII digits (and
# int() parses them), which would let the UI accept a value the host refuses.
_COMPOUND_RE = re.compile(r"^(?:[0-9]+(?:ms|[smhdwy]))+$")
_SEGMENT_RE = re.compile(r"([0-9]+)(ms|[smhdwy])")
# Single-unit only, e.g. "30d" — the writable form.
_SINGLE_RE = re.compile(r"^[0-9]+(?:ms|[smhdwy])$")


def parse_prometheus_duration(value: str) -> Optional[int]:
    """Parse a Prometheus duration to whole seconds (lenient; accepts compound).

    Returns None for anything that is not a valid duration. "0" -> 0.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value == "0":
        return 0
    if not _COMPOUND_RE.match(value):
        return None
    total = 0.0
    for amount, unit in _SEGMENT_RE.findall(value):
        total += int(amount) * _UNIT_SECONDS[unit]
    return int(total)


def is_writable_duration(value: str) -> bool:
    """True iff value is a single-unit Prometheus duration (the writable form)."""
    return isinstance(value, str) and bool(_SINGLE_RE.match(value.strip()))
