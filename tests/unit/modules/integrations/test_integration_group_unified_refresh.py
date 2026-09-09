# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration-group mutations re-materialize group-sourced unified fields
(#1405 part 3): membership changes what ``group:<g>:<attr>`` sources resolve
to, so every materializable group-sourced field gets a full-table refresh.

The hook lives in the service (not the blueprint) and builds a DB-direct
resolver: the 300s group-mapping cache is invalidated by the blueprint only
after the service returns, so a cache-primed resolver could bake — and stamp —
values from the pre-mutation membership.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.modules.integrations.services.integration_group_service import (
    IntegrationGroupService,
)

GID = "11111111-2222-3333-4444-555555555555"
INT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

_MODULE = "src.modules.integrations.services.integration_group_service"


@pytest.fixture
def svc():
    group_repo = MagicMock()
    int_repo = MagicMock()
    return IntegrationGroupService(group_repo, int_repo)


@pytest.fixture
def patched():
    defs = [{"slug": "site", "sources": ["group:s1:site"]}]
    with patch(f"{_MODULE}.refresh_for_sources") as refresh, \
         patch(f"{_MODULE}.build_field_resolver") as build, \
         patch(f"{_MODULE}.UnifiedFieldsRepository") as repo_cls:
        repo_cls.return_value.list_for_catalog.return_value = defs
        yield refresh, build, defs


def _assert_refreshed(svc, refresh, build, defs):
    build.assert_called_once_with(svc.group_repository.db)
    refresh.assert_called_once_with(
        svc.group_repository.db, build.return_value, defs, ("group:",))


def test_create_group_refreshes_group_sourced_fields(svc, patched):
    refresh, build, defs = patched
    svc.group_repository.create.return_value = {"id": GID, "name": "S1"}

    group = svc.create_group("S1", "edr")

    assert group["id"] == GID
    _assert_refreshed(svc, refresh, build, defs)


def test_update_group_refreshes_group_sourced_fields(svc, patched):
    refresh, build, defs = patched
    svc.group_repository.update.return_value = {"id": GID, "name": "S1-new"}

    svc.update_group(GID, "S1-new")

    _assert_refreshed(svc, refresh, build, defs)


def test_update_group_not_found_does_not_refresh(svc, patched):
    refresh, build, _ = patched
    svc.group_repository.update.return_value = None

    assert svc.update_group(GID, "x") is None
    refresh.assert_not_called()
    build.assert_not_called()


def test_delete_group_refreshes_group_sourced_fields(svc, patched):
    refresh, build, defs = patched
    svc.group_repository.get_by_id.return_value = {"id": GID}

    svc.delete_group(GID)

    _assert_refreshed(svc, refresh, build, defs)


def test_add_members_refreshes_group_sourced_fields(svc, patched):
    refresh, build, defs = patched
    svc.group_repository.get_by_id.return_value = {
        "id": GID, "integration_type": "edr"}
    svc.integration_repository.get_integration.return_value = MagicMock(
        integration_type="edr", group_id=None)

    svc.add_members(GID, [INT_ID])

    _assert_refreshed(svc, refresh, build, defs)


def test_remove_member_refreshes_group_sourced_fields(svc, patched):
    refresh, build, defs = patched
    svc.group_repository.get_by_id.return_value = {"id": GID}

    svc.remove_member(GID, INT_ID)

    _assert_refreshed(svc, refresh, build, defs)


def test_refresh_failure_does_not_fail_the_mutation(svc):
    svc.group_repository.create.return_value = {"id": GID, "name": "S1"}
    with patch(f"{_MODULE}.UnifiedFieldsRepository") as repo_cls:
        repo_cls.return_value.list_for_catalog.side_effect = RuntimeError("boom")

        group = svc.create_group("S1", "edr")   # must not raise

    assert group["id"] == GID
