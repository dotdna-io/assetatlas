# SPDX-License-Identifier: AGPL-3.0-or-later
from src.modules.fields.types import FieldDataType
from src.modules.fields.catalog import FieldCatalog, build_catalog_response
from src.modules.fields.encoding import encode
from src.modules.fields.models import VirtualField


def _make_catalog(defs):
    # db_adapter is unused for unified entries (provider supplies them); the
    # other dynamic sources query the DB, so stub execute_query to return [].
    class _DB:
        def execute_query(self, *a, **k):
            return []
    return FieldCatalog(db_adapter=_DB(), group_mapping_provider=lambda: {},
                        unified_fields_provider=lambda: defs)


_DEF = {"slug": "lentity", "name": "lentity", "function": "coalesce",
        "sources": ["ext:A:lentity", "ext:B:lentity"], "data_type": FieldDataType.STRING}


def test_unified_entry_appears_in_catalog_with_unified_category():
    cat = _make_catalog([_DEF])
    resp = build_catalog_response(cat)
    entry = next(e for e in resp["entries"] if e["display_name"] == "lentity")
    assert entry["category"] == "unified"
    assert entry["encoded_key"] == encode(VirtualField(kind="virtual", name="lentity"))
    assert entry["data_type"] == "string"


def test_invalidate_forces_reload():
    state = {"defs": [_DEF]}
    class _DB:
        def execute_query(self, *a, **k):
            return []
    cat = FieldCatalog(db_adapter=_DB(), group_mapping_provider=lambda: {},
                       unified_fields_provider=lambda: state["defs"])
    assert any(e.virtual_name == "lentity" for e in cat.entries())
    state["defs"] = []
    cat.invalidate()
    assert not any(e.virtual_name == "lentity" for e in cat.entries())


# ── F8: unified slug colliding with built-in is skipped ──────────────────────
def test_unified_slug_colliding_with_builtin_is_skipped():
    """A unified field whose slug matches a built-in virtual must be dropped (F8 fix)."""
    from src.modules.fields.types import FieldDataType
    collider = {"slug": "confidence_level", "name": "My Confidence",
                "function": "coalesce", "sources": ["static:Category"],
                "data_type": FieldDataType.STRING}
    cat = _make_catalog([collider])
    # The built-in confidence_level virtual field must still be resolvable
    from src.modules.fields.models import VirtualField
    from src.modules.fields.resolver import FieldResolver
    resolver = FieldResolver(catalog=cat)
    fid = VirtualField(kind="virtual", name="confidence_level")
    rf = resolver.resolve(fid)
    # built-in has display_name "Confidence Level", not "My Confidence"
    assert rf.display_name == "Confidence Level"
    # No unified (category="unified") entry with that slug should appear
    resp = build_catalog_response(cat)
    unified_entries = [e for e in resp["entries"]
                       if e["category"] == "unified" and "confidence_level" in e["encoded_key"]]
    assert unified_entries == [], f"Expected no unified entry for confidence_level, got {unified_entries}"
