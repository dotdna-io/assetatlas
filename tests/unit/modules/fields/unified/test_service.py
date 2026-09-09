# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg2
from psycopg2 import extensions

from src.database.postgres_adapter import PostgreSQLAdapter
from src.database.postgres_connection import PostgreSQLConnectionManager
from src.modules.fields.catalog import FieldCatalog
from src.modules.fields.resolver import FieldResolver
from src.modules.fields.unified.repository import UnifiedFieldsRepository
from src.modules.fields.unified.service import UnifiedFieldsService
from src.modules.fields.unified.models import UnifiedFieldWrite, UnifiedFieldPreviewRequest
from src.api.middleware.error_handler import ValidationError, ConflictError


class _DB:
    def __init__(self):
        self.conn_manager = MagicMock()

    def execute_query(self, *a, **k):
        return []


class _CapturingDB:
    """DB stub that records the last SQL it was called with."""
    def __init__(self):
        self.last_sql = None
        self.conn_manager = MagicMock()

    def execute_query(self, sql, params=None):
        self.last_sql = sql
        return []


def _service(repo, defs=None):
    cat = FieldCatalog(db_adapter=_DB(), group_mapping_provider=lambda: {},
                       unified_fields_provider=lambda: (defs or []))
    resolver = FieldResolver(catalog=cat)
    return UnifiedFieldsService(repo, catalog=cat, resolver=resolver, db_adapter=_DB())


def _write(**kw):
    base = dict(name="lentity", function="coalesce",
                sources=["static:Category", "static:Subcategory"])
    base.update(kw)
    return UnifiedFieldWrite(**base)


def test_create_rejects_virtual_source():
    repo = MagicMock()
    repo.name_exists.return_value = False
    svc = _service(repo)
    with pytest.raises(ValidationError) as exc:
        svc.create(_write(sources=["static:Hostname", "virtual:confidence_level"]))
    assert "cannot be used as sources" in str(exc.value)


def test_create_rejects_unknown_source():
    repo = MagicMock()
    repo.name_exists.return_value = False
    svc = _service(repo)
    with pytest.raises(ValidationError) as exc:
        svc.create(_write(sources=["static:Hostname", "ext:Ghost:x"]))
    assert "not in the catalog" in str(exc.value)


def test_create_name_collision_with_existing_catalog_field():
    repo = MagicMock()
    repo.name_exists.return_value = False
    svc = _service(repo)
    with pytest.raises(ConflictError):
        svc.create(_write(name="Hostname"))  # collides with the static field


def test_create_happy_path_generates_slug_and_invalidates(monkeypatch):
    repo = MagicMock()
    repo.name_exists.return_value = False
    repo.slug_exists.return_value = False
    repo.create.return_value = {
        "id": "id1", "slug": "lentity", "name": "lentity", "function": "coalesce",
        "sources": ["static:Hostname", "static:FQDN"], "data_type": "string",
        "created_at": None, "updated_at": None,
    }
    svc = _service(repo)
    invalidated = {"n": 0}
    monkeypatch.setattr(svc.catalog, "invalidate", lambda: invalidated.__setitem__("n", invalidated["n"] + 1))
    out = svc.create(_write())
    assert repo.create.call_args.kwargs["slug"] == "lentity"
    assert repo.create.call_args.kwargs["data_type"] == "string"
    assert out["encoded_key"] == "virtual:lentity"
    assert invalidated["n"] == 1


def test_unique_slug_avoids_builtin_virtual_collision():
    from unittest.mock import MagicMock
    repo = MagicMock()
    repo.slug_exists.return_value = False
    svc = _service(repo)  # reuse the module's existing _service helper
    assert svc._unique_slug("confidence level") == "confidence_level_2"


