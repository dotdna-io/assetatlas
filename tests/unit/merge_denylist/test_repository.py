# SPDX-License-Identifier: AGPL-3.0-or-later
"""MergeDenylistRepository against a fake adapter (#991).

tests/integration/merge_denylist/test_repository.py covers the same repo against
a live PostgreSQL, but it's marked `integration` and skipped by default — so the
two things here that break loudly in production and silently in CI get a unit
test: the ON CONFLICT target and what load_enabled_components() hands the seams.
"""

from pathlib import Path

from src.modules.merge_denylist.matcher import DenylistMatcher
from src.modules.merge_denylist.repository import MergeDenylistRepository

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src/database/migrations/135_add_denylist_match_types.sql"
)


class FakeDb:
    """Captures SQL and returns canned rows, the way execute_query would."""

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.queries = []

    def execute_query(self, sql, params=None):
        self.queries.append((sql, params))
        return self.rows


def _row(**overrides):
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "identifier_type": "mac",
        "match_type": "prefix",
        "identifier_value": "bc:24:11:*",
        "raw_value": "BC-24-11-*",
        "reason": None,
        "created_by": "admin",
        "enabled": True,
        "created_at": None,
    }
    row.update(overrides)
    return row


def test_create_writes_match_type_and_bounds():
    db = FakeDb([_row(match_type="range", range_start=1, range_end=2)])
    repo = MergeDenylistRepository(db)
    row = repo.create(
        identifier_type="mac",
        match_type="range",
        identifier_value="bc:24:11:00:00:00-bc:24:11:ff:ff:ff",
        raw_value="BC:24:11:00:00:00 - BC:24:11:FF:FF:FF",
        reason=None,
        created_by="admin",
        range_start=0xBC24_1100_0000,
        range_end=0xBC24_11FF_FFFF,
    )
    sql, params = db.queries[0]
    assert params["mtype"] == "range"
    assert params["rstart"] == 0xBC24_1100_0000
    assert params["rend"] == 0xBC24_11FF_FFFF
    assert row.match_type == "range"


def test_create_defaults_to_an_exact_entry_with_no_bounds():
    # The pre-#991 call shape (asset split, the integration suite) must still
    # produce exactly what it produced before.
    db = FakeDb([_row(match_type="exact")])
    repo = MergeDenylistRepository(db)
    repo.create(
        identifier_type="hostname",
        identifier_value="web01",
        raw_value="web01",
        reason=None,
        created_by="admin",
    )
    _, params = db.queries[0]
    assert params["mtype"] == "exact"
    assert params["rstart"] is None and params["rend"] is None


def test_idempotent_conflict_target_matches_the_unique_constraint():
    """Postgres infers ON CONFLICT from the column list, so if this drifts from
    merge_denylist_unique every idempotent insert raises — i.e. every asset
    split — and only the skipped-by-default integration suite would notice."""
    db = FakeDb([_row()])
    repo = MergeDenylistRepository(db)
    repo.create_idempotent(
        identifier_type="hostname",
        identifier_value="web01",
        raw_value="web01",
        reason=None,
        created_by="admin",
    )
    sql, _ = db.queries[0]
    conflict_target = "(identifier_type, identifier_value, match_type)"
    assert f"ON CONFLICT {conflict_target} DO NOTHING" in sql
    assert f"UNIQUE {conflict_target}" in _MIGRATION.read_text()


def test_load_enabled_components_returns_a_working_matcher():
    db = FakeDb([
        _row(identifier_type="hostname", match_type="exact",
             identifier_value="web01"),
        _row(identifier_type="mac", match_type="prefix",
             identifier_value="bc:24:11:*"),
    ])
    matcher = MergeDenylistRepository(db).load_enabled_components()
    assert isinstance(matcher, DenylistMatcher)
    assert "web01" in matcher
    assert "bc:24:11:22:33:44" in matcher
    assert "db01" not in matcher

    # It must select the columns the matcher builds from, and only enabled rows.
    sql, _ = db.queries[0]
    for column in ("identifier_type", "match_type", "identifier_value",
                   "range_start", "range_end"):
        assert column in sql
    assert "WHERE enabled" in sql


def test_load_enabled_components_on_an_empty_table():
    matcher = MergeDenylistRepository(FakeDb([])).load_enabled_components()
    assert "web01" not in matcher
