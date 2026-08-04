# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for AssetLabelOverrideRepository."""
import uuid
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.modules.asset_generation.repositories.asset_label_override_repository import (
    AssetLabelOverrideRepository,
)


@pytest.fixture
def repo():
    return AssetLabelOverrideRepository(db_adapter=MagicMock())


class TestGetAllOverrides:
    def test_returns_dict_keyed_by_asset_id_string(self, repo):
        repo.db.execute_query.return_value = [
            {"asset_id": uuid.UUID("11111111-1111-1111-1111-111111111111"), "label": "Lab Printer"},
            {"asset_id": uuid.UUID("22222222-2222-2222-2222-222222222222"), "label": "CFO Laptop"},
        ]
        out = repo.get_all_overrides()
        assert out == {
            "11111111-1111-1111-1111-111111111111": "Lab Printer",
            "22222222-2222-2222-2222-222222222222": "CFO Laptop",
        }

    def test_only_loads_active_rows(self, repo):
        repo.db.execute_query.return_value = []
        repo.get_all_overrides()
        sql = repo.db.execute_query.call_args[0][0]
        assert "active = true" in sql

    def test_returns_empty_on_db_error(self, repo):
        repo.db.execute_query.side_effect = Exception("boom")
        assert repo.get_all_overrides() == {}


class TestApplyOverrides:
    def test_sets_asset_label_override_column(self, repo):
        df = pd.DataFrame({
            "id": [uuid.UUID("11111111-1111-1111-1111-111111111111"),
                   uuid.UUID("33333333-3333-3333-3333-333333333333")],
            "hostname": [["host-a"], ["host-b"]],
        })
        out = repo.apply_overrides(
            df,
            {"11111111-1111-1111-1111-111111111111": "Lab Printer"},
        )
        assert list(out["asset_label_override"]) == ["Lab Printer", None]

    def test_no_overrides_still_creates_column(self, repo):
        df = pd.DataFrame({"id": [uuid.UUID("11111111-1111-1111-1111-111111111111")]})
        out = repo.apply_overrides(df, {})
        assert "asset_label_override" in out.columns
        assert out["asset_label_override"].iloc[0] is None


class TestSaveOverride:
    def test_upsert_lowercases_nothing(self, repo):
        # Labels are user-facing — preserve case exactly.
        asset_id = uuid.uuid4()
        repo.save_override(asset_id, "Lab Printer 3", overridden_by="alice")
        sql, params = repo.db.execute_query.call_args[0][0], repo.db.execute_query.call_args[0][1]
        assert "INSERT INTO asset_label_overrides" in sql
        assert "ON CONFLICT (asset_id)" in sql
        assert params[1] == "Lab Printer 3"  # not lowercased

    def test_returns_true_on_success(self, repo):
        assert repo.save_override(uuid.uuid4(), "X", overridden_by="bob") is True

    def test_returns_false_on_db_error(self, repo):
        repo.db.execute_query.side_effect = Exception("boom")
        assert repo.save_override(uuid.uuid4(), "X", overridden_by="bob") is False


class TestClearOverride:
    def test_marks_inactive_via_update(self, repo):
        repo.clear_override(uuid.uuid4())
        sql = repo.db.execute_query.call_args[0][0]
        assert "UPDATE asset_label_overrides" in sql
        assert "active = false" in sql

    def test_returns_true_on_success(self, repo):
        assert repo.clear_override(uuid.uuid4()) is True


class TestGetOverride:
    def test_returns_row_dict_or_none(self, repo):
        repo.db.execute_query.return_value = [
            {"asset_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
             "label": "Lab Printer",
             "overridden_by": "alice",
             "updated_at": "2026-05-09T12:00:00+00:00"},
        ]
        out = repo.get_override("11111111-1111-1111-1111-111111111111")
        assert out["label"] == "Lab Printer"

    def test_returns_none_when_missing(self, repo):
        repo.db.execute_query.return_value = []
        assert repo.get_override(str(uuid.uuid4())) is None


class TestTransferOverride:
    def test_no_op_when_source_has_no_override(self, repo):
        # get_override returns no rows → transfer_override returns False
        repo.db.execute_query.return_value = []
        result = repo.transfer_override(uuid.uuid4(), uuid.uuid4())
        assert result is False
        # Only the SELECT inside get_override should have run
        assert repo.db.execute_query.call_count == 1

    def test_moves_row_from_to(self, repo):
        # SELECT (get_override) returns a row; INSERT (save_override) succeeds; UPDATE (clear_override) succeeds.
        from_id, to_id = uuid.uuid4(), uuid.uuid4()
        repo.db.execute_query.side_effect = [
            [{"asset_id": from_id, "label": "Lab Printer",
              "overridden_by": "alice", "updated_at": None}],  # get_override
            None,  # save_override INSERT
            None,  # clear_override UPDATE
        ]
        result = repo.transfer_override(from_id, to_id)
        assert result is True
        # 1 SELECT + 1 INSERT + 1 UPDATE
        assert repo.db.execute_query.call_count == 3
