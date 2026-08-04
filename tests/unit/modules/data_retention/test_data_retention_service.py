# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for data retention service."""


def test_alert_deliveries_registered_for_age_based_retention():
    """Verify alert_deliveries is registered for age-based retention cleanup."""
    from src.modules.data_retention.data_retention_service import (
        RETENTION_TABLES, _ALLOWED_TABLES, _ALLOWED_COLUMNS)
    entry = [t for t in RETENTION_TABLES if t[0] == 'alert_deliveries']
    assert entry == [('alert_deliveries', 'created_at', 'ALERT_DELIVERIES_RETENTION_DAYS')]
    # allowlists derive from RETENTION_TABLES, so the table/column are safe to interpolate.
    assert 'alert_deliveries' in _ALLOWED_TABLES and 'created_at' in _ALLOWED_COLUMNS
