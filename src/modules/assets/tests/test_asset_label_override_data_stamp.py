# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asset-label override set/clear bumps the asset-data version stamp (#1410).

The override writes assets.asset_label_override, which the asset_label
virtual field reads — so both set and clear change that field's distinct
values and the Explorer field-values cache must invalidate. The bump runs
after the write, inside outside_request_scope, so it fires only once the
writer's transaction has committed: a concurrent /values request can never
read the new stamp against the pre-write snapshot and cache stale values
under the new key.
"""

from unittest.mock import MagicMock, patch

from src.modules.assets.services.asset_label_override_service import (
    AssetLabelOverrideService,
)

A1 = "11111111-1111-4111-8111-111111111111"

_BUMP = ("src.modules.assets.services.asset_label_override_service"
         ".asset_data_version.bump")


def _service(asset_row=None):
    db = MagicMock()
    db.execute_query.return_value = [asset_row] if asset_row else []
    repo = MagicMock()
    repo.save_override.return_value = True
    repo.clear_override.return_value = True
    return AssetLabelOverrideService(db, repo)


def test_set_override_bumps_the_data_stamp():
    svc = _service({"id": A1, "asset_label_override": None})
    with patch(_BUMP) as mock_bump:
        out = svc.set_override(A1, "finance-db", "admin")
    assert out["label"] == "finance-db"
    mock_bump.assert_called_once_with()


def test_clear_override_bumps_the_data_stamp():
    svc = _service({"id": A1, "asset_label_override": "finance-db"})
    with patch(_BUMP) as mock_bump:
        out = svc.clear_override(A1, "admin")
    assert out["label"] is None
    mock_bump.assert_called_once_with()


def test_bump_fires_after_the_request_scope_commits():
    """end_scope (the commit of the override write) must precede the bump."""
    svc = _service({"id": A1, "asset_label_override": None})
    order = MagicMock()
    order.attach_mock(svc.db.conn_manager.end_scope, "end_scope")
    with patch(_BUMP) as mock_bump:
        order.attach_mock(mock_bump, "bump")
        svc.set_override(A1, "finance-db", "admin")
    names = [c[0] for c in order.mock_calls]
    assert names == ["end_scope", "bump"]
