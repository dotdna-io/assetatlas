# SPDX-License-Identifier: AGPL-3.0-or-later
"""Manual tag add/remove bumps the field-catalog version stamp (#1413).

asset_manual_tags feeds the FieldCatalog's tag: entries (a UNION with
asset_tags_v2), so a manual add can introduce a brand-new tag field and a
remove can retire the last one. Every TagService mutation path bumps the
shared stamp so every worker's catalog rebuilds within its check interval;
a call that mutates nothing (empty id list) must not bump.
"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from src.modules.assets.services.tag_service import TagService

A1 = "11111111-1111-4111-8111-111111111111"
A2 = "22222222-2222-4222-8222-222222222222"

_BUMP = "src.modules.assets.services.tag_service.catalog_version.bump"
_REFRESH_ONE = "src.modules.assets.services.tag_service.refresh_tags_for_asset"
_REFRESH_MANY = "src.modules.assets.services.tag_service.refresh_tags_for_assets"


def _row(asset_id, inserted=True):
    # RealDictCursor hands back a real UUID for a uuid column, not a str.
    return {"asset_id": UUID(asset_id), "inserted": inserted}


def _service(rows=None):
    db = MagicMock()
    db.execute_query.return_value = rows if rows is not None else []
    return TagService(db), db


def test_add_tag_bumps_the_catalog_stamp():
    svc, _db = _service([{
        "id": "t1", "asset_id": A1, "tag_name": "prod",
        "tag_color": "#ef4444", "created_by": "admin", "created_at": None,
    }])
    with patch(_REFRESH_ONE), patch(_BUMP) as mock_bump:
        svc.add_tag(A1, "prod", "#ef4444", "admin")
    mock_bump.assert_called_once_with()


def test_add_tag_bulk_bumps_the_catalog_stamp():
    svc, _db = _service([_row(A1, True), _row(A2, False)])
    with patch(_REFRESH_MANY), patch(_BUMP) as mock_bump:
        svc.add_tag_bulk([A1, A2], "prod", "#ef4444", "admin")
    mock_bump.assert_called_once_with()


def test_add_tag_bulk_with_no_ids_changes_nothing_and_does_not_bump():
    svc, db = _service()
    with patch(_REFRESH_MANY), patch(_BUMP) as mock_bump:
        assert svc.add_tag_bulk([], "prod", "#ef4444", "admin") == {
            "tagged": 0, "created": 0,
        }
    db.execute_query.assert_not_called()
    mock_bump.assert_not_called()


def test_remove_tag_bumps_the_catalog_stamp():
    svc, _db = _service([{
        "id": "t1", "asset_id": A1, "tag_name": "prod", "tag_color": "#ef4444",
    }])
    with patch(_REFRESH_ONE), patch(_BUMP) as mock_bump:
        svc.remove_tag(A1, "t1")
    mock_bump.assert_called_once_with()


def test_remove_tag_bulk_bumps_the_catalog_stamp():
    svc, _db = _service([_row(A1), _row(A2)])
    with patch(_REFRESH_MANY), patch(_BUMP) as mock_bump:
        svc.remove_tag_bulk([A1, A2], "prod", "admin")
    mock_bump.assert_called_once_with()


def test_remove_tag_bulk_with_no_ids_changes_nothing_and_does_not_bump():
    svc, db = _service()
    with patch(_REFRESH_MANY), patch(_BUMP) as mock_bump:
        assert svc.remove_tag_bulk([], "prod", "admin") == {"removed": 0}
    db.execute_query.assert_not_called()
    mock_bump.assert_not_called()
