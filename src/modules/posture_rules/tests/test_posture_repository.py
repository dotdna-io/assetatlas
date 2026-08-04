# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for PostureRepository (mocked db_adapter)."""
from unittest.mock import MagicMock
import pytest

from src.modules.posture_rules.repositories.posture_repository import PostureRepository


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def repo(mock_db):
    return PostureRepository(db_adapter=mock_db)


class TestListCategories:
    def test_returns_ordered_rows(self, repo, mock_db):
        mock_db.execute_query.return_value = [
            {"id": "secured", "name": "Secured", "description": "desc",
             "icon": "M1", "applicable_categories": ["server"], "display_order": 1, "created_at": None},
        ]
        result = repo.list_categories()
        assert len(result) == 1
        assert result[0]["id"] == "secured"
        assert "ORDER BY display_order" in mock_db.execute_query.call_args[0][0]


class TestSubRuleCrud:
    def test_get_sub_rule_returns_none_if_missing(self, repo, mock_db):
        mock_db.execute_query.return_value = []
        assert repo.get_sub_rule("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa") is None

    def test_get_sub_rule_returns_row_when_found(self, repo, mock_db):
        mock_db.execute_query.return_value = [{"id": "uuid", "category_id": "x", "name": "n"}]
        out = repo.get_sub_rule("uuid")
        assert out["name"] == "n"

    def test_create_sub_rule_returns_new_row(self, repo, mock_db):
        mock_db.execute_query.return_value = [{"id": "new-uuid", "name": "x"}]
        out = repo.create_sub_rule(
            category_id="secured",
            name="x",
            description=None,
            enabled=True,
            target_filters=[{"field": "Category", "operator": "equals", "value": "server"}],
            compliance_criteria={"logic": "any", "conditions": []},
        )
        assert out["id"] == "new-uuid"

    def test_delete_sub_rule_returns_bool(self, repo, mock_db):
        mock_db.execute_query.return_value = [{"id": "uuid"}]
        assert repo.delete_sub_rule("uuid") is True
        mock_db.execute_query.return_value = []
        assert repo.delete_sub_rule("uuid") is False


class TestWriteAssetPostureRules:
    def test_writes_batch_update(self, repo, mock_db):
        repo.write_asset_posture_rules([
            ("11111111-1111-1111-1111-111111111111", {"secured": True}),
            ("22222222-2222-2222-2222-222222222222", {"secured": False}),
        ])
        sql = mock_db.execute_query.call_args[0][0]
        assert "UPDATE assets" in sql
        assert "FROM (VALUES" in sql


def test_mark_all_evaluated_runs_single_update(monkeypatch):
    """mark_all_evaluated should issue one UPDATE to posture_categories."""
    from src.modules.posture_rules.repositories.posture_repository import PostureRepository
    calls = []

    class FakeDB:
        def execute_query(self, sql, params, fetch=True):
            calls.append((sql, params))
            return []

    repo = PostureRepository(db_adapter=FakeDB())
    repo.mark_all_evaluated()

    assert len(calls) == 1
    sql, params = calls[0]
    assert "UPDATE posture_categories" in sql
    assert "last_evaluated_at" in sql
    assert "now()" in sql.lower()
    assert params == {}


