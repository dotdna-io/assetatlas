#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Comprehensive tests for CategoryOverrideRepository (Manual Category Overrides).

Tests the CategoryOverrideRepository class and verifies it handles:
- Loading overrides from PostgreSQL database
- Applying overrides while preserving category_predicted
- Saving and deleting overrides
- CRUD operations
- Edge cases (empty overrides, missing columns, etc.)
"""

import pytest
import pandas as pd
import sys
from pathlib import Path
from unittest.mock import Mock, MagicMock, call
from uuid import UUID, uuid4

from src.modules.asset_generation.repositories.category_override_repository import CategoryOverrideRepository


class TestCategoryOverrideRepositoryInit:
    """Test suite for CategoryOverrideRepository initialization"""

    def test_init_with_db_adapter(self):
        """Test initialization with database adapter"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        assert repo.db == mock_db


class TestCategoryOverrideRepositoryGetOverrides:
    """Test suite for loading overrides from database"""

    def test_get_all_overrides_empty(self):
        """Test loading overrides when none exist"""
        mock_db = Mock()
        mock_db.execute_query.return_value = []

        repo = CategoryOverrideRepository(db_adapter=mock_db)
        overrides = repo.get_all_overrides()

        assert overrides == {}
        assert mock_db.execute_query.called

    def test_get_all_overrides_with_data(self):
        """Test loading overrides with data"""
        mock_db = Mock()

        asset_id_1 = uuid4()
        asset_id_2 = uuid4()

        mock_db.execute_query.return_value = [
            {'asset_id': asset_id_1, 'category': 'Network Device', 'subcategory': None},
            {'asset_id': asset_id_2, 'category': 'Server', 'subcategory': None}
        ]

        repo = CategoryOverrideRepository(db_adapter=mock_db)
        overrides = repo.get_all_overrides()

        # Should return dict with lowercase categories and subcategory
        assert len(overrides) == 2
        assert overrides[str(asset_id_1)] == {'category': 'network device', 'subcategory': None}
        assert overrides[str(asset_id_2)] == {'category': 'server', 'subcategory': None}

    def test_get_all_overrides_filters_active_only(self):
        """Test that only active overrides are loaded"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        repo.get_all_overrides()

        # Check that the query filters by active=true
        call_args = mock_db.execute_query.call_args
        query = call_args[0][0]
        assert 'active = true' in query.lower()

    def test_get_all_overrides_error_handling(self):
        """Test error handling when database query fails"""
        mock_db = Mock()
        mock_db.execute_query.side_effect = Exception("Database error")

        repo = CategoryOverrideRepository(db_adapter=mock_db)
        overrides = repo.get_all_overrides()

        # Should return empty dict on error
        assert overrides == {}


class TestCategoryOverrideRepositoryApplyOverrides:
    """Test suite for applying overrides to assets"""

    def test_apply_overrides_empty_overrides(self):
        """Test applying overrides when none exist"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        data = pd.DataFrame({
            'id': [uuid4()],
            'category_best': ['workstation']
        })

        result = repo.apply_overrides(data, overrides={})

        # Should create category_predicted column even with no overrides
        assert 'category_predicted' in result.columns
        assert result['category_predicted'].iloc[0] == 'workstation'
        assert result['category_best'].iloc[0] == 'workstation'
        # category_is_overridden should be False when no overrides
        assert 'category_is_overridden' in result.columns
        assert result['category_is_overridden'].iloc[0] == False

    def test_apply_overrides_with_matches(self):
        """Test applying overrides to matching assets"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id_1 = uuid4()
        asset_id_2 = uuid4()
        asset_id_3 = uuid4()

        data = pd.DataFrame({
            'id': [asset_id_1, asset_id_2, asset_id_3],
            'category_best': ['workstation', 'workstation', 'laptop']
        })

        # Override asset_id_1 to network device
        overrides = {
            str(asset_id_1): {'category': 'network device', 'subcategory': None}
        }

        result = repo.apply_overrides(data, overrides)

        # Check that override was applied
        assert result[result['id'] == asset_id_1]['category_best'].iloc[0] == 'network device'
        assert result[result['id'] == asset_id_1]['category_predicted'].iloc[0] == 'workstation'

        # Other assets should remain unchanged
        assert result[result['id'] == asset_id_2]['category_best'].iloc[0] == 'workstation'
        assert result[result['id'] == asset_id_3]['category_best'].iloc[0] == 'laptop'

    def test_apply_overrides_multiple_matches(self):
        """Test applying multiple overrides"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id_1 = uuid4()
        asset_id_2 = uuid4()
        asset_id_3 = uuid4()

        data = pd.DataFrame({
            'id': [asset_id_1, asset_id_2, asset_id_3],
            'category_best': ['workstation', 'laptop', 'workstation']
        })

        overrides = {
            str(asset_id_1): {'category': 'network device', 'subcategory': None},
            str(asset_id_2): {'category': 'server', 'subcategory': None}
        }

        result = repo.apply_overrides(data, overrides)

        # Check overrides applied correctly
        assert result[result['id'] == asset_id_1]['category_best'].iloc[0] == 'network device'
        assert result[result['id'] == asset_id_1]['category_predicted'].iloc[0] == 'workstation'

        assert result[result['id'] == asset_id_2]['category_best'].iloc[0] == 'server'
        assert result[result['id'] == asset_id_2]['category_predicted'].iloc[0] == 'laptop'

        # asset_id_3 should be unchanged
        assert result[result['id'] == asset_id_3]['category_best'].iloc[0] == 'workstation'

    def test_apply_overrides_fallback_to_category(self):
        """Test that it falls back to 'category' column if 'category_best' doesn't exist"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id = uuid4()

        # Data with 'category' instead of 'category_best'
        data = pd.DataFrame({
            'id': [asset_id],
            'category': ['workstation']
        })

        overrides = {str(asset_id): {'category': 'network device', 'subcategory': None}}

        result = repo.apply_overrides(data, overrides)

        # Should apply override to 'category' column
        assert result['category'].iloc[0] == 'network device'
        assert result['category_predicted'].iloc[0] == 'workstation'

    def test_apply_overrides_missing_column(self):
        """Test handling when category column is missing"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        data = pd.DataFrame({
            'id': [uuid4()],
            'hostname': ['test-host']
        })

        overrides = {str(uuid4()): {'category': 'network device', 'subcategory': None}}

        # Should return data unchanged
        result = repo.apply_overrides(data, overrides)

        assert 'category_predicted' not in result.columns
        assert len(result) == 1

    def test_apply_overrides_custom_column_name(self):
        """Test applying overrides to custom column name"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id = uuid4()

        data = pd.DataFrame({
            'id': [asset_id],
            'my_custom_category': ['workstation']
        })

        overrides = {str(asset_id): {'category': 'server', 'subcategory': None}}

        result = repo.apply_overrides(data, overrides, category_column='my_custom_category')

        assert result['my_custom_category'].iloc[0] == 'server'
        assert result['category_predicted'].iloc[0] == 'workstation'

    def test_apply_overrides_preserves_ml_predictions(self):
        """Test that category_predicted preserves ML predictions"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id = uuid4()

        data = pd.DataFrame({
            'id': [asset_id],
            'category_best': ['workstation'],
            'category_prediction_score': [0.85]
        })

        overrides = {str(asset_id): {'category': 'network device', 'subcategory': None}}

        result = repo.apply_overrides(data, overrides)

        # Should preserve ML prediction in category_predicted
        assert result['category_predicted'].iloc[0] == 'workstation'
        # Score should be unchanged
        assert result['category_prediction_score'].iloc[0] == 0.85
        # But category_best should be overridden
        assert result['category_best'].iloc[0] == 'network device'

    def test_category_is_overridden_flag(self):
        """Test that category_is_overridden correctly flags overridden assets."""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id_1 = uuid4()
        asset_id_2 = uuid4()

        df = pd.DataFrame({
            'id': [asset_id_1, asset_id_2],
            'category_best': ['workstation', 'laptop']
        })

        overrides = {str(asset_id_1): {'category': 'server', 'subcategory': None}}

        result = repo.apply_overrides(df, overrides)

        # First asset was overridden
        assert result[result['id'] == asset_id_1]['category_is_overridden'].iloc[0] == True
        # Second asset was NOT overridden
        assert result[result['id'] == asset_id_2]['category_is_overridden'].iloc[0] == False


