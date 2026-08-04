# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Pipeline must preserve per-source arrays for owner/location/user_field/
ipam_location end-to-end so multi-source disagreement reaches the DB.
Timestamps still get unwrapped from their 1-element aggregation lists.
"""

from unittest.mock import Mock

import pandas as pd

from src.modules.asset_generation.services.asset_generation_service import (
    AssetGenerationService,
)


def _make_service():
    mock_db = Mock()
    mock_db.execute_query.return_value = []
    mock_db.conn_manager.is_scope_active.return_value = False
    return AssetGenerationService(db_adapter=mock_db)


class TestConvertArraysToScalars:
    """`_convert_arrays_to_scalars` should only unwrap timestamps now."""

    def test_owner_array_preserved(self):
        svc = _make_service()
        df = pd.DataFrame({
            'id': ['a1'],
            'owner': [['alice', 'bob']],
            'location': [['SF', 'NYC']],
            'user_field': [['eng-team']],
            'ipam_location': [['10.0.0.0/24']],
        })

        result = svc._convert_arrays_to_scalars(df)

        assert result['owner'].iloc[0] == ['alice', 'bob']
        assert result['location'].iloc[0] == ['SF', 'NYC']
        assert result['user_field'].iloc[0] == ['eng-team']
        assert result['ipam_location'].iloc[0] == ['10.0.0.0/24']

    def test_empty_arrays_preserved_as_empty(self):
        svc = _make_service()
        df = pd.DataFrame({
            'id': ['a1'],
            'owner': [[]],
            'location': [[]],
        })

        result = svc._convert_arrays_to_scalars(df)

        assert result['owner'].iloc[0] == []
        assert result['location'].iloc[0] == []

    def test_timestamps_still_unwrapped(self):
        """`first_time`/`last_time` are stored as scalar timestamps in the DB,
        so we unwrap the 1-element lists aggregation produces."""
        svc = _make_service()
        df = pd.DataFrame({
            'id': ['a1'],
            'first_time': [['2025-06-01 10:00:00+00:00']],
            'last_time': [['2025-12-15 23:59:59+00:00']],
        })

        result = svc._convert_arrays_to_scalars(df)

        assert result['first_time'].iloc[0] == '2025-06-01 10:00:00+00:00'
        assert result['last_time'].iloc[0] == '2025-12-15 23:59:59+00:00'

    def test_empty_timestamp_list_becomes_none(self):
        svc = _make_service()
        df = pd.DataFrame({
            'id': ['a1'],
            'first_time': [[]],
            'last_time': [['']],
        })

        result = svc._convert_arrays_to_scalars(df)

        assert result['first_time'].iloc[0] is None
        assert result['last_time'].iloc[0] is None