def test_preview_order_by_repeats_merge_expr():
    """Regression: preview ORDER BY must repeat the merge expression rather than
    referencing the SELECT alias 'result'. PostgreSQL does not substitute a
    SELECT alias when it appears inside an expression in ORDER BY, so
    ORDER BY (result IS NULL) raises 'column "result" does not exist'.
    The fix is ORDER BY (<merge_sql> IS NULL).
    """
    repo = MagicMock()
    capturing_db = _CapturingDB()
    cat = FieldCatalog(db_adapter=_DB(), group_mapping_provider=lambda: {},
                       unified_fields_provider=lambda: [])
    resolver = FieldResolver(catalog=cat)
    svc = UnifiedFieldsService(repo, catalog=cat, resolver=resolver, db_adapter=capturing_db)

    svc.preview(UnifiedFieldPreviewRequest(
        function="coalesce",
        sources=["static:Category", "static:Subcategory"],
        sample_size=5,
    ))

    sql = capturing_db.last_sql
    assert sql is not None, "execute_query was never called"
    assert "ORDER BY (result IS NULL)" not in sql, (
        "ORDER BY must not reference the alias 'result' inside an expression -- "
        "PostgreSQL cannot resolve SELECT aliases in ORDER BY expressions"
    )
    assert "ORDER BY (COALESCE(" in sql, (
        f"ORDER BY must repeat the merge expression (e.g. COALESCE(...)); got: {sql!r}"
    )


# ── F1: update with same name does not conflict ────────────────────────────────
def test_update_with_same_name_does_not_conflict():
    """PUT keeping the same name must not raise ConflictError (F1 fix)."""
    repo = MagicMock()
    repo.get.return_value = {
        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "slug": "lentity", "name": "lentity", "function": "coalesce",
        "sources": ["static:Category", "static:Subcategory"],
        "data_type": "string", "created_at": None, "updated_at": None,
    }
    repo.name_exists.return_value = False
    repo.update.return_value = repo.get.return_value

    # The catalog already has this unified field under the same slug
    from src.modules.fields.types import FieldDataType
    defs = [{"slug": "lentity", "name": "lentity", "function": "coalesce",
             "sources": ["static:Category", "static:Subcategory"],
             "data_type": FieldDataType.STRING}]
    svc = _service(repo, defs=defs)
    # Should NOT raise ConflictError
    out = svc.update("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", _write())
    assert out["name"] == "lentity"
    repo.update.assert_called_once()


# ── F2: update returns 404 when row deleted mid-flight ─────────────────────────
def test_update_returns_404_when_row_deleted_midflight():
    """repo.update() returning None (concurrent delete) must raise NotFoundError (F2 fix)."""
    from src.api.middleware.error_handler import NotFoundError
    repo = MagicMock()
    repo.get.return_value = {
        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "slug": "lentity", "name": "lentity", "function": "coalesce",
        "sources": ["static:Category", "static:Subcategory"],
        "data_type": "string", "created_at": None, "updated_at": None,
    }
    repo.name_exists.return_value = False
    repo.update.return_value = None   # concurrent delete

    svc = _service(repo)
    with pytest.raises(NotFoundError):
        svc.update("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", _write())


# ── F5: get rejects non-UUID id ───────────────────────────────────────────────
def test_get_rejects_non_uuid_id():
    """Non-UUID ids must return 404 without hitting the DB (F5 fix)."""
    from src.api.middleware.error_handler import NotFoundError
    repo = MagicMock()
    svc = _service(repo)
    with pytest.raises(NotFoundError):
        svc.get("preview")
    repo.get.assert_not_called()


# ── Materialized-value hooks (#1405 part 3) ───────────────────────────────────
# The hooks refresh/clear stored unified values through the part-1 helpers;
# these tests patch the helpers in the service's namespace and assert how the
# service drives them (slugs, ordering, failure swallowing). The helpers' own
# SQL is covered in test_materialize.py.

_MAT_ROW = {
    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "slug": "lentity", "name": "lentity", "function": "coalesce",
    "sources": ["static:Category", "static:Subcategory"],
    "data_type": "string", "created_at": None, "updated_at": None,
}


