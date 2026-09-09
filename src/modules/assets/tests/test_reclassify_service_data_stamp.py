# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reclassify paths bump the asset-data version stamp (#1410).

A reclassify (single or bulk) changes category/subcategory values, and
clearing an override restores them — all three paths change the distinct
values the Explorer field-values cache serves, so each bumps the stamp after
its write. A bulk clear that matched no overrides writes nothing and must
not bump.

Harness mirrors test_reclassify_override_clear_keys_on_overrides_table.py:
the service is built via __new__ with MagicMock collaborators.
"""

from unittest.mock import MagicMock, patch

from src.modules.assets.services.reclassify_service import ReclassifyService

A1 = "11111111-1111-4111-8111-111111111111"
A2 = "22222222-2222-4222-8222-222222222222"

_BUMP = "src.modules.assets.services.reclassify_service.asset_data_version.bump"


def _svc(rows=(), active_ids=()):
    svc = ReclassifyService.__new__(ReclassifyService)
    svc.db = MagicMock()
    svc.override_repo = MagicMock()
    svc.custom_subcategory_repo = MagicMock()
    svc._catalog = [{"name": "network", "subcategories": ["switch"]}]
    svc.db.execute_query.return_value = [dict(r) for r in rows]
    svc.override_repo.get_active_override_ids.return_value = set(active_ids)
    svc._refresh_unified_values = MagicMock()
    return svc


def _overridden_row(asset_id):
    return {
        "id": asset_id,
        "category": "network",
        "subcategory": "switch",
        "category_predicted": "server",
        "subcategory_predicted": "linux",
        "category_is_overridden": True,
    }


def test_reclassify_single_bumps_the_data_stamp():
    svc = _svc(rows=[{
        "id": A1, "category": "server", "subcategory": "linux",
        "category_predicted": "server", "subcategory_predicted": "linux",
    }])
    with patch(_BUMP) as mock_bump:
        out = svc.reclassify_asset(A1, "network", "switch", "admin")
    assert out["category"] == "network"
    mock_bump.assert_called_once_with()


def test_reclassify_bulk_bumps_the_data_stamp():
    svc = _svc(rows=[_overridden_row(A1), _overridden_row(A2)])
    with patch(_BUMP) as mock_bump:
        out = svc.reclassify_bulk([A1, A2], "network", "switch", "admin")
    assert out == {"updated": 2}
    mock_bump.assert_called_once_with()


def test_clear_overrides_bumps_the_data_stamp_when_it_cleared():
    svc = _svc(rows=[_overridden_row(A1)], active_ids=(A1,))
    with patch(_BUMP) as mock_bump:
        out = svc.clear_overrides_bulk([A1], "admin")
    assert out == {"cleared": 1}
    mock_bump.assert_called_once_with()


def test_clear_overrides_with_no_overrides_writes_nothing_and_does_not_bump():
    svc = _svc(rows=[_overridden_row(A1)], active_ids=())
    with patch(_BUMP) as mock_bump:
        out = svc.clear_overrides_bulk([A1], "admin")
    assert out == {"cleared": 0}
    mock_bump.assert_not_called()
