# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chat session retention: DataRetentionService._cleanup_chat_sessions
resolves ``CHAT_RETENTION_DAYS`` from env and delegates to the shared
batched cleanup helper (so chat_sessions gets the same batching + allowlist
guard as other retention tables)."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.modules.data_retention.data_retention_service import (
    DataRetentionService,
)


def _make_service():
    db_adapter = MagicMock()
    settings = MagicMock()
    return DataRetentionService(db_adapter, settings), db_adapter


def test_default_retention_is_90_days(monkeypatch):
    """With CHAT_RETENTION_DAYS unset, falls back to 90 days."""
    monkeypatch.delenv('CHAT_RETENTION_DAYS', raising=False)
    svc, db = _make_service()
    db.execute_query.return_value = []

    svc._cleanup_chat_sessions()

    assert db.execute_query.called
    _, params = db.execute_query.call_args[0]
    assert params['days'] == 90


def test_reads_retention_from_env(monkeypatch):
    monkeypatch.setenv('CHAT_RETENTION_DAYS', '30')
    svc, db = _make_service()
    db.execute_query.return_value = [{'1': 1}] * 7

    deleted = svc._cleanup_chat_sessions()

    assert deleted == 7
    _, params = db.execute_query.call_args[0]
    assert params['days'] == 30


def test_uses_updated_at_age_filter(monkeypatch):
    """Retention is measured by updated_at, not created_at — an active
    session shouldn't get purged just because it's old."""
    monkeypatch.setenv('CHAT_RETENTION_DAYS', '90')
    svc, db = _make_service()
    db.execute_query.return_value = []
    svc._cleanup_chat_sessions()

    sql, _ = db.execute_query.call_args[0]
    assert 'DELETE FROM chat_sessions' in sql
    assert 'updated_at <' in sql


def test_invalid_env_falls_back_to_90(monkeypatch):
    monkeypatch.setenv('CHAT_RETENTION_DAYS', 'not-an-int')
    svc, db = _make_service()
    db.execute_query.return_value = []

    svc._cleanup_chat_sessions()

    _, params = db.execute_query.call_args[0]
    assert params['days'] == 90


def test_returns_minus_one_on_db_error(monkeypatch):
    monkeypatch.setenv('CHAT_RETENTION_DAYS', '90')
    svc, db = _make_service()
    db.execute_query.side_effect = RuntimeError("boom")

    assert svc._cleanup_chat_sessions() == -1


def test_run_cleanup_includes_chat_sessions(monkeypatch):
    """The top-level run_cleanup() surfaces chat_sessions in its summary."""
    monkeypatch.setenv('CHAT_RETENTION_DAYS', '90')
    svc, db = _make_service()
    svc.settings_manager.get = lambda cat, key, default: default
    db.execute_query.return_value = []

    svc._cleanup_ingestion_batches = MagicMock(return_value=0)
    svc._cleanup_history_table = MagicMock(return_value=0)
    svc._cleanup_table = MagicMock(return_value=0)
    svc._cleanup_sessions = MagicMock(return_value=0)

    results = svc.run_cleanup()
    assert 'chat_sessions' in results


def test_delegates_to_shared_batched_cleanup(monkeypatch):
    """Chat retention reuses _cleanup_table — this is what buys us the
    batching and allowlist the inline DELETE used to lack."""
    monkeypatch.setenv('CHAT_RETENTION_DAYS', '42')
    svc, _ = _make_service()
    svc._cleanup_table = MagicMock(return_value=99)

    result = svc._cleanup_chat_sessions()

    svc._cleanup_table.assert_called_once_with(
        'chat_sessions', 'updated_at', 42
    )
    assert result == 99


def test_chat_sessions_is_allowlisted():
    from src.modules.data_retention.data_retention_service import (
        _ALLOWED_TABLES, _ALLOWED_COLUMNS,
    )
    # Without the allowlist entries, _cleanup_table would refuse the delete.
    assert 'chat_sessions' in _ALLOWED_TABLES
    assert 'updated_at' in _ALLOWED_COLUMNS
