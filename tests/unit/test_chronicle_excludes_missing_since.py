# SPDX-License-Identifier: AGPL-3.0-or-later
"""A missing_since-only change must not produce a chronicle update event (#795)."""

from src.modules.asset_generation.services.chronicle_service import ChronicleService


def test_missing_since_in_default_excluded_fields():
    assert 'missing_since' in ChronicleService.DEFAULT_EXCLUDED_FIELDS
