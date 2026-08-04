# SPDX-License-Identifier: AGPL-3.0-or-later
"""Charts API Pydantic Models.

Column-level allowlisting is enforced by the FieldResolver at query-build
time (via `resolver.resolve()` raising `InvalidFieldError` for unknown FieldIds).
This module holds request/response shapes plus table-level allowlists and query
complexity limits.
"""

import re
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from enum import Enum
from datetime import datetime

_ALLOWED_SORT_FIELDS = {"value", "x_axis", "y_axis", "source", "target", "label", "timestamp", "count", "percentage"}

from src.modules.fields.models import FieldId

# Pre-compiled regex for join condition validation (table.column = table.column)
_JOIN_CONDITION_RE = re.compile(r'^(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)$')


# ============================================================================
# Enums
# ============================================================================

class QueryType(str, Enum):
    """Supported chart query types"""
    DISTRIBUTION = "distribution"  # For donut, pie, circle, radial bar
    METRIC = "metric"  # For gauge, metric card
    TREND = "trend"  # For line chart, timeseries
    TREND_PERCENTAGE = "trend_percentage"  # For line chart percentage-over-time
    COMPARISON = "comparison"  # For bar charts, butterfly, stacked column
    CORRELATION = "correlation"  # For sankey, punchcard
    HISTORY_TREND = "history_trend"  # Snapshot-history time-series (assets_history)


class FilterOperator(str, Enum):
    """Allowed filter operators.

    NOTE: This is the charts-specific operator enum. The fields module has its
    own FilterOperator enum whose string values differ (e.g. "not equals" vs
    "not_equals"). Both must continue to exist — the charts operator strings
    are the wire format used by the chart-query UI.
    """
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    GT = "gt"  # Greater than
    GTE = "gte"  # Greater than or equal
    LT = "lt"  # Less than
    LTE = "lte"  # Less than or equal
    IN = "in"  # In array
    NOT_IN = "not_in"  # Not in array
    IS_NULL = "is empty"
    IS_NOT_NULL = "is not empty"
    BETWEEN = "between"


class JoinType(str, Enum):
    """Allowed SQL join types"""
    INNER = "INNER"
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class TimeInterval(str, Enum):
    """Time bucketing intervals for trend queries"""
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class SortOrder(str, Enum):
    """Sort order options"""
    ASC = "asc"
    DESC = "desc"


class AggregateFunction(str, Enum):
    """Allowed aggregate functions"""
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


# ============================================================================
# Security Whitelists
# ============================================================================

class SecurityWhitelists:
    """
    Table-level whitelist + query complexity limits.

    Column-level validation is no longer maintained here. Field identifiers
    travel through the FieldResolver (src.modules.fields) whose live catalog
    is the authoritative allowlist. Unknown FieldIds surface as
    InvalidFieldError at query-build time.
    """

    # Allowed tables for FROM clause
    ALLOWED_TABLES = {
        "assets",
        "assets_history",
        "asset_providers",
        "asset_tags_v2",
        "integrations"
    }

    # Query complexity limits
    MAX_JOINS = 3
    MAX_FILTERS = 10
    MAX_LIMIT = 1000
    DEFAULT_LIMIT = 100
    MAX_OFFSET = 10000


# ============================================================================
# Sub-Models
# ============================================================================

class MetricSpec(BaseModel):
    """Structured metric specification for aggregations.

    Examples:
        {"agg": "COUNT"}                                          -> COUNT(*)
        {"agg": "COUNT_DISTINCT", "field_id": {"kind": "static", "name": "Category"}}
        {"agg": "AVG", "field_id": {"kind": "static", "name": "Confidence Score"}}
    """
    model_config = ConfigDict(extra="forbid")

    agg: AggregateFunction
    field_id: Optional[FieldId] = None  # None for COUNT(*)


class FilterCondition(BaseModel):
    """
    A single filter condition for WHERE clause.

    Example:
        {
            "field_id": {"kind": "static", "name": "Category"},
            "operator": "equals",
            "value": "Workstation"
        }
    """
    model_config = ConfigDict(extra="forbid")

    field_id: FieldId
    operator: FilterOperator
    value: Optional[Union[str, int, float, bool, List[Union[str, int, float]]]] = None

    @model_validator(mode='after')
    def validate_value_required(self):
        """Ensure value is provided when operator needs it"""
        if self.operator not in [FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL]:
            if self.value is None:
                raise ValueError(
                    f"Value is required for operator '{self.operator.value}'"
                )
        return self


