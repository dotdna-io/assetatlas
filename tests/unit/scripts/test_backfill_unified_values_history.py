# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/backfill_unified_values_history.py (#1405 part 4).

The script is a thin driver over refresh_unified_values; these tests run the
REAL writer against a MagicMock adapter so the generated SQL is asserted, not
assumed: every write must target assets_history, none may stamp
materialized_at, discovery chunking must follow --batch-size, statement size
must follow --assets-per-statement, and --dry-run must not issue any UPDATE
at all. build_field_resolver is replaced with a fake so the merge SQL is the
real expression — without the injection the real FieldCatalog's discovery SQL
hits the mock's AssertionError (swallowed by the catalog), every source is
unresolvable, and the UPDATE degrades to jsonb_build_object('lentity', NULL).
"""

import importlib.util
import re
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.modules.fields.types import FieldDataType
from src.modules.fields.unified import materialize

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "backfill_unified_values_history",
    ROOT / "scripts" / "backfill_unified_values_history.py")
backfill = importlib.util.module_from_spec(SPEC)
sys.modules.setdefault(SPEC.name, backfill)
SPEC.loader.exec_module(backfill)

RUN1 = str(uuid.uuid4())
RUN2 = str(uuid.uuid4())
ASSET1 = str(uuid.uuid4())
ASSET2 = str(uuid.uuid4())
ASSET3 = str(uuid.uuid4())

DEF_ROW = {
    "slug": "lentity",
    "name": "Legal Entity",
    "function": "coalesce",
    "sources": ["ext:A:x", "ext:B:x"],
    "data_type": "string",
    "materialized_at": None,
}


class _FakeResolvedField:
    """What build_merge_sql needs from resolver.resolve(): a row-local text
    extended-attribute expression."""
    sql_kind = "jsonb_path"
    data_type = FieldDataType.STRING

    def where_expr(self):
        return "extended_attributes->'A'->>'x'"

    def is_native_array(self):
        return False


class _FakeResolver:
    def catalog(self):
        return MagicMock()  # only invalidate() is called

    def resolve(self, field_id):
        return _FakeResolvedField()


@pytest.fixture(autouse=True)
def _reset_probe_cache(monkeypatch):
    """No cross-test leaks of the process-wide information_schema probe cache."""
    monkeypatch.setattr(materialize, "_COLUMN_PRESENT", {})
    monkeypatch.setattr(materialize, "_MIGRATION_149_WARNED", False)


@pytest.fixture(autouse=True)
def _inject_fake_resolver(monkeypatch):
    """Resolve sources through _FakeResolver, not the real FieldCatalog — see
    the module docstring for the hollow-UPDATE failure this prevents."""
    monkeypatch.setattr(
        backfill, "build_field_resolver", lambda db: _FakeResolver())


def _adapter(chunk_asset_ids, *, migrated=True, null_run_rows=0):
    """(adapter, sql_log). `chunk_asset_ids` is the list of id-lists returned
    per chunk query, in order. The probe answers `migrated`."""
    adapter = MagicMock()
    sql_log = []
    chunk_calls = iter(chunk_asset_ids)

    def _route(sql, params=None):
        sql_log.append(sql)
        if "information_schema.columns" in sql:
            return [{"?column?": 1}] if migrated else []
        if "FROM unified_fields" in sql:
            return [dict(DEF_ROW)]
        if "SELECT DISTINCT generation_run_id" in sql:
            return [{"generation_run_id": RUN1}, {"generation_run_id": RUN2}]
        if "SELECT DISTINCT id FROM assets_history" in sql:
            return [{"id": a} for a in next(chunk_calls)]
        if "generation_run_id IS NULL" in sql:
            # _has_null_run_rows (the NULL chunk's id query matched above).
            return [{"n": null_run_rows}]
        if "SELECT count(*) AS n FROM assets_history" in sql:
            return [{"n": 2}]
        if "WITH upd AS" in sql:
            return [{"n": 2}]
        raise AssertionError(f"unexpected SQL: {sql}")

    adapter.execute_query = MagicMock(side_effect=_route)
    return adapter, sql_log


def _run(db, **kwargs):
    kwargs.setdefault("out", lambda *_: None)
    return backfill.run(db, **kwargs)


# ── Refusals ──────────────────────────────────────────────────────────────────

def test_refuses_when_migration_149_is_not_applied():
    db, sql_log = _adapter([[ASSET1]], migrated=False)
    assert _run(db, dry_run=False) == 1
    assert not any("UPDATE" in sql for sql in sql_log)


def test_unknown_slug_is_refused():
    db, sql_log = _adapter([[ASSET1]])
    assert _run(db, slugs=["no_such_slug"], dry_run=False) == 1
    assert not any("UPDATE" in sql for sql in sql_log)


# ── Dry run ───────────────────────────────────────────────────────────────────

def test_dry_run_executes_no_update():
    db, sql_log = _adapter([[ASSET1], [ASSET2]])
    assert _run(db, dry_run=True) == 0
    assert sql_log  # probes + discovery SELECTs did run
    assert not any("UPDATE" in sql for sql in sql_log)


def test_dry_run_is_the_default():
    args = backfill._parse_args([])
    assert args.dry_run is True
    args = backfill._parse_args(["--yes"])
    assert args.dry_run is False


# ── Execute ───────────────────────────────────────────────────────────────────

def test_execute_updates_assets_history_chunked_per_run():
    db, sql_log = _adapter([[ASSET1], [ASSET2]])
    assert _run(db, dry_run=False, batch_size=1) == 0

    updates = [sql for sql in sql_log if "WITH upd AS" in sql]
    # two generation_run_ids, batch-size 1 → one UPDATE per chunk
    assert len(updates) == 2
    for sql in updates:
        assert "UPDATE assets_history" in sql
        assert "SET unified_values" in sql
        # the REAL merge expression from the injected resolver, not the hollow
        # jsonb_build_object('lentity', NULL) an unresolvable source yields
        assert "extended_attributes->'A'->>'x'" in sql
        assert "jsonb_build_object('lentity', NULL)" not in sql
    # never stamps — not via refresh (stamp=False) nor any other path
    assert not any("SET materialized_at" in sql for sql in sql_log)
    assert not any("UPDATE unified_fields" in sql for sql in sql_log)


def test_execute_respects_batch_size():
    db, sql_log = _adapter([[ASSET1, ASSET2]])  # one chunk covering both runs
    assert _run(db, dry_run=False, batch_size=100) == 0
    updates = [sql for sql in sql_log if "WITH upd AS" in sql]
    assert len(updates) == 1


def test_an_asset_seen_in_an_earlier_chunk_is_not_refreshed_again():
    # RUN2's chunk re-reports ASSET1 (the per-asset UPDATE already rewrote all
    # of its snapshots); only ASSET2 may appear in the second UPDATE's params.
    db, _ = _adapter([[ASSET1], [ASSET1, ASSET2]])
    assert _run(db, dry_run=False, batch_size=1) == 0

    update_params = [
        call[0][1]["ids"] for call in db.execute_query.call_args_list
        if "WITH upd AS" in call[0][0]
    ]
    assert update_params == [[ASSET1], [ASSET2]]


def test_rows_without_a_run_id_form_their_own_final_chunk():
    # null_run_rows=1 → a third chunk (None) is appended after the run chunks
    # and discovers its ids via the generation_run_id IS NULL query.
    db, sql_log = _adapter([[ASSET1], [ASSET2], [ASSET3]], null_run_rows=1)
    messages = []
    assert backfill.run(db, dry_run=False, batch_size=1,
                        out=messages.append) == 0

    update_params = [
        call[0][1]["ids"] for call in db.execute_query.call_args_list
        if "WITH upd AS" in call[0][0]
    ]
    assert update_params == [[ASSET1], [ASSET2], [ASSET3]]
    assert any("SELECT DISTINCT id FROM assets_history" in sql
               and "generation_run_id IS NULL" in sql for sql in sql_log)
    assert any("unlabelled rows" in m for m in messages)


def test_a_chunk_is_split_into_statements_bounded_by_assets_per_statement():
    # One discovery chunk with 3 new ids, bound 2 → two UPDATEs of 2 and 1
    # ids; run-chunking alone leaves the statement size unbounded.
    db, _ = _adapter([[ASSET1, ASSET2, ASSET3]])
    assert _run(db, dry_run=False, batch_size=100, assets_per_statement=2) == 0

    update_params = [
        call[0][1]["ids"] for call in db.execute_query.call_args_list
        if "WITH upd AS" in call[0][0]
    ]
    assert [len(ids) for ids in update_params] == [2, 1]
    assert sorted(i for ids in update_params for i in ids) == sorted(
        [ASSET1, ASSET2, ASSET3])


def test_dry_run_and_execute_report_the_same_counts():
    def _counts(db, dry_run):
        messages = []
        assert backfill.run(db, dry_run=dry_run, batch_size=100,
                            assets_per_statement=2, out=messages.append) == 0
        return [(m.group(1), m.group(2)) for m in (re.search(
            r"(?:would refresh|refreshed) (\d+) history row\(s\) "
            r"across (\d+) asset\(s\)", line) for line in messages) if m]

    dry_db, _ = _adapter([[ASSET1, ASSET2, ASSET3]])
    exec_db, _ = _adapter([[ASSET1, ASSET2, ASSET3]])
    assert (_counts(dry_db, True) == _counts(exec_db, False)
            == [("2", "2"), ("2", "1")])


def test_nothing_to_backfill_is_a_clean_noop():
    db, sql_log = _adapter([[ASSET1]])
    db.execute_query.side_effect = None
    # Re-route: no NULL rows anywhere → empty run-id list, empty NULL chunk.
    def _route(sql, params=None):
        if "information_schema.columns" in sql:
            return [{"?column?": 1}]
        if "FROM unified_fields" in sql:
            return [dict(DEF_ROW)]
        if "SELECT DISTINCT generation_run_id" in sql:
            return []
        if "generation_run_id IS NULL" in sql:
            return [{"n": 0}]
        raise AssertionError(f"unexpected SQL: {sql}")
    db.execute_query.side_effect = _route
    assert _run(db, dry_run=False) == 0


def test_main_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert backfill.main([]) == 1
