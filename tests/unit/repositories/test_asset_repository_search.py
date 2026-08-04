# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Explorer keyword-search clause builder in AssetRepository.

Regression: keyword search built a per-field ``unnest(...) ILIKE '%term%'`` OR
across ~15 columns plus ``extended_attributes::text ILIKE``, which no index can
serve — every search was two full sequential scans (slow / timeouts at scale,
observed on a large production deployment). The search now targets a single
consolidated text expression that a pg_trgm GIN index accelerates (see
migration ``112_add_assets_search_trgm_index.sql``).
"""

import re
from unittest.mock import MagicMock

import pytest

from src.modules.assets.repositories.asset_repository import (
    AssetRepository,
    _ASSET_SEARCH_EXPR,
)
from src.modules.fields import FieldCatalog, FieldResolver


def _make_resolver():
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


class TestKeywordSearchClause:
    @pytest.fixture
    def repo(self):
        return AssetRepository(db_adapter=MagicMock(), resolver=_make_resolver())

    def test_uses_indexable_expression_not_unnest(self, repo):
        """The clause targets the consolidated expression the trigram index
        serves — not the per-field unnest scan no index can use."""
        clauses, params = repo._build_search_clause('server')
        joined = ' '.join(clauses)
        assert _ASSET_SEARCH_EXPR in joined
        assert "asset_search_join(hostname" in joined
        assert "extended_attributes::text" in joined
        assert "unnest(" not in joined
        assert params['search_0'] == '%server%'

    def test_tokens_are_anded(self, repo):
        """Each whitespace token is its own predicate so extra words narrow."""
        clauses, params = repo._build_search_clause('windows server')
        assert len(clauses) == 2
        assert params['search_0'] == '%windows%'
        assert params['search_1'] == '%server%'

    def test_token_count_capped(self, repo):
        """Tokens are capped so a huge query can't explode the WHERE clause."""
        clauses, _ = repo._build_search_clause(' '.join(f'w{i}' for i in range(20)))
        assert len(clauses) == 8

    def test_whitespace_only_falls_back_to_single_predicate(self, repo):
        """A truthy but token-less search still yields exactly one predicate."""
        clauses, params = repo._build_search_clause('   ')
        assert len(clauses) == 1
        assert params['search_0'] == '%   %'

    def test_term_is_parameterized_not_interpolated(self, repo):
        """The user term is only ever a bound parameter (no SQLi surface)."""
        clauses, params = repo._build_search_clause("o'brien")
        assert "o'brien" not in ' '.join(clauses)
        assert params['search_0'] == "%o'brien%"


class TestSearchAssetsProjection:
    """The chat/MCP ``search_assets`` result is rendered into the same table as
    ``list_assets`` (artifacts._LIST_ASSET_COLUMNS). Unlike list_assets, search
    uses a hand-written SELECT, so it must list every displayed column itself or
    those cells render blank for every row (#742 sibling)."""

    def test_select_includes_every_displayed_column(self):
        db = MagicMock()
        db.execute_query.return_value = []
        repo = AssetRepository(db_adapter=db, resolver=_make_resolver())

        repo.search_assets("host01")

        sql = db.execute_query.call_args[0][0]
        # Assert the invariant, not today's column list: EVERY column the result
        # table renders (artifacts._LIST_ASSET_COLUMNS) must be SELECTed, so a
        # future 9th displayed column trips this until search's SQL adds it too.
        from src.api.blueprints.chat.artifacts import _LIST_ASSET_COLUMNS
        for col, _label in _LIST_ASSET_COLUMNS:
            assert re.search(rf"\b{col}\b", sql), \
                f"search_assets SELECT omits displayed column {col!r}"
