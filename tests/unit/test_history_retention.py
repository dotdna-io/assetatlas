# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for history retention policy (assets_history / asset_providers_history)."""

from unittest.mock import MagicMock

import pytest

from src.api.modules.settings.settings_schema import (
    get_setting_definition,
    validate_setting_value,
)
from src.modules.data_retention.data_retention_service import (
    DataRetentionService,
    HISTORY_TABLES,
    _ALLOWED_HISTORY_TABLES,
)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestHistoryRetentionSchema:
    """History retention settings exist in schema with correct defaults."""

    @pytest.mark.parametrize("key,expected_default", [
        ('ASSETS_HISTORY_RETENTION_DAYS', 90),
        ('ASSETS_HISTORY_RETENTION_COUNT', 5),
        ('ASSET_PROVIDERS_HISTORY_RETENTION_DAYS', 90),
        ('ASSET_PROVIDERS_HISTORY_RETENTION_COUNT', 5),
    ])
    def test_setting_exists_with_default(self, key, expected_default):
        defn = get_setting_definition('retention', key)
        assert defn.default == expected_default

    @pytest.mark.parametrize("key", [
        'ASSETS_HISTORY_RETENTION_DAYS',
        'ASSETS_HISTORY_RETENTION_COUNT',
        'ASSET_PROVIDERS_HISTORY_RETENTION_DAYS',
        'ASSET_PROVIDERS_HISTORY_RETENTION_COUNT',
    ])
    def test_setting_section_is_pipeline_data(self, key):
        defn = get_setting_definition('retention', key)
        assert defn.section == 'Pipeline Data'

    @pytest.mark.parametrize("key,good,bad_low,bad_high", [
        ('ASSETS_HISTORY_RETENTION_DAYS', 90, 6, 731),
        ('ASSETS_HISTORY_RETENTION_COUNT', 10, 0, 101),
        ('ASSET_PROVIDERS_HISTORY_RETENTION_DAYS', 90, 6, 731),
        ('ASSET_PROVIDERS_HISTORY_RETENTION_COUNT', 10, 0, 101),
    ])
    def test_validates_range(self, key, good, bad_low, bad_high):
        assert validate_setting_value('retention', key, good) is True
        assert validate_setting_value('retention', key, bad_low) is False
        assert validate_setting_value('retention', key, bad_high) is False


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------