class JoinClause(BaseModel):
    """
    A JOIN clause specification.

    Example:
        {
            "table": "asset_providers",
            "type": "LEFT",
            "on": "assets.id = asset_providers.asset_id"
        }
    """
    table: str = Field(..., description="Table to join")
    type: JoinType = Field(JoinType.LEFT, description="Join type")
    on: str = Field(..., description="Join condition")

    @field_validator('table')
    @classmethod
    def validate_table_allowed(cls, v: str) -> str:
        """Ensure table is in whitelist"""
        if v not in SecurityWhitelists.ALLOWED_TABLES:
            raise ValueError(
                f"Invalid table '{v}'. Allowed tables: {sorted(SecurityWhitelists.ALLOWED_TABLES)}"
            )
        return v

    @field_validator('on')
    @classmethod
    def validate_join_condition(cls, v: str) -> str:
        """Validate join condition against the strict table.column = table.column shape.

        Decision (issue #455 / Task 15): after retiring TABLE_FIELDS_MAP we no
        longer column-level-allowlist the JOIN expression. _JOIN_CONDITION_RE
        only permits ``\\w+\\.\\w+\\s*=\\s*\\w+\\.\\w+`` (alphanumeric +
        underscores), which is tight enough to prevent SQL injection through
        the join expression — UNION, semicolons, subqueries, comments, and
        arbitrary punctuation all fail the regex. Tables are still validated
        against ALLOWED_TABLES.
        """
        match = _JOIN_CONDITION_RE.match(v.strip())
        if not match:
            raise ValueError(
                "Join condition must be in the format 'table.column = table.column'"
            )

        table_l, _, table_r, _ = match.groups()

        # Table-level allowlist only (column-level allowlist removed with #455).
        for table in (table_l, table_r):
            if table not in SecurityWhitelists.ALLOWED_TABLES:
                raise ValueError(
                    f"Table '{table}' in join condition is not allowed. "
                    f"Allowed: {sorted(SecurityWhitelists.ALLOWED_TABLES)}"
                )

        return v


class SelectClause(BaseModel):
    """
    SELECT clause specification defining what data to retrieve.

    Different query types use different fields:
    - Distribution: dimension (required), segment (optional)
    - Metric: metric (required)
    - Trend: dimension, timeField (required), metric (optional)
    - Comparison: xAxis, yAxis (required), metric (optional)
    - Correlation: sourceField, targetField (required)

    Field identifiers are typed FieldIds (discriminated union); Pydantic
    rejects structurally invalid shapes. Unknown FieldIds that parse OK but
    don't resolve against the live catalog fail at QueryBuilder.build_query
    time with InvalidFieldError.
    """
    model_config = ConfigDict(extra="forbid")

    # For distribution queries
    dimension: Optional[FieldId] = None
    segment: Optional[FieldId] = None

    # For metric queries
    metric: Optional[MetricSpec] = None

    # For trend queries
    timeField: Optional[FieldId] = None

    # For comparison queries
    xAxis: Optional[FieldId] = None
    yAxis: Optional[FieldId] = None

    # For correlation queries (Sankey, Punchcard)
    sourceField: Optional[FieldId] = None
    targetField: Optional[FieldId] = None


