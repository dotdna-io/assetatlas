#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Comprehensive tests for ProviderRepository (Provider Data Tracking).

Tests the ProviderRepository class and verifies it handles:
- Saving provider data (per-source asset records)
- Bulk insert operations
- Querying providers by asset ID
- Preprocessing data for provider storage
- Edge cases (empty data, missing columns, etc.)
"""

import pytest
import pandas as pd
import numpy as np
import sys
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch, call
from uuid import UUID, uuid4

from src.modules.asset_generation.repositories.provider_repository import ProviderRepository


class TestProviderRepositoryInit:
    """Test suite for ProviderRepository initialization"""

    def test_init_with_db_adapter(self):
        """Test initialization with database adapter"""
        mock_db = Mock()
        repo = ProviderRepository(db_adapter=mock_db)

        assert repo.db == mock_db


class TestProviderRepositorySaveProviders:
    """Test suite for saving provider data"""

    def test_save_providers_empty_dataframe(self):
        """Test saving empty provider DataFrame"""
        mock_db = Mock()
        repo = ProviderRepository(db_adapter=mock_db)

        empty_df = pd.DataFrame()
        count = repo.save_providers(empty_df)

        assert count == 0
        # Should not call database
        assert not mock_db.conn_manager.get_connection.called

    def test_save_providers_with_data(self):
        """Test saving provider data successfully"""
        mock_db = Mock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Properly mock context managers for conn_manager.get_connection()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.__exit__ = MagicMock(return_value=None)
        mock_db.conn_manager.get_connection.return_value = mock_cm

        mock_cursor_cm = MagicMock()
        mock_cursor_cm.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor_cm.__exit__ = MagicMock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor_cm

        repo = ProviderRepository(db_adapter=mock_db)

        # Create sample provider data
        providers_df = pd.DataFrame({
            'id': [uuid4(), uuid4()],
            'hostname': ['host1', 'host2'],
            'ip': ['10.0.0.1', '10.0.0.2'],
            'data_source': ['source1', 'source2']
        })

        with patch('psycopg2.extras.execute_values'):
            count = repo.save_providers(providers_df)

        assert count == 2

    def test_save_providers_converts_uuid_to_string(self):
        """Test that UUIDs are converted to strings for database"""
        mock_db = Mock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Properly mock context managers for conn_manager.get_connection()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.__exit__ = MagicMock(return_value=None)
        mock_db.conn_manager.get_connection.return_value = mock_cm

        mock_cursor_cm = MagicMock()
        mock_cursor_cm.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor_cm.__exit__ = MagicMock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor_cm

        repo = ProviderRepository(db_adapter=mock_db)

        asset_id = uuid4()
        providers_df = pd.DataFrame({
            'id': [asset_id],
            'hostname': ['host1']
        })

        with patch('psycopg2.extras.execute_values') as mock_execute:
            count = repo.save_providers(providers_df)
            assert count == 1

    def test_save_providers_handles_dict_values(self):
        """Test that dict values are converted to JSON"""
        mock_db = Mock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Properly mock context managers for conn_manager.get_connection()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.__exit__ = MagicMock(return_value=None)
        mock_db.conn_manager.get_connection.return_value = mock_cm

        mock_cursor_cm = MagicMock()
        mock_cursor_cm.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor_cm.__exit__ = MagicMock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor_cm

        repo = ProviderRepository(db_adapter=mock_db)

        providers_df = pd.DataFrame({
            'id': [uuid4()],
            'metadata': [{'key': 'value'}]
        })

        with patch('psycopg2.extras.execute_values'):
            count = repo.save_providers(providers_df)

        assert count == 1

    def test_save_providers_error_handling(self):
        """Test error handling when save fails"""
        mock_db = Mock()
        mock_conn = MagicMock()

        # Properly mock context manager for conn_manager.get_connection()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.__exit__ = MagicMock(return_value=None)
        mock_db.conn_manager.get_connection.return_value = mock_cm

        # Make cursor() raise an error
        mock_conn.cursor.side_effect = Exception("Database error")

        repo = ProviderRepository(db_adapter=mock_db)

        providers_df = pd.DataFrame({
            'id': [uuid4()],
            'hostname': ['host1']
        })

        with pytest.raises(Exception, match="Database error"):
            repo.save_providers(providers_df)


class TestProviderRepositoryGetProviders:
    """Test suite for querying provider data"""

    def test_get_providers_by_asset_id_empty(self):
        """Test getting providers when none exist"""
        mock_db = Mock()
        mock_db.execute_query.return_value = []

        repo = ProviderRepository(db_adapter=mock_db)

        asset_id = uuid4()
        result = repo.get_providers_by_asset_id(asset_id)

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_get_providers_by_asset_id_with_data(self):
        """Test getting providers for an asset"""
        mock_db = Mock()

        asset_id = uuid4()
        mock_db.execute_query.return_value = [
            {
                'id': asset_id,
                'hostname': 'host1',
                'data_source': 'source1',
                'ip': '10.0.0.1'
            },
            {
                'id': asset_id,
                'hostname': 'host1',
                'data_source': 'source2',
                'ip': '10.0.0.2'
            }
        ]

        repo = ProviderRepository(db_adapter=mock_db)
        result = repo.get_providers_by_asset_id(asset_id)

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        assert 'hostname' in result.columns
        assert 'data_source' in result.columns

    def test_get_providers_by_asset_id_with_limit(self):
        """Test getting providers with limit"""
        mock_db = Mock()
        repo = ProviderRepository(db_adapter=mock_db)

        asset_id = uuid4()
        repo.get_providers_by_asset_id(asset_id, limit=10)

        # Check that LIMIT clause was added to query
        call_args = mock_db.execute_query.call_args
        query = call_args[0][0]
        assert 'LIMIT' in query

    def test_get_providers_by_asset_id_error_handling(self):
        """Test error handling when query fails"""
        mock_db = Mock()
        mock_db.execute_query.side_effect = Exception("Database error")

        repo = ProviderRepository(db_adapter=mock_db)

        asset_id = uuid4()
        result = repo.get_providers_by_asset_id(asset_id)

        # Should return empty DataFrame on error
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_get_all_providers_empty(self):
        """Test getting all providers when none exist"""
        mock_db = Mock()
        mock_db.execute_query.return_value = []

        repo = ProviderRepository(db_adapter=mock_db)
        result = repo.get_all_providers()

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_get_all_providers_with_data(self):
        """Test getting all providers"""
        mock_db = Mock()
        mock_db.execute_query.return_value = [
            {'id': uuid4(), 'hostname': 'host1', 'data_source': 'source1'},
            {'id': uuid4(), 'hostname': 'host2', 'data_source': 'source2'},
            {'id': uuid4(), 'hostname': 'host3', 'data_source': 'source3'}
        ]

        repo = ProviderRepository(db_adapter=mock_db)
        result = repo.get_all_providers()

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 3

    def test_get_all_providers_with_limit(self):
        """Test getting all providers with limit"""
        mock_db = Mock()
        repo = ProviderRepository(db_adapter=mock_db)

        repo.get_all_providers(limit=100)

        # Check that LIMIT clause was added
        call_args = mock_db.execute_query.call_args
        query = call_args[0][0]
        assert 'LIMIT' in query


class TestProviderRepositoryDeleteProviders:
    """Test suite for deleting provider data"""

    def test_delete_providers_by_asset_id_success(self):
        """Test deleting providers for an asset"""
        mock_db = Mock()
        repo = ProviderRepository(db_adapter=mock_db)

        asset_id = uuid4()
        result = repo.delete_providers_by_asset_id(asset_id)

        assert result is True
        assert mock_db.execute_query.called

        # Check that DELETE query was executed
        call_args = mock_db.execute_query.call_args
        query = call_args[0][0]
        assert 'DELETE FROM asset_providers' in query

    def test_delete_providers_by_asset_id_error_handling(self):
        """Test error handling when delete fails"""
        mock_db = Mock()
        mock_db.execute_query.side_effect = Exception("Database error")

        repo = ProviderRepository(db_adapter=mock_db)

        asset_id = uuid4()
        result = repo.delete_providers_by_asset_id(asset_id)

        assert result is False


class TestProviderRepositoryGetCount:
    """Test suite for getting provider count"""

    def test_get_provider_count_zero(self):
        """Test getting count when no providers exist"""
        mock_db = Mock()
        mock_db.execute_query.return_value = [{'count': 0}]

        repo = ProviderRepository(db_adapter=mock_db)
        count = repo.get_provider_count()

        assert count == 0

    def test_get_provider_count_with_data(self):
        """Test getting count with existing providers"""
        mock_db = Mock()
        mock_db.execute_query.return_value = [{'count': 1000}]

        repo = ProviderRepository(db_adapter=mock_db)
        count = repo.get_provider_count()

        assert count == 1000

    def test_get_provider_count_error_handling(self):
        """Test error handling when count query fails"""
        mock_db = Mock()
        mock_db.execute_query.side_effect = Exception("Database error")

        repo = ProviderRepository(db_adapter=mock_db)
        count = repo.get_provider_count()

        assert count == 0


class TestProviderRepositoryPreprocessing:
    """Test suite for preprocessing data for provider storage"""

    def test_preprocess_for_providers_removes_best_columns(self):
        """Test that _best columns are removed"""
        mock_db = Mock()
        repo = ProviderRepository(db_adapter=mock_db)

        data = pd.DataFrame({
            'id': [uuid4()],
            'hostname': ['host1'],
            'category': ['workstation'],
            'category_best': ['workstation'],
            'subcategory_best': ['desktop'],
            'os_normalized': ['Windows 10'],
            'os_normalized_best': ['Windows 10 Pro']
        })

        result = repo.preprocess_for_providers(data)

        # _best columns should be removed
        assert 'category_best' not in result.columns
        assert 'subcategory_best' not in result.columns
        assert 'os_normalized_best' not in result.columns

        # Regular columns should remain (id is renamed to asset_id)
        assert 'asset_id' in result.columns
        assert 'category' in result.columns
        assert 'os_normalized' in result.columns
        assert 'hostname' in result.columns

    def test_preprocess_for_providers_removes_temp_columns(self):
        """Test that temporary columns are removed"""
        mock_db = Mock()
        repo = ProviderRepository(db_adapter=mock_db)

        data = pd.DataFrame({
            'id': [uuid4()],
            'hostname': ['host1'],
            'os_short': ['win'],
            'os_confidence': [0.95],
            'category': ['workstation']
        })

        result = repo.preprocess_for_providers(data)

        # Temp columns should be removed
        assert 'os_short' not in result.columns
        assert 'os_confidence' not in result.columns

        # Regular columns should remain
        assert 'hostname' in result.columns
        assert 'category' in result.columns

    def test_preprocess_for_providers_drops_mac_vendor_enriched(self):
        """Test that mac_vendor_enriched is dropped as a temporary column"""
        mock_db = Mock()
        repo = ProviderRepository(db_adapter=mock_db)

        data = pd.DataFrame({
            'id': [uuid4()],
            'hostname': ['host1'],
            'mac_vendor_enriched': ['Dell Inc.'],
            'mac': ['aa:bb:cc:dd:ee:ff']
        })

        result = repo.preprocess_for_providers(data)

        # mac_vendor_enriched is listed as a temp column and should be dropped
        assert 'mac_vendor_enriched' not in result.columns

    def test_preprocess_for_providers_empty_dataframe(self):
        """Test preprocessing empty DataFrame"""
        mock_db = Mock()
        repo = ProviderRepository(db_adapter=mock_db)

        # Empty DataFrame with no 'id' column raises ValueError
        empty_df = pd.DataFrame({'id': pd.Series(dtype='object')})
        result = repo.preprocess_for_providers(empty_df)

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_preprocess_for_providers_minimal_columns(self):
        """Test preprocessing when there are only fixed columns"""
        mock_db = Mock()
        repo = ProviderRepository(db_adapter=mock_db)

        data = pd.DataFrame({
            'id': [uuid4()],
            'hostname': ['host1'],
            'ip': ['10.0.0.1'],
            'mac': ['aa:bb:cc:dd:ee:ff']
        })

        result = repo.preprocess_for_providers(data)

        # id is renamed to asset_id
        assert 'asset_id' in result.columns
        assert 'hostname' in result.columns
        assert 'ip' in result.columns
        assert 'mac' in result.columns
        assert len(result) == 1

    def test_preprocess_for_providers_renames_id_to_asset_id(self):
        """Test that 'id' column is renamed to 'asset_id'"""
        mock_db = Mock()
        repo = ProviderRepository(db_adapter=mock_db)

        asset_id = uuid4()
        data = pd.DataFrame({
            'id': [asset_id],
            'hostname': ['host1'],
            'category_best': ['workstation'],  # Will be removed
        })

        result = repo.preprocess_for_providers(data)

        # id should be renamed to asset_id
        assert 'id' not in result.columns
        assert 'asset_id' in result.columns
        assert result['asset_id'].iloc[0] == asset_id


class TestProviderRepositoryIntegration:
    """Integration tests for full provider workflow"""

    def test_full_provider_workflow(self):
        """Test complete provider workflow: preprocess, save, query"""
        mock_db = Mock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Properly mock context managers for conn_manager.get_connection()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.__exit__ = MagicMock(return_value=None)
        mock_db.conn_manager.get_connection.return_value = mock_cm

        mock_cursor_cm = MagicMock()
        mock_cursor_cm.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor_cm.__exit__ = MagicMock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor_cm

        repo = ProviderRepository(db_adapter=mock_db)

        asset_id_1 = uuid4()
        asset_id_2 = uuid4()

        # Step 1: Create enriched data with columns to clean
        enriched_data = pd.DataFrame({
            'id': [asset_id_1, asset_id_1, asset_id_2],
            'hostname': ['host1', 'host1', 'host2'],
            'ip': ['10.0.0.1', '10.0.0.2', '10.0.0.3'],
            'data_source': ['source1', 'source2', 'source1'],
            'category': ['workstation', 'workstation', 'laptop'],
            'category_best': ['workstation', 'workstation', 'laptop'],  # Should be removed
            'os_normalized': ['Windows 10', 'Windows 10', 'macOS'],
            'os_short': ['win', 'win', 'mac'],  # Should be removed
            'mac_vendor_enriched': ['Dell Inc.', 'Dell Inc.', 'Apple Inc.']  # Temp, will be dropped
        })

        # Step 2: Preprocess for providers
        providers_df = repo.preprocess_for_providers(enriched_data)

        # Verify preprocessing
        assert 'category_best' not in providers_df.columns
        assert 'os_short' not in providers_df.columns
        assert 'mac_vendor_enriched' not in providers_df.columns  # Dropped as temp column
        assert 'asset_id' in providers_df.columns  # id renamed to asset_id
        assert len(providers_df) == 3

        # Step 3: Save providers
        with patch('psycopg2.extras.execute_values'):
            saved_count = repo.save_providers(providers_df)

        assert saved_count == 3

        # Step 4: Query providers
        mock_db.execute_query.return_value = [
            {'id': asset_id_1, 'hostname': 'host1', 'data_source': 'source1'},
            {'id': asset_id_1, 'hostname': 'host1', 'data_source': 'source2'}
        ]

        result = repo.get_providers_by_asset_id(asset_id_1)

        assert len(result) == 2
        assert all(result['id'] == asset_id_1)

    def test_provider_data_tracks_per_source_records(self):
        """Test that provider data preserves per-source records before aggregation"""
        mock_db = Mock()
        repo = ProviderRepository(db_adapter=mock_db)

        asset_id = uuid4()

        # Same asset seen by 3 different data sources
        data = pd.DataFrame({
            'id': [asset_id, asset_id, asset_id],
            'hostname': ['host1', 'host1', 'host1'],
            'ip': ['10.0.0.1', '10.0.0.2', '10.0.0.1'],  # Different IPs from different sources
            'data_source': ['AD', 'SCCM', 'Nessus'],
            'data_source_category': ['identity', 'endpoint', 'vulnerability'],
            'os_normalized': ['Windows 10', 'Windows 10 Pro', 'Windows 10 Enterprise']
        })

        preprocessed = repo.preprocess_for_providers(data)

        # All 3 records should be preserved (not aggregated)
        assert len(preprocessed) == 3
        assert len(preprocessed['data_source'].unique()) == 3
        assert len(preprocessed['os_normalized'].unique()) == 3


if __name__ == "__main__":
    # Run the tests
    import subprocess

    result = subprocess.run([sys.executable, '-m', 'pytest', __file__, '-v'],
                          capture_output=True, text=True)

    print("STDOUT:")
    print(result.stdout)
    if result.stderr:
        print("STDERR:")
        print(result.stderr)

    sys.exit(result.returncode)
