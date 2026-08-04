# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from unittest.mock import MagicMock
from src.modules.fields.catalog import FieldCatalog
from src.modules.fields.resolver import FieldResolver
from src.modules.fields.unified.service import UnifiedFieldsService
from src.modules.fields.unified.models import UnifiedFieldWrite, UnifiedFieldPreviewRequest
from src.api.middleware.error_handler import ValidationError, ConflictError


class _DB:
    def execute_query(self, *a, **k):
        return []


class _CapturingDB:
    """DB stub that records the last SQL it was called with."""
    def __init__(self):
        self.last_sql = None

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
