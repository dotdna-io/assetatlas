# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests: confidence_level='low' filter matches NULL-confidence assets.

WHY: NULL-confidence assets must bucket as 'low' everywhere.
- The dashboard CASE expression uses ELSE 'low', absorbing NULLs into low.
- The confidence_level projection CASE also puts NULL → low.
- The Explorer filter and charts filter must agree, or the same label 'low'
  describes a different population in different surfaces — a silent count
  discrepancy that breaks cross-surface consistency.

Both _build_confidence_level_clause (asset_repository) and _build_cl_condition
(charts query_builder) must emit (confidence < %(...)s OR confidence IS NULL)
for the 'low' level.
"""
import importlib
from unittest.mock import MagicMock

# Break circular import: import charts models before query_builder to prevent
# the partial-module cycle (same pattern used throughout tests/unit/charts/).
_models_mod = importlib.import_module("src.api.blueprints.charts.models")
FilterOperator = _models_mod.FilterOperator

from src.modules.charts.services.query_builder import QueryBuilder  # noqa: E402


def test_asset_repo_low_clause_matches_null():
    """_build_confidence_level_clause for 'low' must include OR confidence IS NULL."""
    from src.modules.assets.repositories.asset_repository import AssetRepository

    repo = AssetRepository(MagicMock(), resolver=MagicMock())
    params = {}
    clause = repo._build_confidence_level_clause('eq', 'low', 0.45, 0.70, 'f0', params)

    assert clause is not None, "_build_confidence_level_clause returned None for 'low'"
    assert 'IS NULL' in clause, (
        f"Expected 'IS NULL' in low clause to capture NULL-confidence assets, got: {clause!r}"
    )
    assert '<' in clause, (
        f"Expected '<' (numeric bound) in low clause, got: {clause!r}"
    )
    # Param must be set to the medium threshold
    assert 'f0_eq_l' in params, f"Expected param key 'f0_eq_l' in params, got: {params}"
    assert params['f0_eq_l'] == 0.45


def test_charts_low_clause_matches_null():
    """_build_cl_condition for 'low' must include OR confidence IS NULL."""
    from src.modules.fields import FieldCatalog, FieldResolver

    db = MagicMock()
    db.execute_query.return_value = []
    catalog = FieldCatalog(db_adapter=db)
    resolver = FieldResolver(catalog=catalog)

    qb = QueryBuilder(confidence_thresholds=(0.45, 0.70), resolver=resolver)
    params = {}

    frag = qb._build_cl_condition(FilterOperator.EQUALS, 'low', 'p0', params)

    assert frag is not None, "_build_cl_condition returned None for 'low'"
    assert 'IS NULL' in frag, (
        f"Expected 'IS NULL' in low clause to capture NULL-confidence assets, got: {frag!r}"
    )
    assert '<' in frag, (
        f"Expected '<' (numeric bound) in low clause, got: {frag!r}"
    )
    # Param must be set to the medium threshold
    assert 'p0_eq_l' in params, f"Expected param key 'p0_eq_l' in params, got: {params}"
    assert params['p0_eq_l'] == 0.45
