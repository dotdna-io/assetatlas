# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reclassify paths recompute stored unified-field values for the touched
assets (#1405 part 3): category/subcategory are unified-field inputs. The
refresh is per-asset (never stamps) and best-effort (never fails the
reclassify); it runs outside the request scope — the writes commit first —
so a failed refresh can neither roll the writes back nor poison their
transaction.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from psycopg2 import extensions

from src.database.postgres_adapter import PostgreSQLAdapter
from src.database.postgres_connection import PostgreSQLConnectionManager
from src.modules.assets.services.reclassify_service import ReclassifyService
from src.modules.fields.types import FieldDataType

A1 = '11111111-1111-4111-8111-111111111111'
A2 = '22222222-2222-4222-8222-222222222222'

_MODULE = "src.modules.assets.services.reclassify_service"
DEFS = [{"slug": "f1", "sources": ["static:Category"]}]


def _svc():
    svc = ReclassifyService.__new__(ReclassifyService)   # skip catalog load
    svc.db = MagicMock()
    svc.override_repo = MagicMock()
    svc._catalog = [{"name": "network", "subcategories": ["switch"]}]
    svc.custom_subcategory_repo = MagicMock()
    svc.custom_subcategory_repo.get_for_category.return_value = []
    return svc


@pytest.fixture
def patched():
    with patch(f"{_MODULE}.refresh_unified_values") as refresh, \
         patch(f"{_MODULE}.build_field_resolver") as build, \
         patch(f"{_MODULE}.UnifiedFieldsRepository") as repo_cls:
        repo_cls.return_value.list_for_catalog.return_value = DEFS
        yield refresh, build, repo_cls


def test_single_reclassify_refreshes_the_asset(patched):
    refresh, build, _ = patched
    svc = _svc()
    svc.db.execute_query.return_value = [{
        'id': A1, 'category': 'server', 'subcategory': 'linux',
        'manufacturer': '', 'model': '', 'os_normalized': '',
        'mac_vendor': '', 'vendor_category': '',
    }]

    out = svc.reclassify_asset(A1, 'network', 'switch', 'admin')

    assert out["id"] == A1
    build.assert_called_once_with(svc.db)
    refresh.assert_called_once_with(
        svc.db, build.return_value, DEFS, asset_ids=[A1])


def test_bulk_reclassify_refreshes_after_the_scope_with_all_ids(patched):
    refresh, _, _ = patched
    svc = _svc()
    svc.db.execute_query.return_value = [
        {'id': A1, 'category': 'server', 'subcategory': 'linux',
         'category_predicted': None, 'subcategory_predicted': None},
        {'id': A2, 'category': 'server', 'subcategory': 'linux',
         'category_predicted': None, 'subcategory_predicted': None},
    ]

    order = MagicMock()
    order.attach_mock(svc.db.conn_manager.end_scope, "end_scope")
    order.attach_mock(refresh, "refresh")

    out = svc.reclassify_bulk([A1, A2], 'network', 'switch', 'admin')

    assert out == {"updated": 2}
    names = [c[0] for c in order.mock_calls]
    # Two end_scope calls: _bulk_scope's nested close (no commit under Flask),
    # then outside_request_scope's exit (the commit). The refresh must run
    # only AFTER that second end_scope — a hook failure may not cost the write.
    assert names[-2:] == ["end_scope", "refresh"]
    assert refresh.call_args.kwargs["asset_ids"] == [A1, A2]


def test_clear_overrides_refreshes_only_the_actually_overridden(patched):
    refresh, _, _ = patched
    svc = _svc()
    svc.db.execute_query.return_value = [
        {'id': A1, 'category': 'network', 'subcategory': 'switch',
         'category_predicted': 'server', 'subcategory_predicted': 'linux'},
        {'id': A2, 'category': 'server', 'subcategory': 'linux',
         'category_predicted': 'server', 'subcategory_predicted': 'linux'},
    ]
    svc.override_repo.get_active_override_ids.return_value = {A1}

    out = svc.clear_overrides_bulk([A1, A2], 'admin')

    assert out == {"cleared": 1}
    refresh.assert_called_once()
    assert refresh.call_args.kwargs["asset_ids"] == [A1]


def test_clear_overrides_with_none_overridden_does_not_refresh(patched):
    refresh, _, _ = patched
    svc = _svc()
    svc.db.execute_query.return_value = [
        {'id': A1, 'category': 'server', 'subcategory': 'linux',
         'category_predicted': 'server', 'subcategory_predicted': 'linux'},
    ]
    svc.override_repo.get_active_override_ids.return_value = set()

    out = svc.clear_overrides_bulk([A1], 'admin')

    assert out == {"cleared": 0}
    refresh.assert_not_called()


def test_refresh_failure_does_not_fail_reclassify_and_clears_stamps(patched):
    refresh, _, repo_cls = patched
    refresh.side_effect = RuntimeError("boom")
    svc = _svc()
    svc.db.execute_query.return_value = [{
        'id': A1, 'category': 'server', 'subcategory': 'linux',
        'manufacturer': '', 'model': '', 'os_normalized': '',
        'mac_vendor': '', 'vendor_category': '',
    }]

    out = svc.reclassify_asset(A1, 'network', 'switch', 'admin')   # must not raise

    assert out["id"] == A1
    repo_cls.return_value.set_materialized_at.assert_called_once_with(["f1"], False)


# ── Hook/transaction state machine (#1428 round 2) ────────────────────────────
# Same harness as the unified-service round-2 test: the REAL connection
# manager over a fake connection, one full request. Pre-fix the bulk write
# was lost: _bulk_scope's inner end_scope does not commit under Flask, so the
# hook ran inside the request scope and its DB error rolled everything back
# at teardown.

class _ScopeFakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        self._conn.events.append(("execute", text))
        self._conn.last_sql = text
        if self._conn.fail_on and self._conn.fail_on in text:
            raise psycopg2.Error("statement timeout")

    def fetchall(self):
        return self._conn.route(self._conn.last_sql)

    def close(self):
        pass


class _ScopeFakeConn:
    """Enough of a psycopg2 connection for the REAL PostgreSQLConnectionManager:
    borrow, session SETs, and a commit/rollback event log."""

    def __init__(self, route, fail_on=None):
        self.events = []
        self.route = route
        self.fail_on = fail_on
        self.last_sql = ""
        self.closed = 0
        self.autocommit = False
        self.info = SimpleNamespace(
            transaction_status=extensions.TRANSACTION_STATUS_IDLE)

    def cursor(self, cursor_factory=None):
        return _ScopeFakeCursor(self)

    def commit(self):
        self.events.append(("commit", None))

    def rollback(self):
        self.events.append(("rollback", None))


_DEF_ROW = {"slug": "f1", "name": "f1", "function": "coalesce",
            "sources": ["static:Category"], "data_type": "string",
            "materialized_at": None}


def _route(sql):
    if "information_schema.columns" in sql:
        return [{"?column?": 1}]   # migration-149 probe: present
    if "SELECT id, category, subcategory" in sql:
        return [
            {'id': A1, 'category': 'server', 'subcategory': 'linux',
             'category_predicted': None, 'subcategory_predicted': None},
            {'id': A2, 'category': 'server', 'subcategory': 'linux',
             'category_predicted': None, 'subcategory_predicted': None},
        ]
    if "FROM unified_fields" in sql:
        return [dict(_DEF_ROW)]
    if "UPDATE unified_fields" in sql:
        return [{"slug": "f1"}]
    return []


def _resolver_stub():
    resolver = MagicMock()
    rf = MagicMock()
    rf.sql_kind = "column"
    rf.data_type = FieldDataType.STRING
    rf.is_native_array.return_value = False
    rf.where_expr.return_value = "category"
    resolver.resolve.return_value = rf
    return resolver


def test_bulk_reclassify_writes_survive_a_hook_db_error():
    """begin_scope → bulk reclassify → hook's UPDATE assets raises a psycopg2
    error inside the wrapper → request scope stays unpoisoned; the reclassify
    commits before the hook and teardown commits."""
    fake = _ScopeFakeConn(route=_route, fail_on="unified_values")
    cm = PostgreSQLConnectionManager(database_url="postgresql://fake/test")
    cm._get_connection_with_retry = lambda: fake
    cm._release_connection = lambda conn: None
    db = PostgreSQLAdapter(cm)
    db.bulk_write_chronicle_events = MagicMock()   # chronicle SQL not under test

    svc = ReclassifyService.__new__(ReclassifyService)   # skip catalog load
    svc.db = db
    svc.override_repo = MagicMock()
    svc._catalog = [{"name": "network", "subcategories": ["switch"]}]
    svc.custom_subcategory_repo = MagicMock()

    with patch(f"{_MODULE}.build_field_resolver", return_value=_resolver_stub()):
        cm.begin_scope()                        # factory.py before_request
        out = svc.reclassify_bulk([A1, A2], 'network', 'switch', 'admin')
        # The hook's DB error must NOT have poisoned the request scope
        # (pre-fix: errored=True → teardown rolled the reclassify back).
        assert getattr(cm._scope_state, "errored", False) is False
        db.execute_query("SELECT 1", fetch=False)   # later request work
        cm.end_scope()                          # teardown_request

    assert out == {"updated": 2}
    kinds = [e[0] for e in fake.events]
    hook_i = next(i for i, (_, sql) in enumerate(fake.events)
                  if sql and "unified_values" in sql)
    assert kinds.index("commit") < hook_i, (
        f"no commit before the hook's UPDATE: {fake.events}")
    assert kinds.count("rollback") == 1, (
        f"expected only the hook statement's own rollback: {fake.events}")
    last_commit = max(i for i, k in enumerate(kinds) if k == "commit")
    last_rollback = max(i for i, k in enumerate(kinds) if k == "rollback")
    assert last_commit > last_rollback, (
        f"teardown did not commit last: {fake.events}")
    assert getattr(cm._scope_state, "depth", 0) == 0, "scope left unbalanced"
