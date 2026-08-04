# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for _build_cl_condition in QueryBuilder (confidence_level virtual field).

Covers EQUALS, NOT_EQUALS, IN operators for each level (high, medium, low),
custom thresholds, invalid levels, and unsupported operators. Also guards
that virtual fields are rejected in SELECT slots.
"""

import importlib
import pytest
from unittest.mock import MagicMock

# Break circular import (same pattern as test_query_builder.py)
_models_mod = importlib.import_module("src.api.blueprints.charts.models")

FilterOperator = _models_mod.FilterOperator
ChartQueryRequest = _models_mod.ChartQueryRequest
QueryType = _models_mod.QueryType
SelectClause = _models_mod.SelectClause
FromClause = _models_mod.FromClause
QueryOptions = _models_mod.QueryOptions
FilterCondition = _models_mod.FilterCondition

from src.modules.charts.services.query_builder import QueryBuilder
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.models import StaticField, VirtualField


def _sf(name: str) -> StaticField:
    return StaticField(kind="static", name=name)


def _vf() -> VirtualField:
    return VirtualField(kind="virtual", name="confidence_level")


def _make_resolver():
    db = MagicMock()
    db.execute_query.return_value = []
    catalog = FieldCatalog(db_adapter=db)
    return FieldResolver(catalog=catalog)


class TestBuildClCondition:
    """Tests for QueryBuilder._build_cl_condition."""

    @pytest.fixture
    def builder(self):
        return QueryBuilder(confidence_thresholds=(0.45, 0.70), resolver=_make_resolver())

    @pytest.fixture
    def default_builder(self):
        return QueryBuilder(resolver=_make_resolver())

    # ------------------------------------------------------------------
    # EQUALS operator
    # ------------------------------------------------------------------
    def test_equals_high(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.EQUALS, "high", "p", params
        )
        assert result is not None
        assert "confidence >= %(p_eq_h)s" in result
        assert params["p_eq_h"] == 0.70

    def test_equals_medium(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.EQUALS, "medium", "p", params
        )
        assert result is not None
        assert "confidence >= %(p_eq_m)s" in result
        assert "confidence < %(p_eq_mh)s" in result
        assert params["p_eq_m"] == 0.45
        assert params["p_eq_mh"] == 0.70

    def test_equals_low(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.EQUALS, "low", "p", params
        )
        assert result is not None
        assert "confidence < %(p_eq_l)s" in result
        assert params["p_eq_l"] == 0.45

    # ------------------------------------------------------------------
    # NOT_EQUALS operator
    # ------------------------------------------------------------------
    def test_not_equals_high(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.NOT_EQUALS, "high", "p", params
        )
        assert result is not None
        assert result.startswith("NOT (")
        assert "confidence >= %(p_neq_h)s" in result
        assert params["p_neq_h"] == 0.70

    def test_not_equals_medium(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.NOT_EQUALS, "medium", "p", params
        )
        assert result is not None
        assert result.startswith("NOT (")
        assert "confidence >= %(p_neq_m)s" in result
        assert "confidence < %(p_neq_mh)s" in result

    def test_not_equals_low(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.NOT_EQUALS, "low", "p", params
        )
        assert result is not None
        assert result.startswith("NOT (")
        assert "confidence < %(p_neq_l)s" in result

    # ------------------------------------------------------------------
    # IN operator
    # ------------------------------------------------------------------
    def test_in_multiple_levels(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.IN, ["high", "low"], "p", params
        )
        assert result is not None
        assert "OR" in result
        assert "p_in0_h" in params
        assert "p_in1_l" in params

    def test_in_single_level(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.IN, ["medium"], "p", params
        )
        assert result is not None
        assert "confidence >= %(p_in0_m)s" in result
        assert params["p_in0_m"] == 0.45

    def test_in_single_value_not_list(self, builder):
        """IN with a single string (not list) still works."""
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.IN, "high", "p", params
        )
        assert result is not None
        assert "confidence >= %(p_in0_h)s" in result

    # ------------------------------------------------------------------
    # Invalid / unrecognized level
    # ------------------------------------------------------------------
    def test_equals_invalid_level_returns_none(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.EQUALS, "unknown_level", "p", params
        )
        assert result is None

    def test_not_equals_invalid_level_returns_none(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.NOT_EQUALS, "bogus", "p", params
        )
        assert result is None

    def test_in_all_invalid_levels_returns_none(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.IN, ["bogus", "invalid"], "p", params
        )
        assert result is None

    # ------------------------------------------------------------------
    # Custom thresholds
    # ------------------------------------------------------------------
    def test_custom_thresholds(self):
        builder = QueryBuilder(confidence_thresholds=(0.30, 0.80), resolver=_make_resolver())
        params = {}
        builder._build_cl_condition(
            FilterOperator.EQUALS, "high", "p", params
        )
        assert params["p_eq_h"] == 0.80

        params2 = {}
        builder._build_cl_condition(
            FilterOperator.EQUALS, "low", "p", params2
        )
        assert params2["p_eq_l"] == 0.30

    def test_default_thresholds(self, default_builder):
        params = {}
        default_builder._build_cl_condition(
            FilterOperator.EQUALS, "high", "p", params
        )
        assert params["p_eq_h"] == 0.70

        params2 = {}
        default_builder._build_cl_condition(
            FilterOperator.EQUALS, "low", "p", params2
        )
        assert params2["p_eq_l"] == 0.45

    # ------------------------------------------------------------------
    # Unsupported operator
    # ------------------------------------------------------------------
    def test_unsupported_operator_returns_none(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.CONTAINS, "high", "p", params
        )
        assert result is None

    def test_gt_operator_returns_none(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.GT, "high", "p", params
        )
        assert result is None

    def test_between_operator_returns_none(self, builder):
        params = {}
        result = builder._build_cl_condition(
            FilterOperator.BETWEEN, ["low", "high"], "p", params
        )
        assert result is None


class TestVirtualFieldGuard:
    """Tests that virtual fields are rejected in SELECT clauses."""

    @pytest.fixture
    def builder(self):
        return QueryBuilder(resolver=_make_resolver())

    def _make_request(self, **overrides):
        defaults = dict(
            queryType=QueryType.DISTRIBUTION,
            from_=FromClause(table="assets"),
            select=SelectClause(dimension=_sf("Category")),
            filters=[],
            options=QueryOptions(),
        )
        defaults.update(overrides)
        data = {k if k != "from_" else "from": v for k, v in defaults.items()}
        return ChartQueryRequest.model_validate(data)

    def test_confidence_level_as_dimension_rejected(self, builder):
        req = self._make_request(
            select=SelectClause(dimension=_vf()),
        )
        with pytest.raises(ValueError, match="[Vv]irtual field"):
            builder.build_query(req)

    def test_confidence_level_as_xaxis_rejected(self, builder):
        req = self._make_request(
            queryType=QueryType.COMPARISON,
            select=SelectClause(xAxis=_vf(), yAxis=_sf("Category")),
        )
        with pytest.raises(ValueError, match="[Vv]irtual field"):
            builder.build_query(req)

    def test_confidence_level_as_yaxis_rejected(self, builder):
        req = self._make_request(
            queryType=QueryType.COMPARISON,
            select=SelectClause(xAxis=_sf("Category"), yAxis=_vf()),
        )
        with pytest.raises(ValueError, match="[Vv]irtual field"):
            builder.build_query(req)

    def test_confidence_level_as_source_rejected(self, builder):
        req = self._make_request(
            queryType=QueryType.CORRELATION,
            select=SelectClause(sourceField=_vf(), targetField=_sf("Category")),
        )
        with pytest.raises(ValueError, match="[Vv]irtual field"):
            builder.build_query(req)

    def test_confidence_level_as_target_rejected(self, builder):
        req = self._make_request(
            queryType=QueryType.CORRELATION,
            select=SelectClause(sourceField=_sf("Category"), targetField=_vf()),
        )
        with pytest.raises(ValueError, match="[Vv]irtual field"):
            builder.build_query(req)

    def test_confidence_level_in_filter_still_works(self, builder):
        """confidence_level is allowed in filters, just not in SELECT."""
        req = self._make_request(
            filters=[
                FilterCondition(
                    field_id=_vf(),
                    operator=FilterOperator.EQUALS,
                    value="high",
                ),
            ],
        )
        sql, params = builder.build_query(req)
        assert "confidence >=" in sql
