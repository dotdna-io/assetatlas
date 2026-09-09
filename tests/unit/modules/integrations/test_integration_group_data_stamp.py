# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration-group mutations bump the asset-data version stamp (#1410).

Membership or name changes alter what every ``group:<g>:<attr>`` source
resolves to, so the distinct values of group-sourced fields change and the
Explorer field-values cache must invalidate. The bump lives in
``_refresh_group_sourced_values`` — the one hook every mutator already runs
(create/update/delete/add_members/remove_member) — placed after the refresh
block so a Redis error can never roll the mutation back.

Harness mirrors test_integration_group_unified_refresh.py.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.modules.integrations.services.integration_group_service import (
    IntegrationGroupService,
)

GID = "11111111-2222-3333-4444-555555555555"
INT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

_MODULE = "src.modules.integrations.services.integration_group_service"
_BUMP = f"{_MODULE}.asset_data_version.bump"


@pytest.fixture
def svc():
    group_repo = MagicMock()
    int_repo = MagicMock()
    return IntegrationGroupService(group_repo, int_repo)


@pytest.fixture
def patched():
    """The materialized refresh is unrelated to the stamp; stubbed."""
    with patch(f"{_MODULE}.refresh_for_sources"), \
         patch(f"{_MODULE}.build_field_resolver"), \
         patch(f"{_MODULE}.UnifiedFieldsRepository") as repo_cls:
        repo_cls.return_value.list_for_catalog.return_value = []
        yield


def test_create_group_bumps_the_data_stamp(svc, patched):
    svc.group_repository.create.return_value = {"id": GID, "name": "S1"}
    with patch(_BUMP) as mock_bump:
        svc.create_group("S1", "edr")
    mock_bump.assert_called_once_with()


def test_update_group_bumps_the_data_stamp(svc, patched):
    svc.group_repository.update.return_value = {"id": GID, "name": "S1-new"}
    with patch(_BUMP) as mock_bump:
        svc.update_group(GID, "S1-new")
    mock_bump.assert_called_once_with()


def test_update_group_not_found_does_not_bump(svc, patched):
    svc.group_repository.update.return_value = None
    with patch(_BUMP) as mock_bump:
        assert svc.update_group(GID, "x") is None
    mock_bump.assert_not_called()


def test_delete_group_bumps_the_data_stamp(svc, patched):
    svc.group_repository.get_by_id.return_value = {"id": GID}
    with patch(_BUMP) as mock_bump:
        svc.delete_group(GID)
    mock_bump.assert_called_once_with()


def test_add_members_bumps_the_data_stamp(svc, patched):
    svc.group_repository.get_by_id.return_value = {
        "id": GID, "integration_type": "edr"}
    svc.integration_repository.get_integration.return_value = MagicMock(
        integration_type="edr", group_id=None)
    with patch(_BUMP) as mock_bump:
        svc.add_members(GID, [INT_ID])
    mock_bump.assert_called_once_with()


def test_remove_member_bumps_the_data_stamp(svc, patched):
    svc.group_repository.get_by_id.return_value = {"id": GID}
    with patch(_BUMP) as mock_bump:
        svc.remove_member(GID, INT_ID)
    mock_bump.assert_called_once_with()


def test_refresh_failure_still_bumps_and_does_not_fail_the_mutation(svc):
    """The refresh is best-effort; the stamp bump must survive its failure
    (the values still changed — the refresh failure only defers the
    materialized rebuild to the next pipeline run)."""
    svc.group_repository.create.return_value = {"id": GID, "name": "S1"}
    with patch(f"{_MODULE}.UnifiedFieldsRepository") as repo_cls, \
         patch(_BUMP) as mock_bump:
        repo_cls.return_value.list_for_catalog.side_effect = RuntimeError("boom")
        group = svc.create_group("S1", "edr")   # must not raise
    assert group["id"] == GID
    mock_bump.assert_called_once_with()
