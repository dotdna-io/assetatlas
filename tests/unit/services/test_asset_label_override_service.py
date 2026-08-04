# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for AssetLabelOverrideService."""
import uuid
from unittest.mock import MagicMock

import pytest

from src.modules.assets.services.asset_label_override_service import (
    AssetLabelOverrideService,
)


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def repo():
    return MagicMock()


@pytest.fixture
def service(db, repo):
    return AssetLabelOverrideService(db_adapter=db, override_repo=repo)


@pytest.fixture
def asset_id():
    return str(uuid.uuid4())


class TestSetOverride:
    def test_rejects_empty_label(self, service, asset_id):
        with pytest.raises(ValueError):
            service.set_override(asset_id, "   ", user="alice")

    def test_rejects_missing_asset(self, service, asset_id, db):
        db.execute_query.return_value = []  # no asset row
        with pytest.raises(LookupError):
            service.set_override(asset_id, "Lab Printer", user="alice")

    def test_persists_to_assets_and_overrides_table(self, service, asset_id, db, repo):
        db.execute_query.side_effect = [
            [{"id": asset_id, "asset_label_override": None}],  # _get_current_asset
            None,  # UPDATE assets
        ]
        repo.save_override.return_value = True

        out = service.set_override(asset_id, "Lab Printer", user="alice")

        assert out["label"] == "Lab Printer"
        assert out["previous_label"] is None
        assert out["overridden_by"] == "alice"
        repo.save_override.assert_called_once_with(asset_id, "Lab Printer", overridden_by="alice")
        assert db.bulk_write_chronicle_events.called

    def test_runtime_error_when_repo_save_fails(self, service, asset_id, db, repo):
        db.execute_query.return_value = [{"id": asset_id, "asset_label_override": None}]
        repo.save_override.return_value = False

        with pytest.raises(RuntimeError):
            service.set_override(asset_id, "Lab Printer", user="alice")

        # Assets column UPDATE never ran (only the SELECT inside _get_current_asset)
        # AND no chronicle event was written.
        assert db.execute_query.call_count == 1
        assert not db.bulk_write_chronicle_events.called

    def test_chronicle_event_includes_before_after(self, service, asset_id, db, repo):
        db.execute_query.side_effect = [
            [{"id": asset_id, "asset_label_override": "Old"}],
            None,
        ]
        repo.save_override.return_value = True

        service.set_override(asset_id, "New", user="alice")

        events = db.bulk_write_chronicle_events.call_args[0][0]
        assert events[0]["event_type"] == "manual_label_override_set"
        assert events[0]["attribute_changes"]["asset_label_override"] == {"old": "Old", "new": "New"}


class TestClearOverride:
    def test_clears_assets_column_and_marks_inactive(self, service, asset_id, db, repo):
        db.execute_query.side_effect = [
            [{"id": asset_id, "asset_label_override": "Lab Printer"}],
            None,  # UPDATE assets
        ]
        repo.clear_override.return_value = True

        out = service.clear_override(asset_id, user="alice")

        assert out["previous_label"] == "Lab Printer"
        assert out["label"] is None
        repo.clear_override.assert_called_once()
        events = db.bulk_write_chronicle_events.call_args[0][0]
        assert events[0]["event_type"] == "manual_label_override_cleared"

    def test_clear_with_no_existing_override_is_idempotent(self, service, asset_id, db, repo):
        db.execute_query.side_effect = [
            [{"id": asset_id, "asset_label_override": None}],
            None,
        ]
        repo.clear_override.return_value = True

        out = service.clear_override(asset_id, user="alice")
        assert out["previous_label"] is None
        # Still writes chronicle for audit consistency
        assert db.bulk_write_chronicle_events.called

    def test_rejects_missing_asset(self, service, asset_id, db):
        db.execute_query.return_value = []
        with pytest.raises(LookupError):
            service.clear_override(asset_id, user="alice")

    def test_runtime_error_when_repo_clear_fails(self, service, asset_id, db, repo):
        # Asset exists but the survives-truncate row write fails.
        db.execute_query.return_value = [{"id": asset_id, "asset_label_override": "Lab Printer"}]
        repo.clear_override.return_value = False

        with pytest.raises(RuntimeError):
            service.clear_override(asset_id, user="alice")

        # Assets column UPDATE never ran (only the SELECT inside _get_current_asset)
        # AND no chronicle event was written.
        assert db.execute_query.call_count == 1
        assert not db.bulk_write_chronicle_events.called


class TestGetOverride:
    def test_delegates_to_repo(self, service, asset_id, repo):
        repo.get_override.return_value = {"label": "Lab Printer", "overridden_by": "alice"}
        assert service.get_override(asset_id)["label"] == "Lab Printer"
        repo.get_override.assert_called_once_with(asset_id)
