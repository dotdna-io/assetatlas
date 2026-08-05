# SPDX-License-Identifier: AGPL-3.0-or-later
"""Confidence thresholds — the domain's single source of truth.

Lives in the shared layer (not ``src/api``) because repositories and
services under ``src/modules/`` need the defaults without importing from
the API layer (see tests/unit/test_layering.py).
``src.api.modules.settings.settings_schema`` re-exports both names, so
existing callers there keep working.
"""

from typing import Tuple

# Default confidence thresholds (medium, high) — single source of truth
DEFAULT_CONFIDENCE_THRESHOLDS = (0.45, 0.70)


def get_confidence_thresholds(settings_manager=None) -> Tuple[float, float]:
    """Return (medium, high) confidence thresholds — the single source of truth.

    Reads the `asset_generation.confidence_threshold_{medium,high}` settings.
    Falls back to DEFAULT_CONFIDENCE_THRESHOLDS when no manager is available
    (outside Flask/MCP context) or a value is unset, so callers never get a
    partial/None range.
    """
    if settings_manager is None:
        return DEFAULT_CONFIDENCE_THRESHOLDS
    try:
        medium = settings_manager.get('asset_generation', 'confidence_threshold_medium')
        high = settings_manager.get('asset_generation', 'confidence_threshold_high')
        if medium is None or high is None:
            return DEFAULT_CONFIDENCE_THRESHOLDS
        return (float(medium), float(high))
    except Exception:
        # Includes a non-numeric stored value (ValueError on float()) — degrade
        # to the documented default rather than 500-ing every caller.
        return DEFAULT_CONFIDENCE_THRESHOLDS