class TestHistoryRetentionService:
    """DataRetentionService._cleanup_history_table works correctly."""

    def _make_service(self, days=90, count=5):
        db_adapter = MagicMock()
        settings = MagicMock()
        settings.get = lambda cat, key, default: {
            'ASSETS_HISTORY_RETENTION_DAYS': days,
            'ASSETS_HISTORY_RETENTION_COUNT': count,
        }.get(key, default)
        return DataRetentionService(db_adapter, settings), db_adapter

    def test_deletes_expired_rows(self):
        svc, db = self._make_service(days=30, count=3)
        # First call: hoisted latest_runs; second: batch of 100 deleted
        db.execute_query.side_effect = [[], [{'1': 1}] * 100]
        deleted = svc._cleanup_history_table(
            'assets_history',
            'ASSETS_HISTORY_RETENTION_DAYS',
            'ASSETS_HISTORY_RETENTION_COUNT',
        )
        assert deleted == 100
        assert db.execute_query.call_count == 2  # 1 hoisted + 1 batch

    def test_batches_until_done(self):
        svc, db = self._make_service()
        # First call: hoisted latest_runs; then full batch, then partial
        db.execute_query.side_effect = [
            [],
            [{'1': 1}] * 10_000,
            [{'1': 1}] * 500,
        ]
        deleted = svc._cleanup_history_table(
            'assets_history',
            'ASSETS_HISTORY_RETENTION_DAYS',
            'ASSETS_HISTORY_RETENTION_COUNT',
        )
        assert deleted == 10_500
        assert db.execute_query.call_count == 3  # 1 hoisted + 2 batches

    def test_nothing_to_delete(self):
        svc, db = self._make_service()
        # First call: hoisted latest_runs; second: empty batch
        db.execute_query.side_effect = [[], []]
        deleted = svc._cleanup_history_table(
            'assets_history',
            'ASSETS_HISTORY_RETENTION_DAYS',
            'ASSETS_HISTORY_RETENTION_COUNT',
        )
        assert deleted == 0

    def test_rejects_unknown_table(self):
        svc, db = self._make_service()
        deleted = svc._cleanup_history_table(
            'users', 'ASSETS_HISTORY_RETENTION_DAYS', 'ASSETS_HISTORY_RETENTION_COUNT'
        )
        assert deleted == -1
        db.execute_query.assert_not_called()

    def test_returns_negative_on_error(self):
        svc, db = self._make_service()
        db.execute_query.side_effect = Exception("connection lost")
        deleted = svc._cleanup_history_table(
            'assets_history',
            'ASSETS_HISTORY_RETENTION_DAYS',
            'ASSETS_HISTORY_RETENTION_COUNT',
        )
        assert deleted == -1

    def test_sql_uses_smart_retention(self):
        """Verify the query uses generation_run_id-based retention."""
        svc, db = self._make_service()
        # First call: hoisted latest_runs query; second call: batched delete
        db.execute_query.side_effect = [[], []]
        svc._cleanup_history_table(
            'assets_history',
            'ASSETS_HISTORY_RETENTION_DAYS',
            'ASSETS_HISTORY_RETENTION_COUNT',
        )
        # First query fetches protected generation runs
        first_sql = db.execute_query.call_args_list[0][0][0]
        assert 'generation_run_id' in first_sql
        # Second query uses NOT EXISTS for deletion
        second_sql = db.execute_query.call_args_list[1][0][0]
        assert 'NOT EXISTS' in second_sql

    def test_history_tables_in_allowlist(self):
        assert 'assets_history' in _ALLOWED_HISTORY_TABLES
        assert 'asset_providers_history' in _ALLOWED_HISTORY_TABLES

    def test_run_cleanup_includes_history_tables(self):
        """run_cleanup() calls history retention for both tables."""
        db = MagicMock()
        db.execute_query.return_value = []
        settings = MagicMock()
        settings.get = MagicMock(return_value=90)
        svc = DataRetentionService(db, settings)

        results = svc.run_cleanup()
        assert 'assets_history' in results
        assert 'asset_providers_history' in results


class TestAlertEventsRetention:
    """alert_events retention: settings + run/ts-scoped cleanup."""

    @pytest.mark.parametrize("key,expected_default", [
        ('ALERT_EVENTS_RETENTION_DAYS', 90),
        ('ALERT_EVENTS_RETENTION_COUNT', 30),
    ])
    def test_setting_exists_with_default(self, key, expected_default):
        assert get_setting_definition('retention', key).default == expected_default

    @pytest.mark.parametrize("key", ['ALERT_EVENTS_RETENTION_DAYS', 'ALERT_EVENTS_RETENTION_COUNT'])
    def test_setting_section_is_pipeline_data(self, key):
        assert get_setting_definition('retention', key).section == 'Pipeline Data'

    def test_registered_with_run_scoped_columns(self):
        entry = next(t for t in HISTORY_TABLES if t[0] == 'alert_events')
        assert entry == ('alert_events', 'ALERT_EVENTS_RETENTION_DAYS',
                         'ALERT_EVENTS_RETENTION_COUNT', 'pipeline_run_id', 'created_at')

    def test_cleanup_uses_its_own_columns(self):
        from src.modules.data_retention.data_retention_service import DataRetentionService
        db = MagicMock()
        settings = MagicMock()
        settings.get = lambda cat, key, default: {
            'ALERT_EVENTS_RETENTION_DAYS': 90, 'ALERT_EVENTS_RETENTION_COUNT': 30}.get(key, default)
        db.execute_query.side_effect = [[], []]  # hoisted keep-set, then empty batch
        svc = DataRetentionService(db, settings)
        svc._cleanup_history_table('alert_events', 'ALERT_EVENTS_RETENTION_DAYS',
                                   'ALERT_EVENTS_RETENTION_COUNT', 'pipeline_run_id', 'created_at')
        keep_sql = db.execute_query.call_args_list[0].args[0]
        assert 'pipeline_run_id' in keep_sql and 'created_at' in keep_sql

    def test_rejects_non_allowlisted_column(self):
        from src.modules.data_retention.data_retention_service import DataRetentionService
        svc = DataRetentionService(MagicMock(), MagicMock())
        assert svc._cleanup_history_table(
            'alert_events', 'D', 'C', 'pipeline_run_id; DROP TABLE alerts', 'created_at') == -1