class TestCategoryOverrideRepositorySaveOverride:
    """Test suite for saving overrides"""

    def test_save_override_success(self):
        """Test saving a new override"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id = uuid4()
        result = repo.save_override(
            asset_id=asset_id,
            category='Network Device',
            reason='Manual classification by admin'
        )

        assert result is True
        assert mock_db.execute_query.called

        # Check the query parameters
        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert str(asset_id) in params
        assert 'network device' in params  # Should be lowercase

    def test_save_override_without_reason(self):
        """Test saving override without reason"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id = uuid4()
        result = repo.save_override(asset_id=asset_id, category='Server')

        assert result is True
        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert params[2] is None  # reason should be None

    def test_save_override_upsert_behavior(self):
        """Test that save uses UPSERT (ON CONFLICT)"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id = uuid4()
        repo.save_override(asset_id=asset_id, category='Server')

        # Check that query includes ON CONFLICT clause
        call_args = mock_db.execute_query.call_args
        query = call_args[0][0]
        assert 'ON CONFLICT' in query
        assert 'DO UPDATE' in query

    def test_save_override_error_handling(self):
        """Test error handling when save fails"""
        mock_db = Mock()
        mock_db.execute_query.side_effect = Exception("Database error")

        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id = uuid4()
        result = repo.save_override(asset_id=asset_id, category='Server')

        assert result is False


class TestCategoryOverrideRepositoryDeleteOverride:
    """Test suite for deleting overrides"""

    def test_delete_override_success(self):
        """Test deleting (deactivating) an override"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id = uuid4()
        result = repo.delete_override(asset_id)

        assert result is True
        assert mock_db.execute_query.called

        # Check that it sets active=false (soft delete)
        call_args = mock_db.execute_query.call_args
        query = call_args[0][0]
        assert 'active = false' in query.lower()

    def test_delete_override_error_handling(self):
        """Test error handling when delete fails"""
        mock_db = Mock()
        mock_db.execute_query.side_effect = Exception("Database error")

        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id = uuid4()
        result = repo.delete_override(asset_id)

        assert result is False