class QueryOptions(BaseModel):
    """
    Query execution options (limit, sort, time range, etc.).
    """
    # Pagination
    limit: int = Field(
        SecurityWhitelists.DEFAULT_LIMIT,
        ge=1,
        le=SecurityWhitelists.MAX_LIMIT,
        description="Maximum number of results to return"
    )
    offset: int = Field(
        0,
        ge=0,
        le=SecurityWhitelists.MAX_OFFSET,
        description="Number of results to skip"
    )

    # Sorting
    sortBy: Optional[str] = Field(None, description="Field to sort by (alias of output column)")
    sortOrder: SortOrder = Field(SortOrder.DESC, description="Sort order")

    @field_validator('sortBy')
    @classmethod
    def validate_sort_by(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in _ALLOWED_SORT_FIELDS:
            raise ValueError(
                f"Invalid sortBy '{v}'. Allowed: {sorted(_ALLOWED_SORT_FIELDS)}"
            )
        return v

    # Time range (for trend queries)
    interval: Optional[TimeInterval] = Field(None, description="Time bucketing interval")
    last: Optional[str] = Field(None, description="Time range (e.g., '7d', '30d', '24h')")

    # Metric options
    metricType: Optional[str] = Field(None, description="Metric mode: total, filtered, or percentage")
    showPercentage: bool = Field(False, description="Whether result represents a percentage")
    maxValue: Optional[int] = Field(None, description="Maximum value for gauge display")

    # Dimension limiting (for multi-series trend queries)
    dimensionLimit: Optional[int] = Field(None, ge=1, le=20, description="Max distinct dimension values for multi-series trends")

    # Segment limiting (for comparison/stacked queries)
    segmentLimit: Optional[int] = Field(None, ge=1, le=20, description="Max distinct segment values per group in comparison queries")

    # Row/column limiting (for correlation/punchcard queries)
    rowLimit: Optional[int] = Field(None, ge=1, le=30, description="Max distinct row values in correlation queries")
    columnLimit: Optional[int] = Field(None, ge=1, le=30, description="Max distinct column values in correlation queries")

    # Other options
    includeNull: bool = Field(False, description="Include NULL values in results")
    calculatePercentage: bool = Field(True, description="Calculate percentage for distribution queries")


class FromClause(BaseModel):
    """
    FROM clause specification with optional JOINs.

    Example:
        {
            "table": "assets",
            "joins": [
                {"table": "asset_providers", "type": "LEFT", "on": "assets.id = asset_providers.asset_id"}
            ]
        }
    """
    table: str = Field(..., description="Primary table to query")
    joins: List[JoinClause] = Field(default_factory=list, description="JOIN clauses")

    @field_validator('table')
    @classmethod
    def validate_table_allowed(cls, v: str) -> str:
        """Ensure table is in whitelist"""
        if v not in SecurityWhitelists.ALLOWED_TABLES:
            raise ValueError(
                f"Invalid table '{v}'. Allowed tables: {sorted(SecurityWhitelists.ALLOWED_TABLES)}"
            )
        return v

    @field_validator('joins')
    @classmethod
    def validate_joins_limit(cls, v: List[JoinClause]) -> List[JoinClause]:
        """Ensure number of joins doesn't exceed limit"""
        if len(v) > SecurityWhitelists.MAX_JOINS:
            raise ValueError(
                f"Too many joins ({len(v)}). Maximum allowed: {SecurityWhitelists.MAX_JOINS}"
            )
        return v


# ============================================================================
# Main Request Model
# ============================================================================

class ChartQueryRequest(BaseModel):
    """
    Main request model for POST /api/charts/query endpoint.

    Example:
        {
            "queryType": "distribution",
            "from": { "table": "assets", "joins": [] },
            "select": {
                "dimension": {"kind": "static", "name": "Category"}
            },
            "filters": [
                {
                    "field_id": {"kind": "static", "name": "Confidence Score"},
                    "operator": "gte",
                    "value": 50
                }
            ],
            "options": {
                "limit": 100,
                "sortBy": "value",
                "sortOrder": "desc"
            }
        }
    """
    queryType: QueryType = Field(..., description="Type of chart query")
    from_: FromClause = Field(..., alias="from", description="FROM clause with table and joins")
    select: SelectClause = Field(..., description="SELECT clause defining data to retrieve")
    filters: List[FilterCondition] = Field(
        default_factory=list,
        description="WHERE clause filters"
    )
    comparisonFilters: List[FilterCondition] = Field(
        default_factory=list,
        description="Denominator filters for percentage metric queries"
    )
    options: QueryOptions = Field(
        default_factory=QueryOptions,
        description="Query execution options"
    )

    class Config:
        populate_by_name = True  # Allow both 'from' and 'from_'

    @field_validator('filters')
    @classmethod
    def validate_filters_limit(cls, v: List[FilterCondition]) -> List[FilterCondition]:
        """Ensure number of filters doesn't exceed limit"""
        if len(v) > SecurityWhitelists.MAX_FILTERS:
            raise ValueError(
                f"Too many filters ({len(v)}). Maximum allowed: {SecurityWhitelists.MAX_FILTERS}"
            )
        return v

    @model_validator(mode='after')
    def validate_query_type_requirements(self):
        """Ensure required fields are present for each query type"""
        query_type = self.queryType
        select = self.select

        if query_type == QueryType.DISTRIBUTION:
            if not select.dimension:
                raise ValueError("Distribution queries require 'dimension' in select clause")

        elif query_type == QueryType.METRIC:
            if not select.metric:
                raise ValueError("Metric queries require 'metric' in select clause")
            if self.options.metricType == "distinct" and not select.dimension:
                raise ValueError(
                    "distinct metric mode requires 'dimension' in select clause "
                    "(the field whose unique values are counted)"
                )

        elif query_type == QueryType.TREND:
            if not select.timeField:
                raise ValueError("Trend queries require 'timeField' in select clause")
            if not self.options.interval:
                raise ValueError("Trend queries require 'interval' in options")

        elif query_type == QueryType.TREND_PERCENTAGE:
            if not select.timeField:
                raise ValueError("Trend percentage queries require 'timeField' in select clause")
            if not self.options.interval:
                raise ValueError("Trend percentage queries require 'interval' in options")

        elif query_type == QueryType.HISTORY_TREND:
            if not self.options.interval:
                raise ValueError("History trend queries require 'interval' in options")
            # NOTE: no timeField — bucketing is server-side on archived_at.

        elif query_type == QueryType.COMPARISON:
            # Accept dimension/segment as aliases for xAxis/yAxis
            if select.dimension and select.segment and not select.xAxis and not select.yAxis:
                select.xAxis = select.dimension
                select.yAxis = select.segment
            if not select.xAxis or not select.yAxis:
                raise ValueError("Comparison queries require both 'xAxis' and 'yAxis' (or 'dimension' and 'segment') in select clause")

        elif query_type == QueryType.CORRELATION:
            if not select.sourceField or not select.targetField:
                raise ValueError("Correlation queries require both 'sourceField' and 'targetField' in select clause")

        return self


# ============================================================================
# Response Models
# ============================================================================

class ChartMetadata(BaseModel):
    """
    Metadata about query execution.
    """
    total: int = Field(..., description="Total number of records matching query")
    executionTime: int = Field(..., description="Query execution time in milliseconds")
    cached: bool = Field(False, description="Whether result was served from cache")
    timestamp: datetime = Field(..., description="Timestamp when query was executed")
    sql: Optional[str] = Field(None, description="Generated SQL query (dev mode only)")


class ChartDataPoint(BaseModel):
    """
    A single data point in the chart response.

    Structure varies by query type, but common fields include:
    - label: Category/dimension name
    - value: Numeric value
    - color: Color for visualization
    - percentage: Percentage of total (for distribution)
    """
    label: Optional[str] = None
    value: Optional[Union[int, float]] = None
    color: Optional[str] = None
    percentage: Optional[float] = None
    # Additional fields for specific chart types
    extra: Optional[Dict[str, Any]] = Field(default_factory=dict)


class ChartQueryResponse(BaseModel):
    """
    Success response for chart query.

    Example:
        {
            "success": true,
            "queryType": "distribution",
            "data": [
                {"label": "Workstation", "value": 2729, "percentage": 45.2, "color": "#667eea"}
            ],
            "metadata": {
                "total": 6042,
                "executionTime": 87,
                "cached": false,
                "timestamp": "2025-11-19T10:30:00Z"
            }
        }
    """
    success: bool = Field(True, description="Whether query succeeded")
    queryType: QueryType = Field(..., description="Type of query executed")
    data: List[Dict[str, Any]] = Field(..., description="Query results")
    metadata: ChartMetadata = Field(..., description="Query execution metadata")


class ErrorDetail(BaseModel):
    """
    Error details for failed requests.
    """
    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")


class ChartQueryErrorResponse(BaseModel):
    """
    Error response for chart query.

    Example:
        {
            "success": false,
            "error": {
                "code": "INVALID_QUERY",
                "message": "Invalid dimension field: invalid_field",
                "details": {"allowedFields": ["category", "os_normalized", "..."]}
            }
        }
    """
    success: bool = Field(False, description="Whether query succeeded")
    error: ErrorDetail = Field(..., description="Error information")
