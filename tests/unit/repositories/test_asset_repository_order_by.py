# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for _build_order_by deterministic tiebreaker in AssetRepository.

Every ORDER BY branch must append `id ASC` as a unique tiebreaker so that
paginated LIMIT/OFFSET queries are deterministic across page boundaries.
Without it, PostgreSQL may return different orderings for tied rows across
separate executions, causing multi-page exports to skip or duplicate rows.
"""

import pytest
from unittest.mock import MagicMock

from src.modules.assets.repositories.asset_repository import AssetRepository
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.encoding import encode as encode_field_id
from src.modules.fields.models import StaticField


def _make_resolver():
    """Resolver backed by a catalog with static + virtual entries (no DB hits)."""
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


@pytest.fixture
def repo():
    return AssetRepository(db_adapter=MagicMock(), resolver=_make_resolver())


class TestBuildOrderByTiebreaker:
    """_build_order_by must always emit `id ASC` as the last tiebreaker."""

    def test_default_no_sort_by_has_pk_tiebreaker(self, repo):
        """No sort_by → default last_time DESC NULLS LAST, id ASC."""
        order_sql = repo._build_order_by(None, None)
        assert "id ASC" in order_sql
        # Primary sort is preserved
        assert "last_time DESC NULLS LAST" in order_sql

    def test_explicit_sort_by_has_pk_tiebreaker(self, repo):
        """Explicit sort_by → primary sort preserved, id ASC appended."""
        encoded = encode_field_id(StaticField(kind="static", name="Category"))
        order_sql = repo._build_order_by(encoded, "asc")
        assert "id ASC" in order_sql
        # Primary sort direction preserved
        assert "ASC" in order_sql

    def test_explicit_sort_desc_has_pk_tiebreaker(self, repo):
        """DESC sort direction preserves tiebreaker."""
        encoded = encode_field_id(StaticField(kind="static", name="Hostname"))
        order_sql = repo._build_order_by(encoded, "desc")
        assert "id ASC" in order_sql
        assert "DESC" in order_sql

    def test_pk_tiebreaker_is_last_term(self, repo):
        """`id ASC` must be the final ORDER BY term so it only breaks ties."""
        order_sql = repo._build_order_by(None, None)
        # Strip leading "ORDER BY " and split on commas
        terms = [t.strip() for t in order_sql[len("ORDER BY "):].split(",")]
        assert terms[-1] == "id ASC"

    def test_explicit_sort_pk_tiebreaker_is_last_term(self, repo):
        """`id ASC` is last term for explicit sort_by too."""
        encoded = encode_field_id(StaticField(kind="static", name="Category"))
        order_sql = repo._build_order_by(encoded, "asc")
        terms = [t.strip() for t in order_sql[len("ORDER BY "):].split(",")]
        assert terms[-1] == "id ASC"
