# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end integration tests for the Unified Fields feature.

Coverage
--------
1. test_create_then_resolves_and_selects
   Create a unified field via service -> decode encoded_key -> resolve via
   field_resolver -> run SELECT using the resolved select_expr -> assert
   a list is returned.

2. test_preview_returns_rows
   service.preview() returns the expected envelope shape:
   data_type == "string" and each row has the four required keys.

3. test_filter_executes_via_compiler
   Create a unified field, build a WHERE clause for it via AssetQueryCompiler,
   execute COUNT(*) directly against the DB -> returns an integer.

4. test_cross_engine_equivalence
   Same notempty filter executed through AssetRepository.list_assets() and
   AssetQueryCompiler's WHERE clause -> counts agree (guards the four-engines
   divergence documented in the project memory).

Scope limitation
----------------
Covers the two SQL filter engines only — Explorer (AssetRepository) and the
shared AssetQueryCompiler. It does NOT cover the in-memory tag-rule compliance
evaluator (query_engine_service._extract_asset_value), which — like the existing
complex built-in virtuals (asset_label, confidence_level) — does not evaluate
virtual-field SQL expressions per asset. Unified fields are reliable as
target/filter fields (SQL path); using one as an in-memory compliance *condition*
is a known pre-existing limitation.

Requires a live Postgres (DATABASE_URL / postgresql://test:test@localhost:5434/assetatlas_test)
with migration 114 applied and at least a few assets rows.

Run with: pytest -m integration tests/integration/test_unified_fields_integration.py -v
"""

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def svc(live_app):
    """UnifiedFieldsService wired into the live app."""
    return live_app.unified_fields_service


@pytest.fixture
def resolver(live_app):
    """FieldResolver wired into the live app."""
    return live_app.field_resolver


@pytest.fixture(autouse=True)
def cleanup_itest_fields(live_db):
    """Delete any itest_ unified fields written during the test, even on failure."""
    yield
    live_db.execute_query(
        "DELETE FROM unified_fields WHERE slug LIKE %(pat)s",
        {"pat": "itest_%"},
        fetch=False,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_create_then_resolves_and_selects(svc, live_db, resolver):
    """Create a unified field, decode its encoded_key, resolve it, and SELECT
    from assets using the resolved SQL expression.

    Coalesce of two scalar STRING fields (Category, Subcategory) is used --
    coalesce requires non-array source types (see definition.py:validate_compatibility).
    """
    from src.modules.fields.unified.models import UnifiedFieldWrite
    from src.modules.fields.encoding import decode

    out = svc.create(UnifiedFieldWrite(
        name="itest_label",
        function="coalesce",
        sources=["static:Category", "static:Subcategory"],
    ))

    encoded_key = out["encoded_key"]
    assert encoded_key.startswith("virtual:itest_label"), (
        f"Expected encoded_key to start with 'virtual:itest_label', got {encoded_key!r}"
    )
    assert out["data_type"] == "string"

    # Resolve via the live catalog (catalog was invalidated by create)
    field_id = decode(encoded_key)
    rf = resolver.resolve(field_id)

    # Run an actual SELECT -- if the SQL expression is malformed this will raise
    rows = live_db.execute_query(
        f"SELECT {rf.select_expr(alias='v')} FROM assets LIMIT 5"
    )
    assert isinstance(rows, list), (
        f"execute_query should return a list; got {type(rows).__name__}"
    )
    if rows:
        assert "v" in rows[0], (
            f"Expected alias 'v' to appear in result row keys; got {list(rows[0].keys())!r}"
        )


def test_preview_returns_rows(svc):
    """service.preview() returns data_type='string' and rows with the
    expected four keys: asset_id, hostname, source_values, result.

    Uses scalar Category + Subcategory sources -- coalesce is scalar-only.
    """
    from src.modules.fields.unified.models import UnifiedFieldPreviewRequest

    res = svc.preview(UnifiedFieldPreviewRequest(
        function="coalesce",
        sources=["static:Category", "static:Subcategory"],
        sample_size=5,
    ))

    assert res["data_type"] == "string", (
        f"Expected data_type='string', got {res['data_type']!r}"
    )
    assert isinstance(res["rows"], list), (
        f"Expected rows to be a list, got {type(res['rows']).__name__}"
    )
    for row in res["rows"]:
        assert set(row.keys()) == {"asset_id", "hostname", "source_values", "result"}, (
            f"Row keys mismatch: {set(row.keys())!r}"
        )


def test_filter_executes_via_compiler(svc, live_db, resolver):
    """Create a unified field, build a WHERE clause via AssetQueryCompiler with
    a notempty filter, execute COUNT(*) against the live DB.

    Verifies that the unified field's SQL expression is accepted end-to-end
    by the shared query compiler used by tag_rules and alert_rules.

    AssetQueryCompiler filter dict keys: field_id + operator + value
    (distinct from AssetRepository which uses field_id + op + value).
    """
    from src.modules.fields.unified.models import UnifiedFieldWrite
    from src.modules.shared.asset_query_compiler import AssetQueryCompiler

    out = svc.create(UnifiedFieldWrite(
        name="itest_src",
        function="coalesce",
        sources=["static:Category", "static:Subcategory"],
    ))
    key = out["encoded_key"]

    compiler = AssetQueryCompiler(resolver)
    # AssetQueryCompiler.build_where_clause uses 'field_id' + 'operator' + 'value'
    where, params = compiler.build_where_clause([{
        "field_id": key,
        "operator": "notempty",
        "value": None,
    }])

    assert where, "Expected a non-empty WHERE clause from compiler"

    rows = live_db.execute_query(
        f"SELECT COUNT(*) AS c FROM assets WHERE {where}",
        params,
    )
    assert rows and isinstance(rows[0]["c"], int), (
        f"Expected COUNT(*) to return an integer; got {rows!r}"
    )


# Guards the two SQL engines (Explorer + shared compiler) only — not the in-memory compliance evaluator.
def test_cross_engine_equivalence(svc, live_db, resolver):
    """Same notempty filter yields identical asset count through both engines:
    - AssetRepository.list_assets() (Explorer path, uses 'op' key)
    - AssetQueryCompiler + raw COUNT(*) (shared compiler path, uses 'operator' key)

    This guards the four-engines divergence noted in the project memory:
    reference_operator_filter_architecture.md lists Explorer's
    _build_single_filter and the shared AssetQueryCompiler as two of the four
    distinct filter engines that can diverge on edge cases.

    Uses scalar Category field so coalesce resolves to a non-array SQL expr --
    notempty on a scalar emits IS NOT NULL AND <col> != '' which both engines
    handle identically through the operator registry's build_sql.

    Filter dict shapes differ between engines:
    - AssetRepository._build_filter_clauses: field_id + 'op' + value
    - AssetQueryCompiler.build_where_clause:  field_id + 'operator' + value
    """
    from src.modules.fields.unified.models import UnifiedFieldWrite
    from src.modules.assets.repositories.asset_repository import AssetRepository
    from src.modules.shared.asset_query_compiler import AssetQueryCompiler

    out = svc.create(UnifiedFieldWrite(
        name="itest_equiv",
        function="coalesce",
        sources=["static:Category", "static:Subcategory"],
    ))
    key = out["encoded_key"]

    # --- Explorer path ---
    # AssetRepository._build_filter_clauses: field_id (encoded key string) + 'op' + value
    asset_repo = AssetRepository(live_db, resolver)
    _rows, explorer_count = asset_repo.list_assets(
        page=1,
        limit=10000,
        filters=[{
            "field_id": key,
            "op": "notempty",
            "value": None,
        }],
    )

    # --- Shared compiler path ---
    # AssetQueryCompiler.build_where_clause: field_id (encoded key string) + 'operator' + value
    compiler = AssetQueryCompiler(resolver)
    where, params = compiler.build_where_clause([{
        "field_id": key,
        "operator": "notempty",
        "value": None,
    }])

    if not where:
        pytest.fail(
            "AssetQueryCompiler produced an empty WHERE clause for the notempty filter "
            "on this unified field -- this is a compiler BUG, not a skip condition"
        )

    if explorer_count == 0:
        pytest.skip(
            "no assets with non-empty category/subcategory -- "
            "equivalence check would be vacuous"
        )

    count_rows = live_db.execute_query(
        f"SELECT COUNT(*) AS c FROM assets WHERE {where}",
        params,
    )
    compiler_count = count_rows[0]["c"] if count_rows else 0

    assert explorer_count == compiler_count, (
        f"Engine counts diverge: AssetRepository={explorer_count}, "
        f"AssetQueryCompiler={compiler_count}. "
        f"WHERE clause: {where!r} params: {params!r}"
    )
