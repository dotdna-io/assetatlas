# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for confidence_level filter translation in AssetRepository.

Filters now carry a typed `field_id` (discriminated union) instead of a
string `field` key. See src/modules/fields/models.py.
"""

import pytest
from unittest.mock import MagicMock

from src.modules.assets.repositories.asset_repository import AssetRepository
from src.modules.fields import FieldCatalog, FieldResolver


def _make_resolver():
    """Resolver backed by a catalog with static + virtual entries (no DB hits)."""
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


def _confidence_level_filter(op, value):
    return {
        'field_id': {'kind': 'virtual', 'name': 'confidence_level'},
        'op': op,
        'value': value,
    }


def _static_filter(name, op, value):
    return {
        'field_id': {'kind': 'static', 'name': name},
        'op': op,
        'value': value,
    }


class TestConfidenceLevelFilter:
    """confidence_level filters translate to numeric range conditions."""

    @pytest.fixture
    def repo(self):
        return AssetRepository(db_adapter=MagicMock(), resolver=_make_resolver())

    def test_eq_high(self, repo):
        filters = [_confidence_level_filter('eq', 'high')]
        clauses, params = repo._build_filter_clauses(
            filters, confidence_thresholds=(0.45, 0.70)
        )
        assert len(clauses) == 1
        assert 'confidence >=' in clauses[0]

    def test_eq_medium(self, repo):
        filters = [_confidence_level_filter('eq', 'medium')]
        clauses, params = repo._build_filter_clauses(
            filters, confidence_thresholds=(0.45, 0.70)
        )
        assert len(clauses) == 1
        assert 'AND' in clauses[0]  # range: >= medium AND < high

    def test_eq_low(self, repo):
        filters = [_confidence_level_filter('eq', 'low')]
        clauses, params = repo._build_filter_clauses(
            filters, confidence_thresholds=(0.45, 0.70)
        )
        assert len(clauses) == 1
        assert 'confidence <' in clauses[0]

    def test_any_multiple_levels(self, repo):
        filters = [_confidence_level_filter('any', ['high', 'low'])]
        clauses, params = repo._build_filter_clauses(
            filters, confidence_thresholds=(0.45, 0.70)
        )
        assert len(clauses) == 1
        assert 'OR' in clauses[0]

    def test_custom_thresholds(self, repo):
        """Custom thresholds produce correct boundary values."""
        filters = [_confidence_level_filter('eq', 'high')]
        clauses, params = repo._build_filter_clauses(
            filters, confidence_thresholds=(0.30, 0.80)
        )
        # The param value should be 0.80
        assert any(v == 0.80 for v in params.values())

    def test_unknown_level_skipped(self, repo):
        filters = [_confidence_level_filter('eq', 'invalid')]
        clauses, params = repo._build_filter_clauses(
            filters, confidence_thresholds=(0.45, 0.70)
        )
        assert len(clauses) == 0

    def test_mixed_filters(self, repo):
        """confidence_level filter works alongside standard filters."""
        filters = [
            _static_filter('Category', 'eq', 'Server'),
            _confidence_level_filter('eq', 'high'),
        ]
        clauses, params = repo._build_filter_clauses(
            filters, confidence_thresholds=(0.45, 0.70)
        )
        assert len(clauses) == 2


class TestArraySetOperators:
    """all/none on Data Source (STRING_ARRAY) via the registry-backed builder."""

    @pytest.fixture
    def repo(self):
        return AssetRepository(db_adapter=MagicMock(), resolver=_make_resolver())

    def test_none_drops_to_not_exists(self, repo):
        filters = [_static_filter("Data Source", "none", ["jamf", "sccm"])]
        clauses, params = repo._build_filter_clauses(filters)
        assert len(clauses) == 1
        # group-name OR-expansion does not apply to 'none'
        assert "NOT EXISTS" in clauses[0]
        assert "LOWER" in clauses[0]

    def test_all_is_and_of_exists(self, repo):
        filters = [_static_filter("Data Source", "all", ["jamf", "sccm"])]
        clauses, params = repo._build_filter_clauses(filters)
        assert clauses[0].count("EXISTS") == 2
        assert " AND " in clauses[0]

    def test_empty_list_drops_clause(self, repo):
        filters = [_static_filter("Data Source", "any", [])]
        clauses, params = repo._build_filter_clauses(filters)
        assert len(clauses) == 0

    def test_eq_scalar_is_exact_not_ilike(self, repo):
        # R4: scalar eq is exact LOWER()=, not ILIKE.
        filters = [_static_filter("Category", "eq", "Server")]
        clauses, params = repo._build_filter_clauses(filters)
        assert "LOWER(category) = LOWER(" in clauses[0]
        assert "ILIKE" not in clauses[0]

    def test_empty_string_value_drops_clause(self, repo):
        # An empty value for a binary op is "not provided" -> dropped (matches
        # the compiler), not emitted as ILIKE '%%' which would match every row.
        filters = [_static_filter("Category", "contains", "")]
        clauses, params = repo._build_filter_clauses(filters)
        assert len(clauses) == 0


class TestDataSourceProviderMembership:
    """A 'Data Source' filter must also match assets via their asset_providers
    row.

    For integrations that belong to a group, the pipeline collapses
    ``assets.data_source`` to the GROUP name (asset_scorer._collapse_groups),
    so the member's own name never appears in ``assets.data_source``. The
    member's contribution survives only in ``asset_providers.data_source``.
    Without provider expansion, filtering by the member name (as the
    datasource-details page does) returns zero rows even though the header
    count — derived from asset_providers — is non-zero.
    """

    @pytest.fixture
    def repo(self):
        return AssetRepository(db_adapter=MagicMock(), resolver=_make_resolver())

    def test_contains_expands_to_provider_membership(self, repo):
        filters = [_static_filter("Data Source", "contains", "Corp Entra")]
        clauses, params = repo._build_filter_clauses(filters)
        assert len(clauses) == 1
        # OR-expanded to match assets that have a provider row for this source.
        assert "asset_providers" in clauses[0]
        assert "ap.asset_id = assets.id" in clauses[0]
        # Assert on the provider arm's OWN param (suffix _prov), not the base
        # array clause's param — otherwise the test passes even if provider
        # expansion is deleted (both arms bind the same value).
        prov = {k: v for k, v in params.items() if k.endswith("_prov")}
        assert prov, "provider arm must emit its own param"
        assert next(iter(prov.values())) == "%Corp Entra%"

    def test_contains_escapes_ilike_metachars(self, repo):
        # The provider arm must escape %/_ like the base clause, else a source
        # name with an underscore ("crowdstrike_falcon") would let `_` act as a
        # wildcard and over-match "crowdstrikeXfalcon".
        filters = [_static_filter("Data Source", "contains", "crowdstrike_falcon")]
        clauses, params = repo._build_filter_clauses(filters)
        assert "ESCAPE" in clauses[0]
        prov = {k: v for k, v in params.items() if k.endswith("_prov")}
        assert next(iter(prov.values())) == "%crowdstrike\\_falcon%"

    def test_eq_expands_to_provider_membership(self, repo):
        filters = [_static_filter("Data Source", "eq", "Corp Entra")]
        clauses, params = repo._build_filter_clauses(filters)
        assert len(clauses) == 1
        assert "asset_providers" in clauses[0]
        assert "ap.asset_id = assets.id" in clauses[0]
        prov = {k: v for k, v in params.items() if k.endswith("_prov")}
        assert prov, "provider arm must emit its own param"
        assert next(iter(prov.values())) == "Corp Entra"

    def test_eq_is_case_insensitive(self, repo):
        # The provider arm must case-fold like the base clause, else a grouped
        # member (whose name is absent from assets.data_source) is missed on a
        # case-variant eq — defeating the fix this class guards.
        filters = [_static_filter("Data Source", "eq", "hs entra")]
        clauses, params = repo._build_filter_clauses(filters)
        assert "LOWER(ap.data_source) = LOWER(" in clauses[0]

    def test_set_operators_do_not_expand_to_provider(self, repo):
        # all/none never resolve to eq/contains -> no provider expansion.
        filters = [_static_filter("Data Source", "none", ["jamf"])]
        clauses, params = repo._build_filter_clauses(filters)
        assert len(clauses) == 1
        assert "asset_providers" not in clauses[0]

    def test_non_data_source_field_does_not_expand(self, repo):
        filters = [_static_filter("Category", "contains", "Server")]
        clauses, params = repo._build_filter_clauses(filters)
        assert len(clauses) == 1
        assert "asset_providers" not in clauses[0]


class TestAggregateAssetsInjectionGuard:
    """aggregate_assets rejects non-live FieldIds before hitting SQL.

    The two user-controlled column positions (row_field, col_field) are the
    highest-risk injection surface in the repository.  A well-formed but
    non-live ExtendedAttributeField raises InvalidFieldError from the resolver,
    so execute_query is never called with user-supplied strings at column
    position.
    """

    def test_aggregate_assets_rejects_unknown_row_field(self):
        """Non-live row_field returns empty dict; SQL is never executed."""
        from src.modules.fields import FieldCatalog, FieldResolver
        from src.modules.fields.encoding import encode
        from src.modules.fields.models import ExtendedAttributeField

        db = MagicMock()
        db.execute_query.return_value = []
        resolver = FieldResolver(catalog=FieldCatalog(db_adapter=db))
        repo = AssetRepository(db, resolver=resolver)

        # Encode a well-formed but non-live FieldId — source doesn't exist in catalog.
        malicious = encode(ExtendedAttributeField(
            kind="extended_attribute",
            source="NonExistent'; DROP TABLE assets;--",
            attribute="x",
        ))

        result = repo.aggregate_assets(row_field=malicious, col_field=None, filters=None)

        # InvalidFieldError is caught internally; empty response returned.
        assert result == {'rows': [], 'columns': [], 'data': {}, 'grand_total': 0}
        # The malicious string must NOT appear in any SQL query sent to the DB.
        # (The catalog may call execute_query for index construction — that is
        # fine; what must NOT happen is the user string reaching a column
        # position in an f-string query.)
        for call_args in db.execute_query.call_args_list:
            query_str = call_args[0][0] if call_args[0] else ''
            assert "NonExistent" not in query_str
            assert "DROP TABLE" not in query_str

    def test_aggregate_assets_rejects_unknown_col_field(self):
        """Non-live col_field is silently dropped; SQL runs as 1-D aggregate."""
        from src.modules.fields import FieldCatalog, FieldResolver
        from src.modules.fields.encoding import encode
        from src.modules.fields.models import ExtendedAttributeField, StaticField

        db = MagicMock()
        db.execute_query.return_value = []
        resolver = FieldResolver(catalog=FieldCatalog(db_adapter=db))
        repo = AssetRepository(db, resolver=resolver)

        malicious_col = encode(ExtendedAttributeField(
            kind="extended_attribute",
            source="'; DROP TABLE assets;--",
            attribute="y",
        ))
        # row_field is valid (static:Category is in the static catalog).
        valid_row = encode(StaticField(kind="static", name="Category"))

        result = repo.aggregate_assets(
            row_field=valid_row,
            col_field=malicious_col,
            filters=None,
        )

        # col_field rejected → falls back to 1-D query; no error raised.
        assert 'rows' in result
        # When execute_query is called, it must NOT contain the injected string.
        for call_args in db.execute_query.call_args_list:
            query_str = call_args[0][0] if call_args[0] else ''
            assert "DROP TABLE" not in query_str


def _virtual_filter(name, op, value):
    return {"field_id": {"kind": "virtual", "name": name}, "op": op, "value": value}


class TestGraceFilters:
    """in_grace / days_in_grace compile to WHERE clauses and SELECT columns."""

    @pytest.fixture
    def repo(self):
        return AssetRepository(db_adapter=MagicMock(), resolver=_make_resolver())

    def test_in_grace_true_filters_on_missing_since(self, repo):
        clauses, params = repo._build_filter_clauses(
            [_virtual_filter("in_grace", "eq", True)]
        )
        assert len(clauses) == 1
        assert "missing_since IS NOT NULL" in clauses[0]
        assert True in params.values()

    def test_days_in_grace_gte_filters_numeric(self, repo):
        clauses, params = repo._build_filter_clauses(
            [_virtual_filter("days_in_grace", "gte", 20)]
        )
        assert len(clauses) == 1
        assert "FLOOR(EXTRACT(EPOCH FROM (now() - missing_since)) / 86400)::int" in clauses[0]
        assert ">=" in clauses[0]
        assert 20 in params.values()

    def test_grace_fields_project_as_columns(self, repo):
        cols = repo._build_select_columns(["virtual:in_grace", "virtual:days_in_grace"])
        assert "(missing_since IS NOT NULL)" in cols
        assert "virtual:in_grace" in cols
        assert "FLOOR(EXTRACT(EPOCH FROM (now() - missing_since)) / 86400)::int" in cols
        assert "virtual:days_in_grace" in cols
