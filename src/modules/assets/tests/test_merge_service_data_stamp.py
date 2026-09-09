# SPDX-License-Identifier: AGPL-3.0-or-later
"""Merge and unmerge bump the asset-data version stamp (#1410).

A merge rewrites the primary's field values and removes the secondary; an
unmerge restores both. Either way the distinct values of the touched fields
change, so the Explorer field-values cache must invalidate. The bump sits
after the write (and the unified-values refresh) on both paths.

The DB is a MagicMock and the query-building internals are stubbed: the
assertion under test is the bump, not the merge mechanics (covered by the
blueprint/integration suites).
"""

from unittest.mock import MagicMock, patch

from src.modules.assets.services.merge_service import MergeService

P = "11111111-1111-4111-8111-111111111111"
S = "22222222-2222-4222-8222-222222222222"

_BUMP = "src.modules.assets.services.merge_service.asset_data_version.bump"


def _service():
    svc = MergeService(MagicMock())
    svc._refresh_unified_values = MagicMock()
    return svc


def test_merge_bumps_the_data_stamp():
    svc = _service()
    svc._fetch_asset = MagicMock(side_effect=[
        {"id": P, "merged_into": None},
        {"id": S, "merged_into": None},
    ])
    svc._resolve_fields = MagicMock(return_value=({"hostname": ["web-01"]}, {"hostname": {}}))
    svc._resolve_label_override = MagicMock(return_value=None)

    with patch(_BUMP) as mock_bump:
        out = svc.execute_merge(P, S, "prefer_primary", "admin")

    assert out["merged_asset"]["id"] == P
    mock_bump.assert_called_once_with()


def test_unmerge_bumps_the_data_stamp():
    svc = _service()
    svc._get_merge_record = MagicMock(return_value={
        "id": "m1", "field_resolutions": {"original_mappings": []},
    })
    svc._fetch_asset = MagicMock(side_effect=[
        {"id": P, "merged_into": None},
        {"id": S, "merged_into": P},
    ])
    svc._restore_primary_arrays = MagicMock()
    svc._restore_label_override = MagicMock()
    svc._restore_mappings = MagicMock()
    svc._delete_merge_record = MagicMock()

    with patch(_BUMP) as mock_bump:
        out = svc.unmerge(P, S, "admin")

    assert out["restored_asset_id"] == S
    mock_bump.assert_called_once_with()
