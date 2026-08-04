# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for BatchRepository helpers used by the integrations API.

Currently focused on the targeted single-integration timestamp lookup
that backs `GET /api/integrations/{id}` enrichment.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.integrations.repositories.batch_repository import (
    BatchRepository,
)


@pytest.fixture
def repo():
    with patch.object(
        BatchRepository, '__init__', lambda self, **kw: None
    ):
        r = BatchRepository()
        r.conn_manager = MagicMock()
    return r


def _mock_cursor(repo, row):
    cur = MagicMock()
    cur.fetchone.return_value = row
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)

    repo.conn_manager.get_connection.return_value = conn
    return cur


class TestGetLatestSyncAndRunForIntegration:

    def test_returns_both_timestamps(self, repo):
        from datetime import datetime, timezone
        sync = datetime(2026, 4, 28, 4, 30, tzinfo=timezone.utc)
        run = datetime(2026, 4, 28, 0, 53, tzinfo=timezone.utc)
        _mock_cursor(repo, {'last_sync_at': sync, 'last_run_at': run})

        result = repo.get_latest_sync_and_run_for_integration(uuid4())
        assert result == {'last_sync_at': sync, 'last_run_at': run}

    def test_returns_nulls_when_no_batches(self, repo):
        _mock_cursor(repo, {'last_sync_at': None, 'last_run_at': None})
        result = repo.get_latest_sync_and_run_for_integration(uuid4())
        assert result == {'last_sync_at': None, 'last_run_at': None}

    def test_returns_nulls_when_row_missing(self, repo):
        _mock_cursor(repo, None)
        result = repo.get_latest_sync_and_run_for_integration(uuid4())
        assert result == {'last_sync_at': None, 'last_run_at': None}

    def test_passes_integration_id_to_query(self, repo):
        cur = _mock_cursor(
            repo, {'last_sync_at': None, 'last_run_at': None}
        )
        iid = uuid4()
        repo.get_latest_sync_and_run_for_integration(iid)
        args = cur.execute.call_args[0][1]
        assert args == (str(iid),)
