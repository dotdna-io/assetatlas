# SPDX-License-Identifier: AGPL-3.0-or-later
"""invalidate_group_mapping_cache bumps the field-catalog version stamp (#1413).

Integration-group membership feeds the FieldCatalog's integration_group
entries, so every group mutation (the five call sites in
blueprints/integrations/integration_groups.py) must propagate cross-worker:
the helper now bumps the shared stamp alongside clearing the group-mapping
cache key. The bump must survive a cache-backend failure — a down cache must
not take the stamp with it.
"""

from unittest.mock import patch

from src.api.utils.caching import invalidate_group_mapping_cache

_CACHE = "src.api.utils.caching.cache"
_BUMP = "src.api.utils.caching.catalog_version.bump"


def test_group_mapping_invalidation_bumps_the_catalog_stamp():
    with patch(_CACHE) as mock_cache, patch(_BUMP) as mock_bump:
        invalidate_group_mapping_cache()
    mock_cache.delete.assert_called_once_with("integration_group_mapping")
    mock_bump.assert_called_once_with()


def test_bump_survives_a_cache_delete_failure():
    with patch(_CACHE) as mock_cache, patch(_BUMP) as mock_bump:
        mock_cache.delete.side_effect = RuntimeError("cache backend down")
        invalidate_group_mapping_cache()  # must not raise
    mock_bump.assert_called_once_with()
