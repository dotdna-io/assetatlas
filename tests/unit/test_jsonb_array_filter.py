# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for JSONB array filter handling in AssetRepository.

Exercises `_build_single_filter` directly and the full `_build_filter_clauses`
path via typed FieldId inputs (CoreEnrichmentField for Private IP / IP Type).

`_build_single_filter` now routes (scalar / native-array / jsonb-array) off the
ResolvedField carried in `repo._current_resolved`, not the legacy
is_array/is_jsonb booleans. Direct callers therefore prime `_current_resolved`
via the real resolver before calling it (see `_filter`). The registry names
params `<key>_<n>` (so param_key='p0' yields 'p0_0') — that suffix is an
internal detail; the asserted clause text is the contract.
"""

import pytest
from unittest.mock import MagicMock

from src.modules.assets.repositories.asset_repository import AssetRepository
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.models import CoreEnrichmentField


def _make_resolver():
    """Resolver backed by a catalog with static + core_enrichment entries (no DB hits)."""
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


def _core_filter(name, op, value=None):
    return {
        'field_id': {'kind': 'core_enrichment', 'name': name},
        'op': op,
        'value': value,
    }


class TestBuildSingleFilterJsonbArray:
    """_build_single_filter uses jsonb_array_elements_text for JSONB array columns.

    Routing is decided from the ResolvedField in `repo._current_resolved`, so
    `_filter` resolves the real field whose where_expr() matches the requested
    column (Private IP -> JSONB_ARRAY_COL, IP Type -> JSONB_SCALAR_COL) and
    primes `_current_resolved` before calling the builder.
    """

    JSONB_ARRAY_COL = "extended_attributes->'AssetAtlas'->'ip_private'"
    JSONB_SCALAR_COL = "extended_attributes->'AssetAtlas'->>'ip_type'"

    # Map each column constant to the real catalog field that resolves to it.
    # "Private IP" is JSONB_ARRAY (-> arrow), "IP Type" is STRING (->> arrow).
    _COL_TO_FIELD = {
        JSONB_ARRAY_COL: CoreEnrichmentField(kind='core_enrichment', name='Private IP'),
        JSONB_SCALAR_COL: CoreEnrichmentField(kind='core_enrichment', name='IP Type'),
    }

    @pytest.fixture
    def repo(self):
        return AssetRepository(db_adapter=MagicMock(), resolver=_make_resolver())

    def _filter(self, repo, col, op, value=None):
        # Prime the ResolvedField the registry routes off. We verify the chosen
        # field's where_expr() equals the column under test so routing and the
        # asserted SQL stay in lockstep with the resolver.
        resolved = repo._resolver.resolve(self._COL_TO_FIELD[col])
        assert resolved.where_expr() == col
        repo._current_resolved = resolved
        params = {}
        clause = repo._build_single_filter(
            col=col,
            is_array=resolved.is_native_array(),
            is_jsonb=resolved.is_jsonb_sourced(),
            op=op,
            value=value,
            param_key='p0',
            params=params,
        )
        return clause, params

    def test_eq_on_jsonb_array(self, repo):
        clause, params = self._filter(repo, self.JSONB_ARRAY_COL, 'eq', '10.0.1.5')
        assert clause is not None
        assert 'jsonb_array_elements_text' in clause
        assert 'x.value' in clause
        assert params['p0_0'] == '10.0.1.5'

    def test_contains_on_jsonb_array(self, repo):
        clause, params = self._filter(repo, self.JSONB_ARRAY_COL, 'contains', '10.0')
        assert 'jsonb_array_elements_text' in clause
        assert 'ILIKE' in clause
        assert '%10.0%' in params['p0_0']

    def test_starts_on_jsonb_array(self, repo):
        clause, params = self._filter(repo, self.JSONB_ARRAY_COL, 'starts', '192.168')
        assert 'jsonb_array_elements_text' in clause
        assert 'ILIKE' in clause
        assert params['p0_0'].startswith('192.168')

    def test_ends_on_jsonb_array(self, repo):
        clause, params = self._filter(repo, self.JSONB_ARRAY_COL, 'ends', '.1')
        assert 'jsonb_array_elements_text' in clause
        assert params['p0_0'] == '%.1'

    def test_neq_on_jsonb_array(self, repo):
        clause, params = self._filter(repo, self.JSONB_ARRAY_COL, 'neq', '10.0.1.5')
        assert 'jsonb_array_elements_text' in clause
        assert 'NOT EXISTS' in clause

    def test_empty_on_jsonb_array(self, repo):
        clause, params = self._filter(repo, self.JSONB_ARRAY_COL, 'empty')
        assert 'jsonb_array_length' in clause or 'IS NULL' in clause

    def test_notempty_on_jsonb_array(self, repo):
        clause, params = self._filter(repo, self.JSONB_ARRAY_COL, 'notempty')
        assert 'IS NOT NULL' in clause

    def test_any_on_jsonb_array(self, repo):
        clause, params = self._filter(repo, self.JSONB_ARRAY_COL, 'any', ['10.0.1.5', '192.168.1.1'])
        assert 'jsonb_array_elements_text' in clause
        assert len(params) == 2

    def test_scalar_jsonb_not_affected(self, repo):
        """Scalar JSONB fields still produce simple comparison, not jsonb_array_elements_text."""
        clause, params = self._filter(repo, self.JSONB_SCALAR_COL, 'eq', 'private')
        assert 'jsonb_array_elements_text' not in clause
        # String eq is case-insensitive — comparison is LOWER()-wrapped on
        # both sides. The invariant this test protects (scalar JSONB doesn't
        # route through the array-unnest path) still holds.
        assert clause == (
            f"LOWER({self.JSONB_SCALAR_COL}) = LOWER(%(p0_0)s)"
        )


class TestBuildFilterClausesIntegration:
    """Integration: _build_filter_clauses routes JSONB array fields correctly.

    Uses typed CoreEnrichmentField FieldIds; the resolver picks the right
    SQL expression (-> vs ->>) from the catalog based on data type.
    """

    @pytest.fixture
    def repo(self):
        return AssetRepository(db_adapter=MagicMock(), resolver=_make_resolver())

    def test_ip_private_filter_uses_jsonb_array_elements(self, repo):
        """CoreEnrichmentField("Private IP") is JSONB_ARRAY → jsonb_array_elements_text."""
        filters = [_core_filter('Private IP', 'contains', '10.0')]
        clauses, params = repo._build_filter_clauses(filters)
        assert len(clauses) == 1
        assert 'jsonb_array_elements_text' in clauses[0]

    def test_ip_type_filter_uses_scalar(self, repo):
        """CoreEnrichmentField("IP Type") is STRING → plain text comparison."""
        filters = [_core_filter('IP Type', 'eq', 'private')]
        clauses, params = repo._build_filter_clauses(filters)
        assert len(clauses) == 1
        assert 'jsonb_array_elements_text' not in clauses[0]
