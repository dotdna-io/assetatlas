# SPDX-License-Identifier: AGPL-3.0-or-later
"""Severity mapping for Prometheus alert labels → AssetAtlas alert severity enum."""
from __future__ import annotations

SEVERITY_MAP = {
    "critical": "critical",
    "warning": "high",
    "info": "low",
}
DEFAULT_SEVERITY = "medium"


def map_severity(label: str | None) -> str:
    if not label:
        return DEFAULT_SEVERITY
    return SEVERITY_MAP.get(label.lower(), DEFAULT_SEVERITY)