def _existing_repo(**overrides):
    """A repo mock standing in for one existing materializable field 'lentity'."""
    repo = MagicMock()
    repo.get.return_value = dict(_MAT_ROW, **overrides)
    repo.name_exists.return_value = False
    repo.update.return_value = dict(_MAT_ROW, **overrides)
    repo.list_for_catalog.return_value = []
    return repo


def test_create_refreshes_materialized_value_for_new_slug(monkeypatch):
    repo = MagicMock()
    repo.name_exists.return_value = False
    repo.slug_exists.return_value = False
    repo.create.return_value = dict(_MAT_ROW)
    repo.list_for_catalog.return_value = defs = [{"slug": "lentity"}]
    svc = _service(repo)
    refresh = MagicMock()
    monkeypatch.setattr(
        "src.modules.fields.unified.service.refresh_unified_values", refresh)

    out = svc.create(_write())

    refresh.assert_called_once()
    args, kwargs = refresh.call_args
    assert args == (svc.db, svc.resolver, defs)
    assert kwargs == {"slugs": ["lentity"]}   # full-table refresh → stamps
    assert out["encoded_key"] == "virtual:lentity"


def test_create_refresh_failure_does_not_fail_create(monkeypatch):
    repo = MagicMock()
    repo.name_exists.return_value = False
    repo.slug_exists.return_value = False
    repo.create.return_value = dict(_MAT_ROW)
    repo.list_for_catalog.return_value = []
    svc = _service(repo)
    monkeypatch.setattr(
        "src.modules.fields.unified.service.refresh_unified_values",
        MagicMock(side_effect=RuntimeError("boom")))

    out = svc.create(_write())   # must not raise

    assert out["encoded_key"] == "virtual:lentity"
    # failure leaves the stamp cleared so the resolver falls back to live SQL
    repo.set_materialized_at.assert_called_once_with(["lentity"], False)


def test_update_clears_stamp_before_definition_update_and_refresh(monkeypatch):
    repo = _existing_repo()
    svc = _service(repo)
    refresh = MagicMock()
    monkeypatch.setattr(
        "src.modules.fields.unified.service.refresh_unified_values", refresh)

    order = MagicMock()
    order.attach_mock(repo.set_materialized_at, "set_materialized_at")
    order.attach_mock(repo.update, "update")
    order.attach_mock(refresh, "refresh")

    svc.update(_MAT_ROW["id"], _write())

    names = [c[0] for c in order.mock_calls]
    assert names == ["set_materialized_at", "update", "refresh"]
    assert repo.set_materialized_at.call_args.args == (["lentity"], False)
    assert refresh.call_args.kwargs["slugs"] == ["lentity"]


def test_update_slug_rename_drops_old_stored_value(monkeypatch):
    repo = _existing_repo()
    repo.update.return_value = dict(_MAT_ROW, slug="lentity_v2")
    svc = _service(repo)
    refresh = MagicMock()
    remove = MagicMock()
    monkeypatch.setattr(
        "src.modules.fields.unified.service.refresh_unified_values", refresh)
    monkeypatch.setattr(
        "src.modules.fields.unified.service.remove_unified_value", remove)

    svc.update(_MAT_ROW["id"], _write())

    remove.assert_called_once_with(svc.db, "lentity")
    assert refresh.call_args.kwargs["slugs"] == ["lentity_v2"]


def test_update_refresh_failure_does_not_fail_update_and_reclears_stamp(monkeypatch):
    repo = _existing_repo()
    svc = _service(repo)
    monkeypatch.setattr(
        "src.modules.fields.unified.service.refresh_unified_values",
        MagicMock(side_effect=RuntimeError("boom")))

    out = svc.update(_MAT_ROW["id"], _write())   # must not raise

    assert out["encoded_key"] == "virtual:lentity"
    # cleared once before the definition update, once again on the failure path
    assert repo.set_materialized_at.call_count == 2
    for c in repo.set_materialized_at.call_args_list:
        assert c.args == (["lentity"], False)


