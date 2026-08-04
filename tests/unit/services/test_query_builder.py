# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for QueryBuilder (charts)

The FieldResolver is the single authority for field translation; these tests
use a resolver backed by a mocked FieldCatalog that returns no dynamic fields
(so only the hardcoded static/core_enrichment/virtual catalog entries exist).

NOTE: The chart models module and query_builder module have a circular import
through src.api.blueprints.__init__.py.  We break the cycle by pre-loading
the models module before importing QueryBuilder.
"""

import importlib
import pytest
from pydantic import ValidationError
from unittest.mock import MagicMock

# Break circular import: load the chart models module directly before
# the blueprints __init__.py gets a chance to trigger the cycle.
_models_mod = importlib.import_module("src.api.blueprints.charts.models")

ChartQueryRequest = _models_mod.ChartQueryRequest
QueryType = _models_mod.QueryType
FilterOperator = _models_mod.FilterOperator
SelectClause = _models_mod.SelectClause
FromClause = _models_mod.FromClause
QueryOptions = _models_mod.QueryOptions
FilterCondition = _models_mod.FilterCondition
MetricSpec = _models_mod.MetricSpec
AggregateFunction = _models_mod.AggregateFunction
SortOrder = _models_mod.SortOrder
TimeInterval = _models_mod.TimeInterval
SecurityWhitelists = _models_mod.SecurityWhitelists

from src.modules.charts.services.query_builder import QueryBuilder
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.models import StaticField


def _make_resolver():
    db = MagicMock()
    db.execute_query.return_value = []   # no dynamic fields (extended_attributes, tags, posture)
    catalog = FieldCatalog(db_adapter=db)
    return FieldResolver(catalog=catalog)


def _sf(name: str) -> StaticField:
    """Shortcut for StaticField construction."""
    return StaticField(kind="static", name=name)


def _metric_count() -> MetricSpec:
    return MetricSpec(agg=AggregateFunction.COUNT)


def _metric_avg_confidence() -> MetricSpec:
    return MetricSpec(agg=AggregateFunction.AVG, field_id=_sf("Confidence Score"))


def _make_request(**overrides):
    """Build a minimal ChartQueryRequest with sensible defaults."""
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


class TestQueryBuilder:
    """Tests for QueryBuilder."""

    @pytest.fixture
    def builder(self):
        return QueryBuilder(resolver=_make_resolver())

    # ------------------------------------------------------------------
    # Constructor guards
    # ------------------------------------------------------------------
    def test_requires_resolver(self):
        with pytest.raises(ValueError, match="FieldResolver"):
            QueryBuilder()

    # ------------------------------------------------------------------
    # Distribution queries
    # ------------------------------------------------------------------
    def test_distribution_basic(self, builder):
        req = _make_request()
        sql, params = builder.build_query(req)

        assert isinstance(sql, str)
        assert isinstance(params, dict)
        assert "GROUP BY" in sql
        assert "category" in sql
        assert "COUNT(*)" in sql
        assert "FROM assets" in sql
        assert "%(limit)s" in sql
        assert "WITH ranked AS" in sql
        assert "'Other'" in sql

    def test_distribution_include_null(self, builder):
        req = _make_request(
            options=QueryOptions(includeNull=True),
        )
        sql, params = builder.build_query(req)

        assert "COALESCE" in sql or "(Empty)" in sql

    def test_distribution_exclude_null(self, builder):
        req = _make_request(
            options=QueryOptions(includeNull=False),
        )
        sql, params = builder.build_query(req)

        assert "IS NOT NULL" in sql

    def test_distribution_limit_uses_request_limit(self, builder):
        """Distribution queries respect the requested limit."""
        req = _make_request(options=QueryOptions(limit=8))
        sql, params = builder.build_query(req)

        assert params["limit"] == 8

    # ------------------------------------------------------------------
    # Metric queries
    # ------------------------------------------------------------------
    def test_metric_count(self, builder):
        req = _make_request(
            queryType=QueryType.METRIC,
            select=SelectClause(metric=_metric_count()),
        )
        sql, params = builder.build_query(req)

        assert "COUNT(*)" in sql
        assert "as value" in sql
        assert "GROUP BY" not in sql

    def test_metric_avg_confidence(self, builder):
        req = _make_request(
            queryType=QueryType.METRIC,
            select=SelectClause(metric=_metric_avg_confidence()),
        )
        sql, params = builder.build_query(req)

        assert "AVG(confidence)" in sql

    def test_metric_distinct_scalar_field(self, builder):
        """Distinct metric on a scalar field uses COUNT(DISTINCT field)."""
        req = _make_request(
            queryType=QueryType.METRIC,
            select=SelectClause(metric=_metric_count(), dimension=_sf("Category")),
            options=QueryOptions(metricType="distinct"),
        )
        sql, params = builder.build_query(req)

        assert "COUNT(DISTINCT category)" in sql
        assert "as value" in sql
        assert "GROUP BY" not in sql

    def test_metric_distinct_array_field(self, builder):
        """Distinct metric on an array field uses field[1]."""
        req = _make_request(
            queryType=QueryType.METRIC,
            select=SelectClause(metric=_metric_count(), dimension=_sf("Manufacturer")),
            options=QueryOptions(metricType="distinct"),
        )
        sql, params = builder.build_query(req)

        assert "COUNT(DISTINCT manufacturer[1])" in sql
        assert "manufacturer IS NOT NULL" in sql
        assert "array_length(manufacturer, 1) > 0" in sql

    def test_metric_distinct_without_dimension_rejected(self):
        """Distinct metric without dimension is now rejected at request
        validation (ChartQueryRequest.validate_query_type_requirements), so it
        never reaches the QueryBuilder to silently fall back to COUNT(*)."""
        with pytest.raises(ValidationError, match="distinct"):
            _make_request(
                queryType=QueryType.METRIC,
                select=SelectClause(metric=_metric_count()),
                options=QueryOptions(metricType="distinct"),
            )

    # ------------------------------------------------------------------
    # Trend queries
    # ------------------------------------------------------------------
    def test_trend_basic(self, builder):
        req = _make_request(
            queryType=QueryType.TREND,
            select=SelectClause(timeField=_sf("Last Seen")),
            options=QueryOptions(interval=TimeInterval.DAY),
        )
        sql, params = builder.build_query(req)

        assert "DATE_TRUNC" in sql
        assert "last_seen" in sql
        assert "ORDER BY timestamp ASC" in sql

    def test_trend_with_dimension(self, builder):
        req = _make_request(
            queryType=QueryType.TREND,
            select=SelectClause(timeField=_sf("Last Seen"), dimension=_sf("Category")),
            options=QueryOptions(interval=TimeInterval.MONTH),
        )
        sql, params = builder.build_query(req)

        assert "category" in sql
        assert "as series" in sql

    def test_trend_with_time_range(self, builder):
        req = _make_request(
            queryType=QueryType.TREND,
            select=SelectClause(timeField=_sf("Last Seen")),
            options=QueryOptions(interval=TimeInterval.DAY, last="30d"),
        )
        sql, params = builder.build_query(req)

        assert "INTERVAL" in sql
        assert "30 days" in sql

    def test_trend_non_time_field_rejected(self, builder):
        """timeField must resolve to a TIMESTAMP/DATE data type."""
        req = _make_request(
            queryType=QueryType.TREND,
            select=SelectClause(timeField=_sf("Category")),  # STRING
            options=QueryOptions(interval=TimeInterval.DAY),
        )
        with pytest.raises(ValueError, match="timeField"):
            builder.build_query(req)

    # ------------------------------------------------------------------
    # Comparison queries
    # ------------------------------------------------------------------
    def test_comparison_basic(self, builder):
        req = _make_request(
            queryType=QueryType.COMPARISON,
            select=SelectClause(xAxis=_sf("Category"), yAxis=_sf("OS Normalized")),
        )
        sql, params = builder.build_query(req)

        assert "category" in sql
        assert "os_normalized" in sql
        assert "x_axis" in sql
        assert "y_axis" in sql
        assert "GROUP BY" in sql

    def test_comparison_with_segment_limit(self, builder):
        """segmentLimit should use CTE-based ranking with Other aggregation."""
        req = _make_request(
            queryType=QueryType.COMPARISON,
            select=SelectClause(xAxis=_sf("Category"), yAxis=_sf("OS Normalized")),
            options=QueryOptions(segmentLimit=5),
        )
        sql, params = builder.build_query(req)

        assert "ROW_NUMBER()" in sql
        assert "top_segments" in sql
        assert "'Other'" in sql
        assert params["segment_limit"] == 5

    # ------------------------------------------------------------------
    # Correlation queries
    # ------------------------------------------------------------------
    def test_correlation_basic(self, builder):
        req = _make_request(
            queryType=QueryType.CORRELATION,
            select=SelectClause(sourceField=_sf("Category"), targetField=_sf("OS Normalized")),
        )
        sql, params = builder.build_query(req)

        assert "source" in sql
        assert "target" in sql
        assert "GROUP BY" in sql

    def test_correlation_array_field_uses_first_element(self, builder):
        """Array fields (like data_source) should use field[1]."""
        req = _make_request(
            queryType=QueryType.CORRELATION,
            select=SelectClause(sourceField=_sf("Category"), targetField=_sf("Data Source")),
        )
        sql, params = builder.build_query(req)

        assert "data_source[1]" in sql
        # Non-array field should NOT use [1]
        assert "category[1]" not in sql
        assert "category" in sql

    def test_correlation_both_array_fields(self, builder):
        req = _make_request(
            queryType=QueryType.CORRELATION,
            select=SelectClause(sourceField=_sf("Hostname"), targetField=_sf("IP Address")),
        )
        sql, params = builder.build_query(req)

        assert "hostname[1]" in sql
        assert "ip[1]" in sql

    # ------------------------------------------------------------------
    # Filter handling
    # ------------------------------------------------------------------
    def test_filter_equals(self, builder):
        req = _make_request(
            filters=[
                FilterCondition(field_id=_sf("Category"),
                                operator=FilterOperator.EQUALS, value="Server"),
            ],
        )
        sql, params = builder.build_query(req)

        assert "WHERE" in sql
        assert "%(filter_0_value)s" in sql
        assert params["filter_0_value"] == "Server"

    def test_filter_gte(self, builder):
        req = _make_request(
            filters=[
                FilterCondition(field_id=_sf("Confidence Score"),
                                operator=FilterOperator.GTE, value=80),
            ],
        )
        sql, params = builder.build_query(req)

        assert ">=" in sql
        assert params["filter_0_value"] == 80

    def test_filter_contains(self, builder):
        req = _make_request(
            filters=[
                FilterCondition(field_id=_sf("Category"),
                                operator=FilterOperator.CONTAINS, value="work"),
            ],
        )
        sql, params = builder.build_query(req)

        assert "ILIKE" in sql
        assert params["filter_0_value"] == "%work%"

    def test_filter_is_null(self, builder):
        req = _make_request(
            filters=[
                FilterCondition(field_id=_sf("Category"), operator=FilterOperator.IS_NULL),
            ],
        )
        sql, params = builder.build_query(req)

        assert "IS NULL" in sql

    def test_filter_is_null_array_field(self, builder):
        """Array fields have extra NULL/empty checks."""
        req = _make_request(
            filters=[
                FilterCondition(field_id=_sf("Hostname"), operator=FilterOperator.IS_NULL),
            ],
        )
        sql, params = builder.build_query(req)

        assert "hostname IS NULL" in sql
        assert "array_length" in sql

    def test_filter_in_operator(self, builder):
        req = _make_request(
            filters=[
                FilterCondition(
                    field_id=_sf("Category"),
                    operator=FilterOperator.IN,
                    value=["Server", "Desktop"],
                ),
            ],
        )
        sql, params = builder.build_query(req)

        assert "ANY" in sql
        assert params["filter_0_value"] == ["Server", "Desktop"]

    def test_filter_between(self, builder):
        req = _make_request(
            filters=[
                FilterCondition(
                    field_id=_sf("Confidence Score"),
                    operator=FilterOperator.BETWEEN,
                    value=[50, 100],
                ),
            ],
        )
        sql, params = builder.build_query(req)

        assert "BETWEEN" in sql
        assert params["filter_0_value_min"] == 50
        assert params["filter_0_value_max"] == 100

    def test_multiple_filters_combined_with_and(self, builder):
        req = _make_request(
            filters=[
                FilterCondition(field_id=_sf("Category"),
                                operator=FilterOperator.EQUALS, value="Server"),
                FilterCondition(field_id=_sf("Confidence Score"),
                                operator=FilterOperator.GTE, value=70),
            ],
        )
        sql, params = builder.build_query(req)

        assert "AND" in sql
        assert "filter_0_value" in params
        assert "filter_1_value" in params

    def test_no_filters_no_where(self, builder):
        req = _make_request(filters=[])
        sql, params = builder.build_query(req)

        # WHERE should only appear for non-null exclusion, not user filters.
        assert "filter_0_value" not in params

    # ------------------------------------------------------------------
    # Array field handling in filters
    # ------------------------------------------------------------------
    def test_array_field_filter_uses_first_element(self, builder):
        """Array field comparisons use field[1] syntax."""
        req = _make_request(
            filters=[
                FilterCondition(field_id=_sf("Hostname"),
                                operator=FilterOperator.EQUALS, value="web01"),
            ],
        )
        sql, params = builder.build_query(req)

        assert "hostname[1]" in sql

    # ------------------------------------------------------------------
    # Return type validation
    # ------------------------------------------------------------------
    def test_returns_tuple(self, builder):
        req = _make_request()
        result = builder.build_query(req)

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], dict)

    # ------------------------------------------------------------------
    # Time range parsing
    # ------------------------------------------------------------------
    def test_parse_time_range_days(self, builder):
        assert builder._parse_time_range("7d") == "7 days"

    def test_parse_time_range_hours(self, builder):
        assert builder._parse_time_range("24h") == "24 hours"

    def test_parse_time_range_weeks(self, builder):
        assert builder._parse_time_range("2w") == "2 weeks"

    def test_parse_time_range_months(self, builder):
        assert builder._parse_time_range("3m") == "3 months"

    def test_parse_time_range_invalid(self, builder):
        """Invalid format defaults to 30 days."""
        assert builder._parse_time_range("abc") == "30 days"

    # ------------------------------------------------------------------
    # Metric translation (MetricSpec)
    # ------------------------------------------------------------------
    def test_translate_count(self, builder):
        assert builder._translate_metric_to_sql(
            MetricSpec(agg=AggregateFunction.COUNT)
        ) == "COUNT(*)"

    def test_translate_avg_confidence(self, builder):
        assert builder._translate_metric_to_sql(
            MetricSpec(agg=AggregateFunction.AVG, field_id=_sf("Confidence Score"))
        ) == "AVG(confidence)"

    def test_translate_count_distinct(self, builder):
        assert builder._translate_metric_to_sql(
            MetricSpec(agg=AggregateFunction.COUNT_DISTINCT, field_id=_sf("Category"))
        ) == "COUNT(DISTINCT category)"

    def test_translate_sum_requires_field(self, builder):
        with pytest.raises(ValueError, match="requires a field_id"):
            builder._translate_metric_to_sql(
                MetricSpec(agg=AggregateFunction.SUM)
            )

    def test_translate_rejects_none(self, builder):
        with pytest.raises(ValueError, match="metric spec"):
            builder._translate_metric_to_sql(None)

    # ------------------------------------------------------------------
    # Sort options
    # ------------------------------------------------------------------
    def test_distribution_sort_order(self, builder):
        req = _make_request(
            options=QueryOptions(sortBy="value", sortOrder=SortOrder.ASC),
        )
        sql, params = builder.build_query(req)

        assert "ORDER BY value ASC" in sql

    def test_distribution_default_sort_desc(self, builder):
        req = _make_request()
        sql, params = builder.build_query(req)

        assert "DESC" in sql

    # ------------------------------------------------------------------
    # Array field handling in trend/comparison dimensions
    # ------------------------------------------------------------------
    def test_trend_array_dimension_uses_first_element(self, builder):
        """Array fields used as trend dimension should use field[1]."""
        req = _make_request(
            queryType=QueryType.TREND,
            select=SelectClause(timeField=_sf("Last Seen"), dimension=_sf("Manufacturer")),
            options=QueryOptions(interval=TimeInterval.MONTH),
        )
        sql, params = builder.build_query(req)

        assert "manufacturer[1] as series" in sql
        assert "GROUP BY" in sql
        assert "manufacturer[1]" in sql
        assert "manufacturer IS NOT NULL" in sql
        assert "array_length(manufacturer, 1) > 0" in sql

    def test_trend_scalar_dimension_unchanged(self, builder):
        """Non-array fields used as trend dimension should be used as-is."""
        req = _make_request(
            queryType=QueryType.TREND,
            select=SelectClause(timeField=_sf("Last Seen"), dimension=_sf("Category")),
            options=QueryOptions(interval=TimeInterval.DAY),
        )
        sql, params = builder.build_query(req)

        assert "category as series" in sql
        assert "array_length" not in sql

    def test_trend_array_dimension_limit_uses_first_element(self, builder):
        """Dimension limit subquery should also use field[1] for arrays."""
        req = _make_request(
            queryType=QueryType.TREND,
            select=SelectClause(timeField=_sf("Last Seen"), dimension=_sf("Manufacturer")),
            options=QueryOptions(interval=TimeInterval.MONTH, dimensionLimit=5),
        )
        sql, params = builder.build_query(req)

        assert params["dimension_limit"] == 5
        assert "manufacturer[1] IN" in sql

    def test_comparison_array_field_uses_first_element(self, builder):
        """Array fields in comparison queries should use field[1]."""
        req = _make_request(
            queryType=QueryType.COMPARISON,
            select=SelectClause(xAxis=_sf("Category"), yAxis=_sf("Manufacturer")),
        )
        sql, params = builder.build_query(req)

        assert "manufacturer[1]" in sql
        assert "category" in sql
        assert "manufacturer IS NOT NULL" in sql

    def test_trend_percentage_array_dimension(self, builder):
        """Trend percentage with array dimension should use field[1]."""
        req = _make_request(
            queryType=QueryType.TREND_PERCENTAGE,
            select=SelectClause(timeField=_sf("Last Seen"), dimension=_sf("Data Source")),
            options=QueryOptions(interval=TimeInterval.MONTH),
            comparisonFilters=[
                FilterCondition(field_id=_sf("Category"),
                                operator=FilterOperator.EQUALS, value="Server"),
            ],
        )
        sql, params = builder.build_query(req)

        assert "data_source[1] as series" in sql
        assert "data_source IS NOT NULL" in sql


class TestSortByValidation:
    def _base_comparison_payload(self):
        return {
            "queryType": "comparison",
            "from": {"table": "assets"},
            "select": {
                "xAxis": {"kind": "static", "name": "Category"},
                "yAxis": {"kind": "static", "name": "Manufacturer"},
            },
            "filters": [],
            "options": {},
        }

    def test_rejects_sortby_injection_subquery(self):
        payload = self._base_comparison_payload()
        payload["options"]["sortBy"] = "(SELECT pg_sleep(5))"
        with pytest.raises(ValidationError):
            ChartQueryRequest(**payload)

    def test_rejects_sortby_injection_comma_subquery(self):
        payload = self._base_comparison_payload()
        payload["options"]["sortBy"] = "value, (SELECT current_user)"
        with pytest.raises(ValidationError):
            ChartQueryRequest(**payload)

    def test_accepts_value(self):
        payload = self._base_comparison_payload()
        payload["options"]["sortBy"] = "value"
        ChartQueryRequest(**payload)  # no error

    def test_accepts_x_axis(self):
        payload = self._base_comparison_payload()
        payload["options"]["sortBy"] = "x_axis"
        ChartQueryRequest(**payload)  # no error
