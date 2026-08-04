# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for tags JSONB column refresh logic."""
import pytest
from unittest.mock import MagicMock, call
from src.modules.tag_rules.services.tags_column_service import refresh_tags_column, refresh_tags_for_asset


class TestRefreshTagsColumn:
    """Tests for bulk tags column refresh."""

    def test_refresh_builds_jsonb_from_both_tables(self):
        """Refresh should UNION asset_tags_v2 + asset_manual_tags and UPDATE assets.tags."""
        db = MagicMock()
        db.execute_query = MagicMock(return_value=None)

        result = refresh_tags_column(db)

        # Should execute a single consolidated query
        assert db.execute_query.call_count == 1
        query = db.execute_query.call_args_list[0][0][0]
        assert "asset_tags_v2" in query
        assert "asset_manual_tags" in query
        assert "jsonb_object_agg" in query
        assert result["status"] == "success"

    def test_refresh_handles_db_error(self):
        """Refresh should catch and report DB errors."""
        db = MagicMock()
        db.execute_query.side_effect = Exception("connection lost")

        result = refresh_tags_column(db)

        assert result["status"] == "failed"
        assert "connection lost" in result["error"]


class TestRefreshTagsForAsset:
    """Tests for single-asset tags column refresh."""

    def test_refresh_single_asset(self):
        """Should rebuild tags JSONB for one asset."""
        db = MagicMock()
        db.execute_query = MagicMock(return_value=None)
        asset_id = "550e8400-e29b-41d4-a716-446655440000"

        refresh_tags_for_asset(db, asset_id)

        assert db.execute_query.call_count == 1
        query = db.execute_query.call_args[0][0]
        assert "asset_tags_v2" in query
        assert "asset_manual_tags" in query
        params = db.execute_query.call_args[0][1]
        assert params["asset_id"] == asset_id

    def test_refresh_single_asset_propagates_error(self):
        """Should raise on DB error (caller handles it)."""
        db = MagicMock()
        db.execute_query.side_effect = Exception("timeout")

        with pytest.raises(Exception, match="timeout"):
            refresh_tags_for_asset(db, "some-id")