def test_delete_removes_stored_value(monkeypatch):
    repo = _existing_repo()
    repo.delete.return_value = True
    svc = _service(repo)
    remove = MagicMock()
    monkeypatch.setattr(
        "src.modules.fields.unified.service.remove_unified_value", remove)

    assert svc.delete(_MAT_ROW["id"]) is None
    remove.assert_called_once_with(svc.db, "lentity")


def test_delete_unknown_field_raises_not_found():
    from src.api.middleware.error_handler import NotFoundError
    repo = MagicMock()
    repo.get.return_value = None
    svc = _service(repo)
    with pytest.raises(NotFoundError):
        svc.delete("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    repo.delete.assert_not_called()


def test_delete_remove_failure_does_not_raise(monkeypatch):
    repo = _existing_repo()
    repo.delete.return_value = True
    svc = _service(repo)
    monkeypatch.setattr(
        "src.modules.fields.unified.service.remove_unified_value",
        MagicMock(side_effect=RuntimeError("boom")))

    assert svc.delete(_MAT_ROW["id"]) is None   # must not raise


# ── Hook/transaction state machine (#1428 round 2) ────────────────────────────
# The review proved the defect with the real connection manager and a fake
# connection; these tests pin the fix the same way. Under Flask a request is
# ONE scope on ONE connection, rolled back at teardown on the first DB error
# inside it — so a hook's failing `UPDATE assets` used to cost the user's own
# write. The wrapper must commit that write BEFORE the hook runs and leave
# the request scope unpoisoned.

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


def _real_connection_manager(fake):
    cm = PostgreSQLConnectionManager(database_url="postgresql://fake/test")
    cm._get_connection_with_retry = lambda: fake
    cm._release_connection = lambda conn: None
    return cm


def _assert_write_survives_hook_error(fake, cm):
    """The user write committed before the hook ran; the request scope was
    never poisoned, so teardown committed instead of rolling back — the only
    rollback in the whole request is the hook statement's own."""
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


_STATE_ROW = {
    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "slug": "lentity", "name": "lentity", "function": "coalesce",
    "sources": ["static:Category", "static:Subcategory"],
    "data_type": "string", "created_at": None, "updated_at": None,
    "materialized_at": None,
}


def _state_route(sql):
    if "information_schema.columns" in sql:
        return [{"?column?": 1}]               # migration-149 probe: present
    if "SELECT 1 FROM unified_fields" in sql:
        return []                              # name_exists
    if "FROM unified_fields" in sql:
        return [dict(_STATE_ROW)]              # get / list_for_catalog
    if "UPDATE unified_fields SET materialized_at" in sql:
        return [{"slug": "lentity"}]           # set_materialized_at
    if "UPDATE unified_fields" in sql:
        return [dict(_STATE_ROW)]              # definition UPDATE ... RETURNING
    return []


def test_update_write_survives_a_hook_db_error():
    """Real connection manager + fake connection, one full request: begin_scope
    → definition update → hook's UPDATE assets raises a psycopg2 error inside
    the wrapper → request scope stays unpoisoned; teardown commits."""
    fake = _ScopeFakeConn(route=_state_route, fail_on="unified_values")
    cm = _real_connection_manager(fake)
    db = PostgreSQLAdapter(cm)
    cat = FieldCatalog(db_adapter=_DB(), group_mapping_provider=lambda: {},
                       unified_fields_provider=lambda: [dict(_STATE_ROW)])
    resolver = FieldResolver(catalog=cat)
    svc = UnifiedFieldsService(UnifiedFieldsRepository(db), catalog=cat,
                               resolver=resolver, db_adapter=db)

    cm.begin_scope()                            # factory.py before_request
    out = svc.update(_STATE_ROW["id"], _write())   # must not raise
    # The hook's DB error must NOT have poisoned the request scope (pre-fix it
    # did: errored=True → teardown rolled the definition UPDATE back).
    assert getattr(cm._scope_state, "errored", False) is False
    db.execute_query("SELECT 1", fetch=False)   # later work in the same request
    cm.end_scope()                              # teardown_request

    assert out["name"] == "lentity"
    _assert_write_survives_hook_error(fake, cm)
