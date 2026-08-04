# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for EnrichmentRepository.

Verifies CRUD operations against a mocked db_adapter.
"""

from unittest.mock import MagicMock

import pytest

from src.modules.enrichment.repositories.enrichment_repository import (
    EnrichmentRepository,
)


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def repo(db):
    return EnrichmentRepository(db)


class TestCreateDataset:
    def test_calls_execute_query_with_insert(self, repo, db):
        db.execute_query.return_value = [{"id": "abc-123"}]

        result = repo.create_dataset(
            name="My Dataset",
            match_field="hostname",
            match_type="exact",
            target_field="owner",
            row_count=10,
            uploaded_by="admin",
        )

        assert result == "abc-123"
        sql = db.execute_query.call_args[0][0]
        assert "INSERT INTO enrichment_datasets" in sql
        assert "RETURNING id::text" in sql
        params = db.execute_query.call_args[0][1]
        assert params["name"] == "My Dataset"
        assert params["match_field"] == "hostname"
        assert params["match_type"] == "exact"
        assert params["target_field"] == "owner"
        assert params["row_count"] == 10
        assert params["uploaded_by"] == "admin"

    def test_uploaded_by_defaults_to_none(self, repo, db):
        db.execute_query.return_value = [{"id": "xyz-789"}]

        repo.create_dataset(
            name="D", match_field="ip", match_type="exact",
            target_field="location", row_count=5,
        )

        params = db.execute_query.call_args[0][1]
        assert params["uploaded_by"] is None


class TestInsertRows:
    def test_calls_execute_query_with_bulk_insert(self, repo, db):
        db.execute_query.return_value = None
        rows = [
            {"hostname": "srv1", "owner": "alice"},
            {"hostname": "srv2", "owner": "bob"},
        ]

        count = repo.insert_rows(
            "ds-id", rows,
            match_col="hostname", enrich_col="owner",
        )

        assert count == 2
        sql = db.execute_query.call_args[0][0]
        assert "INSERT INTO enrichment_rows" in sql
        assert "%(match_0)s" in sql
        assert "%(enrich_0)s" in sql
        assert "%(match_1)s" in sql
        assert "%(enrich_1)s" in sql
        params = db.execute_query.call_args[0][1]
        assert params["dataset_id"] == "ds-id"
        assert params["match_0"] == "srv1"
        assert params["enrich_0"] == "alice"
        assert params["match_1"] == "srv2"
        assert params["enrich_1"] == "bob"

    def test_empty_rows_returns_zero(self, repo, db):
        count = repo.insert_rows(
            "ds-id", [],
            match_col="hostname", enrich_col="owner",
        )

        assert count == 0
        db.execute_query.assert_not_called()


class TestListDatasets:
    def test_returns_results(self, repo, db):
        db.execute_query.return_value = [
            {"id": "1", "name": "D1", "status": "active"},
            {"id": "2", "name": "D2", "status": "active"},
        ]

        result = repo.list_datasets()

        assert len(result) == 2
        sql = db.execute_query.call_args[0][0]
        assert "status != %(excluded_status)s" in sql or "status !=" in sql
        assert "ORDER BY created_at DESC" in sql

    def test_returns_empty_list_when_no_results(self, repo, db):
        db.execute_query.return_value = []

        result = repo.list_datasets()

        assert result == []


class TestGetDataset:
    def test_returns_row_when_found(self, repo, db):
        db.execute_query.return_value = [
            {"id": "abc", "name": "D1", "status": "active"}
        ]

        result = repo.get_dataset("abc")

        assert result == {"id": "abc", "name": "D1", "status": "active"}
        params = db.execute_query.call_args[0][1]
        assert params["dataset_id"] == "abc"

    def test_returns_none_when_not_found(self, repo, db):
        db.execute_query.return_value = []

        result = repo.get_dataset("nonexistent")

        assert result is None

    def test_returns_none_when_query_returns_none(self, repo, db):
        db.execute_query.return_value = None

        result = repo.get_dataset("nonexistent")

        assert result is None


class TestGetRows:
    def test_returns_rows(self, repo, db):
        db.execute_query.return_value = [
            {"match_value": "srv1", "enrich_value": "alice"},
            {"match_value": "srv2", "enrich_value": "bob"},
        ]

        result = repo.get_rows("ds-id")

        assert len(result) == 2
        sql = db.execute_query.call_args[0][0]
        assert "enrichment_rows" in sql
        params = db.execute_query.call_args[0][1]
        assert params["dataset_id"] == "ds-id"

    def test_with_limit(self, repo, db):
        db.execute_query.return_value = [
            {"match_value": "a", "enrich_value": "b"},
        ]

        repo.get_rows("ds-id", limit=5)

        sql = db.execute_query.call_args[0][0]
        assert "LIMIT" in sql
        params = db.execute_query.call_args[0][1]
        assert params["limit"] == 5

    def test_returns_empty_list_when_no_results(self, repo, db):
        db.execute_query.return_value = []

        result = repo.get_rows("ds-id")

        assert result == []


class TestSoftDeleteDataset:
    def test_sets_status_to_deleted(self, repo, db):
        db.execute_query.return_value = [{"id": "abc"}]

        result = repo.soft_delete_dataset("abc")

        assert result is True
        sql = db.execute_query.call_args[0][0]
        assert "UPDATE enrichment_datasets" in sql
        assert "deleted" in sql.lower() or "%(status)s" in sql
        assert "RETURNING" in sql

    def test_returns_false_when_not_found(self, repo, db):
        db.execute_query.return_value = []

        result = repo.soft_delete_dataset("nonexistent")

        assert result is False


class TestUpdateDatasetStatus:
    def test_calls_update(self, repo, db):
        db.execute_query.return_value = [{"id": "abc"}]

        result = repo.update_dataset_status("abc", "applied")

        assert result is True
        sql = db.execute_query.call_args[0][0]
        assert "UPDATE enrichment_datasets" in sql
        assert "RETURNING" in sql
        params = db.execute_query.call_args[0][1]
        assert params["status"] == "applied"
        assert params["dataset_id"] == "abc"

    def test_returns_false_when_not_found(self, repo, db):
        db.execute_query.return_value = []

        result = repo.update_dataset_status("nonexistent", "applied")

        assert result is False


class TestGetActiveDatasets:
    def test_filters_by_active_status(self, repo, db):
        db.execute_query.return_value = [
            {"id": "1", "name": "D1", "status": "active"},
        ]

        result = repo.get_active_datasets()

        assert len(result) == 1
        sql = db.execute_query.call_args[0][0]
        assert "active" in sql.lower() or "%(status)s" in sql
        assert "ORDER BY created_at ASC" in sql

    def test_returns_empty_when_none_active(self, repo, db):
        db.execute_query.return_value = []

        result = repo.get_active_datasets()

        assert result == []


class TestUpdateMatchedCount:
    def test_calls_update(self, repo, db):
        db.execute_query.return_value = None

        repo.update_matched_count("abc", 42)

        sql = db.execute_query.call_args[0][0]
        assert "UPDATE enrichment_datasets" in sql
        assert "matched_count" in sql
        params = db.execute_query.call_args[0][1]
        assert params["matched_count"] == 42
        assert params["dataset_id"] == "abc"


class TestDeleteRowsForDataset:
    def test_calls_delete(self, repo, db):
        db.execute_query.return_value = None

        repo.delete_rows_for_dataset("ds-id")

        sql = db.execute_query.call_args[0][0]
        assert "DELETE FROM enrichment_rows" in sql
        params = db.execute_query.call_args[0][1]
        assert params["dataset_id"] == "ds-id"
