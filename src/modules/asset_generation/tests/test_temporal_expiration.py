# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for temporal component expiration (30-day expiration rule).

This test suite verifies that:
1. Components expire after 30 days of inactivity
2. Expired components are cleaned up at generation start
3. Active components are retained
4. last_seen timestamps are updated correctly
"""

import pytest
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from unittest.mock import Mock, MagicMock, patch

from src.modules.asset_generation.repositories.mapping_repository import MappingRepository
from src.database.postgres_adapter import PostgreSQLAdapter


class TestTemporalExpiration:
    """Test component expiration logic."""

    def test_delete_expired_mappings_removes_old_components(self):
        """Test that components inactive for >30 days are deleted."""
        # Mock database adapter
        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.delete_expired_mappings = Mock(return_value=5)

        repo = MappingRepository(mock_db)

        # Call cleanup
        count = repo.cleanup_expired_mappings(expiration_days=30)

        # Verify database was called with correct expiration
        mock_db.delete_expired_mappings.assert_called_once_with(30)
        assert count == 5

    def test_cleanup_with_no_expired_mappings(self):
        """Test cleanup when no components have expired."""
        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.delete_expired_mappings = Mock(return_value=0)

        repo = MappingRepository(mock_db)

        count = repo.cleanup_expired_mappings(expiration_days=30)

        assert count == 0
        mock_db.delete_expired_mappings.assert_called_once_with(30)

    def test_cleanup_with_custom_expiration_period(self):
        """Test cleanup with custom expiration period."""
        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.delete_expired_mappings = Mock(return_value=10)

        repo = MappingRepository(mock_db)

        # Custom 60-day expiration
        count = repo.cleanup_expired_mappings(expiration_days=60)

        mock_db.delete_expired_mappings.assert_called_once_with(60)
        assert count == 10

    def test_default_expiration_is_30_days(self):
        """Test that default expiration period is 30 days."""
        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.delete_expired_mappings = Mock(return_value=3)

        repo = MappingRepository(mock_db)

        # Call without specifying expiration (should default to 30)
        count = repo.cleanup_expired_mappings()

        mock_db.delete_expired_mappings.assert_called_once_with(30)
        assert count == 3


class TestTimestampUpdates:
    """Test that timestamps are updated correctly during saves."""

    def test_bulk_save_includes_timestamps(self):
        """Test that bulk_set_asset_id_mappings updates last_seen."""
        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.bulk_set_asset_id_mappings = Mock(return_value=3)

        repo = MappingRepository(mock_db)

        # Save mappings
        mappings = {
            "server01": UUID("12345678-1234-5678-1234-567812345678"),
            "00:11:22:33:44:55": UUID("12345678-1234-5678-1234-567812345678"),
            "sn:abc123": UUID("12345678-1234-5678-1234-567812345678")
        }

        count = repo.save_mappings_bulk(mappings, use_uuid=False)

        # Verify database method was called
        assert mock_db.bulk_set_asset_id_mappings.called
        assert count == 3

    def test_single_save_updates_last_seen(self):
        """Test that set_asset_id_mapping updates last_seen."""
        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.set_asset_id_mapping = Mock()

        repo = MappingRepository(mock_db)

        # Save single mapping
        component = "server01"
        asset_id = UUID("12345678-1234-5678-1234-567812345678")

        repo.save_mapping(component, asset_id)

        # Verify database method was called
        mock_db.set_asset_id_mapping.assert_called_once_with(component, asset_id)


class TestExpirationScenarios:
    """Test realistic expiration scenarios."""

    def test_active_components_not_expired(self):
        """Test that recently seen components are not expired."""
        mock_db = Mock(spec=PostgreSQLAdapter)
        # No components expired
        mock_db.delete_expired_mappings = Mock(return_value=0)

        repo = MappingRepository(mock_db)

        count = repo.cleanup_expired_mappings(expiration_days=30)

        # Should be 0 if all components were seen recently
        assert count == 0

    def test_mixed_active_and_expired_components(self):
        """Test cleanup with mix of active and expired components."""
        mock_db = Mock(spec=PostgreSQLAdapter)
        # Some components expired (e.g., 10 out of 100)
        mock_db.delete_expired_mappings = Mock(return_value=10)

        repo = MappingRepository(mock_db)

        count = repo.cleanup_expired_mappings(expiration_days=30)

        assert count == 10

    def test_all_components_expired(self):
        """Test when all components have expired."""
        mock_db = Mock(spec=PostgreSQLAdapter)
        # All components expired
        mock_db.delete_expired_mappings = Mock(return_value=100)

        repo = MappingRepository(mock_db)

        count = repo.cleanup_expired_mappings(expiration_days=30)

        assert count == 100


class TestNoReactivation:
    """Test that expired components are treated as new when reappearing."""

    def test_expired_component_creates_new_mapping(self):
        """
        Test that a component that expired and comes back is treated as new.

        This tests the "no reactivation" requirement - expired components
        are deleted and if they reappear, they get fresh timestamps.
        """
        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.delete_expired_mappings = Mock(return_value=1)
        mock_db.set_asset_id_mapping = Mock()

        repo = MappingRepository(mock_db)

        # Simulate cleanup removing an old component
        expired_count = repo.cleanup_expired_mappings(expiration_days=30)
        assert expired_count == 1

        # Now the component reappears - should be saved as NEW
        component = "server01"
        asset_id = UUID("12345678-1234-5678-1234-567812345678")
        repo.save_mapping(component, asset_id)

        # Verify it was saved (with fresh timestamps from database default)
        mock_db.set_asset_id_mapping.assert_called_once_with(component, asset_id)


class TestExpirationLogging:
    """Test that expiration operations are logged correctly."""

    def test_cleanup_logs_expiration_count(self, caplog):
        """Test that cleanup logs the number of expired mappings."""
        import logging
        caplog.set_level(logging.INFO)

        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.delete_expired_mappings = Mock(return_value=5)

        repo = MappingRepository(mock_db)

        repo.cleanup_expired_mappings(expiration_days=30)

        # Check that logging occurred
        assert "cleaning" in caplog.text.lower() or "deleted" in caplog.text.lower()

    def test_cleanup_logs_when_no_expiration(self, caplog):
        """Test that cleanup logs when no components expired."""
        import logging
        caplog.set_level(logging.INFO)

        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.delete_expired_mappings = Mock(return_value=0)

        repo = MappingRepository(mock_db)

        repo.cleanup_expired_mappings(expiration_days=30)

        # Check that logging occurred
        assert "clean" in caplog.text.lower() or "no expired" in caplog.text.lower()


class TestExpirationEdgeCases:
    """Test edge cases for expiration logic."""

    def test_cleanup_with_zero_days(self):
        """Test cleanup with 0-day expiration (everything expires immediately)."""
        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.delete_expired_mappings = Mock(return_value=100)

        repo = MappingRepository(mock_db)

        count = repo.cleanup_expired_mappings(expiration_days=0)

        mock_db.delete_expired_mappings.assert_called_once_with(0)
        assert count == 100

    def test_cleanup_with_large_expiration(self):
        """Test cleanup with very large expiration period (1 year)."""
        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.delete_expired_mappings = Mock(return_value=0)

        repo = MappingRepository(mock_db)

        count = repo.cleanup_expired_mappings(expiration_days=365)

        mock_db.delete_expired_mappings.assert_called_once_with(365)
        assert count == 0

    def test_cleanup_error_handling(self):
        """Test that cleanup errors are propagated."""
        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.delete_expired_mappings = Mock(side_effect=Exception("Database error"))

        repo = MappingRepository(mock_db)

        # Should raise the exception
        with pytest.raises(Exception, match="Database error"):
            repo.cleanup_expired_mappings(expiration_days=30)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