class TestFetchAssetFieldValuesJsonbArray:
    """fetch_asset_field_values should join JSONB_ARRAY fields with ', '.

    Uses a mocked FieldResolver — the SQL expression comes from the resolver,
    not from any user-supplied string (CVE #455 fix).
    """

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        return db

    def _make_resolver(self, sql_expr: str, data_type):
        """Return a mock FieldResolver whose resolve() returns the given SQL and data type."""
        from src.modules.fields.types import FieldDataType  # noqa: F401
        resolved = MagicMock()
        resolved.where_expr.return_value = sql_expr
        resolved.data_type = data_type
        resolver = MagicMock()
        resolver.resolve.return_value = resolved
        return resolver

    def test_jsonb_array_uses_jsonb_array_elements_text(self, mock_db):
        """When the field is a JSONB_ARRAY, SQL must expand via jsonb_array_elements_text."""
        from src.modules.fields.models import StaticField
        from src.modules.fields.types import FieldDataType

        resolver = self._make_resolver(
            "extended_attributes->'intune'->'tags'", FieldDataType.JSONB_ARRAY,
        )
        mock_db.execute_query.return_value = [
            {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01", "v": "tagA, tagB"},
        ]
        repo = PostureRepository(db_adapter=mock_db)
        field_id = StaticField(kind="static", name="IntuneTagsField")

        out = repo.fetch_asset_field_values(
            ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"], field_id, resolver,
        )

        sql = mock_db.execute_query.call_args[0][0]
        assert "jsonb_array_elements_text" in sql
        assert "string_agg" in sql
        assert out == {"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01": "tagA, tagB"}

    def test_string_array_still_uses_array_to_string(self, mock_db):
        """STRING_ARRAY fields must continue to use array_to_string (regression guard)."""
        from src.modules.fields.models import StaticField
        from src.modules.fields.types import FieldDataType

        resolver = self._make_resolver("hostname", FieldDataType.STRING_ARRAY)
        mock_db.execute_query.return_value = []
        repo = PostureRepository(db_adapter=mock_db)
        field_id = StaticField(kind="static", name="Hostname")
        repo.fetch_asset_field_values(
            ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"], field_id, resolver,
        )

        sql = mock_db.execute_query.call_args[0][0]
        assert "array_to_string" in sql
        assert "jsonb_array_elements_text" not in sql

    def test_scalar_field_uses_raw_column(self, mock_db):
        """Scalar fields must continue to SELECT the column directly (regression guard)."""
        from src.modules.fields.models import StaticField
        from src.modules.fields.types import FieldDataType

        resolver = self._make_resolver("category", FieldDataType.STRING)
        mock_db.execute_query.return_value = []
        repo = PostureRepository(db_adapter=mock_db)
        field_id = StaticField(kind="static", name="Category")
        repo.fetch_asset_field_values(
            ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"], field_id, resolver,
        )

        sql = mock_db.execute_query.call_args[0][0]
        assert "array_to_string" not in sql
        assert "jsonb_array_elements_text" not in sql
        # Scalar column appears as `category AS v`
        assert "category AS v" in sql


class TestReorderSubRules:
    """PostureRepository.reorder_sub_rules should UPDATE display_order by position."""

    def test_reorder_issues_update(self, monkeypatch):
        from src.modules.posture_rules.repositories.posture_repository import PostureRepository
        calls = []

        class FakeDB:
            def execute_query(self, sql, params, fetch=True):
                calls.append((sql, params))
                # First call checks count of matching ids
                if "COUNT(*)" in sql:
                    return [{"n": 3}]
                return []

        repo = PostureRepository(db_adapter=FakeDB())
        ok = repo.reorder_sub_rules("secured", ["a", "b", "c"])

        assert ok is True
        assert len(calls) == 2
        assert "UNNEST" in calls[1][0]
        assert "GENERATE_SERIES" in calls[1][0]
        assert calls[1][1]["cid"] == "secured"
        assert calls[1][1]["ids"] == ["a", "b", "c"]
        assert calls[1][1]["count"] == 3

    def test_reorder_returns_false_if_ids_dont_match(self, monkeypatch):
        from src.modules.posture_rules.repositories.posture_repository import PostureRepository

        class FakeDB:
            def execute_query(self, sql, params, fetch=True):
                if "COUNT(*)" in sql:
                    return [{"n": 2}]  # only 2 of the 3 ids exist
                return []

        repo = PostureRepository(db_adapter=FakeDB())
        ok = repo.reorder_sub_rules("secured", ["a", "b", "c"])

        assert ok is False

    def test_reorder_empty_is_noop(self):
        from src.modules.posture_rules.repositories.posture_repository import PostureRepository
        calls = []

        class FakeDB:
            def execute_query(self, sql, params, fetch=True):
                calls.append((sql, params))
                return []

        repo = PostureRepository(db_adapter=FakeDB())
        ok = repo.reorder_sub_rules("secured", [])

        assert ok is True
        assert calls == []


class TestSubRuleHistory:
    def test_insert_sub_rule_history_runs_insert(self):
        from src.modules.posture_rules.repositories.posture_repository import PostureRepository
        calls = []

        class FakeDB:
            def execute_query(self, sql, params, fetch=True):
                calls.append((sql, params))
                return []

        repo = PostureRepository(db_adapter=FakeDB())
        repo.insert_sub_rule_history(
            sub_rule_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01",
            category_id="secured",
            action="create",
            before_state=None,
            after_state={"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01", "name": "x"},
            changed_by="user-1",
            changed_by_username="alice",
        )

        assert len(calls) == 1
        sql, params = calls[0]
        assert "INSERT INTO posture_sub_rule_history" in sql
        assert params["changed_by"] == "user-1"
        assert params["action"] == "create"
        assert params["before"] is None
        assert params["after"] is not None

    def test_latest_history_per_sub_rule_handles_empty_input(self):
        from src.modules.posture_rules.repositories.posture_repository import PostureRepository

        class FakeDB:
            def execute_query(self, sql, params, fetch=True):
                raise AssertionError("Should not query DB when sub_rule_ids is empty")

        repo = PostureRepository(db_adapter=FakeDB())
        assert repo.latest_history_per_sub_rule([]) == {}

    def test_latest_history_per_sub_rule_maps_rows(self):
        from src.modules.posture_rules.repositories.posture_repository import PostureRepository
        from datetime import datetime, timezone
        ts = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)

        class FakeDB:
            def execute_query(self, sql, params, fetch=True):
                return [
                    {"sub_rule_id": "sr-1", "action": "update", "changed_by": "u1",
                     "changed_by_username": "alice", "created_at": ts},
                ]

        repo = PostureRepository(db_adapter=FakeDB())
        out = repo.latest_history_per_sub_rule(["sr-1"])
        assert "sr-1" in out
        assert out["sr-1"]["action"] == "update"
        assert out["sr-1"]["changed_by_username"] == "alice"