class TestCategoryOverrideRepositoryGetCount:
    """Test suite for getting override count"""

    def test_get_override_count_zero(self):
        """Test getting count when no overrides exist"""
        mock_db = Mock()
        mock_db.execute_query.return_value = [{'count': 0}]

        repo = CategoryOverrideRepository(db_adapter=mock_db)
        count = repo.get_override_count()

        assert count == 0

    def test_get_override_count_with_data(self):
        """Test getting count with existing overrides"""
        mock_db = Mock()
        mock_db.execute_query.return_value = [{'count': 42}]

        repo = CategoryOverrideRepository(db_adapter=mock_db)
        count = repo.get_override_count()

        assert count == 42

    def test_get_override_count_filters_active(self):
        """Test that count only includes active overrides"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        repo.get_override_count()

        # Check that the query filters by active=true
        call_args = mock_db.execute_query.call_args
        query = call_args[0][0]
        assert 'active = true' in query.lower()

    def test_get_override_count_error_handling(self):
        """Test error handling when count query fails"""
        mock_db = Mock()
        mock_db.execute_query.side_effect = Exception("Database error")

        repo = CategoryOverrideRepository(db_adapter=mock_db)
        count = repo.get_override_count()

        assert count == 0


class TestCategoryOverrideRepositoryIntegration:
    """Integration tests for full override workflow"""

    def test_full_override_workflow(self):
        """Test complete override workflow: save, load, apply, delete"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_id_1 = uuid4()
        asset_id_2 = uuid4()

        # Step 1: Save overrides
        mock_db.execute_query.return_value = None  # Success
        result1 = repo.save_override(asset_id_1, 'Network Device', 'Admin correction')
        result2 = repo.save_override(asset_id_2, 'Server', 'Data center asset')

        assert result1 is True
        assert result2 is True

        # Step 2: Load overrides
        mock_db.execute_query.return_value = [
            {'asset_id': asset_id_1, 'category': 'Network Device', 'subcategory': None},
            {'asset_id': asset_id_2, 'category': 'Server', 'subcategory': None}
        ]

        overrides = repo.get_all_overrides()
        assert len(overrides) == 2

        # Step 3: Apply overrides to assets
        data = pd.DataFrame({
            'id': [asset_id_1, asset_id_2, uuid4()],
            'category_best': ['workstation', 'laptop', 'workstation']
        })

        result_df = repo.apply_overrides(data, overrides)

        assert result_df[result_df['id'] == asset_id_1]['category_best'].iloc[0] == 'network device'
        assert result_df[result_df['id'] == asset_id_2]['category_best'].iloc[0] == 'server'

        # Step 4: Delete override
        mock_db.execute_query.return_value = None  # Success
        delete_result = repo.delete_override(asset_id_1)
        assert delete_result is True

    def test_override_does_not_affect_other_assets(self):
        """Test that overrides only affect specified assets"""
        mock_db = Mock()
        repo = CategoryOverrideRepository(db_adapter=mock_db)

        asset_with_override = uuid4()
        asset_without_override_1 = uuid4()
        asset_without_override_2 = uuid4()

        data = pd.DataFrame({
            'id': [asset_with_override, asset_without_override_1, asset_without_override_2],
            'category_best': ['workstation', 'laptop', 'server'],
            'category_prediction_score': [0.8, 0.9, 0.85]
        })

        overrides = {str(asset_with_override): {'category': 'network device', 'subcategory': None}}

        result = repo.apply_overrides(data, overrides)

        # Override applied
        assert result[result['id'] == asset_with_override]['category_best'].iloc[0] == 'network device'
        assert result[result['id'] == asset_with_override]['category_predicted'].iloc[0] == 'workstation'

        # Others unchanged
        assert result[result['id'] == asset_without_override_1]['category_best'].iloc[0] == 'laptop'
        assert result[result['id'] == asset_without_override_2]['category_best'].iloc[0] == 'server'

        # Scores preserved
        assert result[result['id'] == asset_without_override_1]['category_prediction_score'].iloc[0] == 0.9


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
