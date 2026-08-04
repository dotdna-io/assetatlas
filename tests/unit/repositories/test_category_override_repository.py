# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for CategoryOverrideRepository.save_override."""

import uuid
from unittest.mock import MagicMock, call

import pytest
from psycopg2.extras import Json

from src.modules.asset_generation.repositories.category_override_repository import (
    CategoryOverrideRepository,
)


@pytest.fixture
def repo():
    return CategoryOverrideRepository(db_adapter=MagicMock())


class TestSaveOverride:
    """save_override persists category overrides with optional features_snapshot."""

    def test_save_without_features_snapshot(self, repo):
        """features_snapshot defaults to None and is passed as None."""
        asset_id = uuid.uuid4()
        repo.save_override(asset_id, "server")

        repo.db.execute_query.assert_called_once()
        _, params, _ = repo.db.execute_query.call_args[0][0], \
            repo.db.execute_query.call_args[0][1], \
            repo.db.execute_query.call_args[1]
        # features_snapshot is last positional param (index 5)
        assert params[5] is None

    def test_save_with_features_snapshot(self, repo):
        """features_snapshot is wrapped in Json() and included in params."""
        asset_id = uuid.uuid4()
        snapshot = {"os": "Windows", "confidence": 0.92}

        repo.save_override(asset_id, "server", features_snapshot=snapshot)

        repo.db.execute_query.assert_called_once()
        params = repo.db.execute_query.call_args[0][1]
        # features_snapshot is the 6th positional param (index 5)
        assert isinstance(params[5], Json)
        assert params[5].adapted == snapshot

    def test_features_snapshot_in_insert_column_list(self, repo):
        """INSERT query includes features_snapshot column."""
        asset_id = uuid.uuid4()
        repo.save_override(asset_id, "laptop", features_snapshot={"os": "Linux"})

        sql = repo.db.execute_query.call_args[0][0]
        assert "features_snapshot" in sql

    def test_features_snapshot_in_on_conflict_update(self, repo):
        """ON CONFLICT DO UPDATE SET includes features_snapshot."""
        asset_id = uuid.uuid4()
        repo.save_override(asset_id, "laptop", features_snapshot={"os": "Linux"})

        sql = repo.db.execute_query.call_args[0][0]
        # Must appear in the UPDATE SET clause, not just the INSERT list
        update_section = sql[sql.index("DO UPDATE SET"):]
        assert "features_snapshot" in update_section

    def test_category_is_lowercased(self, repo):
        """Category value is always stored in lowercase."""
        asset_id = uuid.uuid4()
        repo.save_override(asset_id, "SERVER")

        params = repo.db.execute_query.call_args[0][1]
        assert params[1] == "server"

    def test_returns_true_on_success(self, repo):
        result = repo.save_override(uuid.uuid4(), "workstation")
        assert result is True

    def test_returns_false_on_db_error(self, repo):
        repo.db.execute_query.side_effect = Exception("DB error")
        result = repo.save_override(uuid.uuid4(), "workstation")
        assert result is False