class TestWriteTierSnapshot:
    def test_runs_insert_with_filtered_aggregates(self, repo, mock_db):
        repo.write_tier_snapshot()

        assert mock_db.execute_query.call_count == 1
        sql, params = mock_db.execute_query.call_args[0][:2]
        assert "INSERT INTO posture_tier_snapshots" in sql
        assert "secured_count" in sql
        assert "monitored_count" in sql
        assert "discovered_count" in sql
        assert "IS DISTINCT FROM 'true'" in sql
        assert sql.count("posture_rules -> 'secured'") >= 3
        assert sql.count("posture_rules -> 'monitored'") >= 2

    def test_uses_fetch_false(self, repo, mock_db):
        repo.write_tier_snapshot()
        kwargs = mock_db.execute_query.call_args.kwargs
        assert kwargs.get("fetch") is False


class TestWriteCoverageSnapshot:
    def test_runs_insert_per_control_and_asset_category(self, repo, mock_db):
        repo.write_coverage_snapshot()

        assert mock_db.execute_query.call_count == 1
        sql, params = mock_db.execute_query.call_args[0][:2]
        assert "INSERT INTO posture_coverage_snapshots" in sql
        assert "control_id" in sql and "asset_category" in sql
        assert "pass_count" in sql and "fail_count" in sql and "na_count" in sql
        # per-(control, asset_category): CROSS JOIN posture_categories x assets
        assert "FROM posture_categories" in sql
        assert "CROSS JOIN assets" in sql
        assert "GROUP BY c.id" in sql
        # lowercase category normalization, matching get_posture_tier_breakdown
        assert "LOWER(TRIM(a.category))" in sql
        assert "-> c.id ->> 'state' = 'true'" in sql

    def test_uses_fetch_false(self, repo, mock_db):
        repo.write_coverage_snapshot()
        kwargs = mock_db.execute_query.call_args.kwargs
        assert kwargs.get("fetch") is False


class TestCategoryStats:
    def test_coverage_is_compliant_over_full_category(self, repo, mock_db):
        # 'secured' posture category, 'server' asset category:
        # 8 pass, 2 fail, 5 n/a. The coverage bar must report COMPLIANT assets
        # (those matching the criteria = pass), not assets merely evaluated
        # (pass + fail). The denominator is the full category population.
        mock_db.execute_query.return_value = [
            {"category_id": "secured", "asset_category": "server",
             "pass": 8, "fail": 2, "n_a": 5},
        ]
        cov = repo.get_category_stats()["secured"]["by_asset_category"]["server"]
        # Would be 10 under the old "evaluated" semantics — that's the bug we fixed.
        assert cov["covered"] == 8
        assert cov["total"] == 15

    def test_rolls_up_multiple_asset_categories(self, repo, mock_db):
        mock_db.execute_query.return_value = [
            {"category_id": "secured", "asset_category": "server",
             "pass": 3, "fail": 1, "n_a": 0},
            {"category_id": "secured", "asset_category": "mobile",
             "pass": 4, "fail": 0, "n_a": 2},
        ]
        stats = repo.get_category_stats()["secured"]
        assert stats["pass"] == 7
        assert stats["fail"] == 1
        assert stats["n_a"] == 2
        assert stats["by_asset_category"]["server"] == {"covered": 3, "total": 4}
        assert stats["by_asset_category"]["mobile"] == {"covered": 4, "total": 6}
