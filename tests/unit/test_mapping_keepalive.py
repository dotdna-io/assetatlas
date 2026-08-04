# SPDX-License-Identifier: AGPL-3.0-or-later
"""Carried-forward assets keep their identifier mappings alive (#795)."""

from unittest.mock import MagicMock

from src.modules.asset_generation.repositories.mapping_repository import MappingRepository


def test_refresh_noop_on_empty():
    repo = MappingRepository(MagicMock())
    assert repo.refresh_mappings_for_assets([]) == 0
    repo.db.touch_asset_mappings.assert_not_called()


def test_refresh_delegates_to_adapter():
    db = MagicMock()
    db.touch_asset_mappings.return_value = 7
    repo = MappingRepository(db)
    result = repo.refresh_mappings_for_assets(['id-a', 'id-b'])
    assert result == 7
    db.touch_asset_mappings.assert_called_once_with(['id-a', 'id-b'])
