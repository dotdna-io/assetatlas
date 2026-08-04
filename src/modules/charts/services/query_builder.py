# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Chart Query Builder

Builds safe, parameterized SQL queries from validated chart query requests.

Uses template-based approach with prepared statements to prevent SQL injection.
Supports 5 query types: distribution, metric, trend, comparison, correlation.

Created: 2025-11-19
Updated: 2026-04-21 — issue #455: consumes typed FieldId via FieldResolver.
Array / virtual / jsonb detection comes from ResolvedField metadata; no raw
column strings from the request touch SQL.
"""

import re
from typing import Dict, List, Tuple, Any, Optional
from src.api.blueprints.charts.models import (
    ChartQueryRequest,
    QueryType,
    FilterOperator,
    MetricSpec,
)
from src.api.modules.settings.settings_schema import DEFAULT_CONFIDENCE_THRESHOLDS
from src.modules.fields import (
    FieldResolver,
    ResolvedField,
    FieldDataType,
    VirtualField,
)
import logging

logger = logging.getLogger(__name__)


class QueryBuilder:
    """
    Builds SQL queries from validated chart query requests.

    Uses template-based SQL generation with parameterized queries
    to prevent SQL injection while supporting flexible query structures.

    The FieldResolver is the single authority for field-to-SQL translation —
    array semantics, JSONB paths, virtual expressions, and catalog liveness
    checks all flow through resolver.resolve(field_id).
    """

    def __init__(self, confidence_thresholds=None, resolver: Optional[FieldResolver] = None):
        """Initialize the query builder.

        Args:
            confidence_thresholds: (medium, high) tuple used by
                confidence_level virtual-field translation.
            resolver: FieldResolver instance. Required — there is no safe
                fallback; passing None raises ValueError.
        """
        self.confidence_thresholds = confidence_thresholds or DEFAULT_CONFIDENCE_THRESHOLDS
        if resolver is None:
            raise ValueError("QueryBuilder requires a FieldResolver instance")
        self._resolver = resolver
        logger.debug("QueryBuilder initialized")

    # ------------------------------------------------------------------
    # ResolvedField-aware helpers
    # ------------------------------------------------------------------

    def _resolve_array_field(self, resolved: ResolvedField) -> str:
        """Return ``col[1]`` for array columns, or the SQL expression as-is.

        Only plain columns (sql_kind == "column") are subscripted with [1].
        JSONB array sources keep their expression unchanged — subscripting
        ``extended_attributes->'Src'->'Attr'`` with [1] would not be valid.
        """
        if resolved.is_native_array():
            return f"{resolved.where_expr()}[1]"
        return resolved.where_expr()

    def _array_not_empty_condition(self, resolved: ResolvedField) -> Optional[str]:
        """WHERE condition that excludes NULL/empty arrays (None for non-array columns)."""
        if resolved.is_native_array():
            col = resolved.where_expr()
            return f"({col} IS NOT NULL AND {col} != '{{}}' AND array_length({col}, 1) > 0)"
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_query(self, request: ChartQueryRequest) -> Tuple[str, Dict[str, Any]]:
        """
        Build SQL query from validated request.

        Args:
            request: Validated chart query request

        Returns:
            Tuple of (sql_query, parameters)

        Raises:
            ValueError: If query type is not supported, or if a virtual
                field is used in a SELECT slot, or if timeField resolves
                to a non-time data type.
            InvalidFieldError: If a FieldId does not resolve against the
                live catalog (propagated from resolver.resolve).
        """
        query_type = request.queryType

        # Reject virtual fields in SELECT slots (they have no DB column, only
        # a CASE expression that doesn't make sense as a grouping key).
        select = request.select
        for attr in ('dimension', 'segment', 'xAxis', 'yAxis', 'sourceField', 'targetField', 'timeField'):
            field_id = getattr(select, attr, None)
            if field_id is not None and isinstance(field_id, VirtualField):
                raise ValueError(
                    f"Virtual field '{field_id.name}' cannot be used as {attr}. "
                    f"Virtual fields can only be used in filters."
                )

        # timeField semantic check — must resolve to a time/date type.
        if select.timeField is not None:
            resolved_time = self._resolver.resolve(select.timeField)
            if resolved_time.data_type not in (FieldDataType.TIMESTAMP, FieldDataType.DATE):
                raise ValueError(
                    f"timeField must resolve to a timestamp/date field; "
                    f"got {resolved_time.data_type.value}"
                )

        logger.debug(f"Building {query_type.value} query for table: {request.from_.table}")

        # Route to appropriate query builder based on type
        if query_type == QueryType.DISTRIBUTION:
            return self._build_distribution_query(request)
        elif query_type == QueryType.METRIC:
            return self._build_metric_query(request)
        elif query_type == QueryType.TREND:
            return self._build_trend_query(request)
        elif query_type == QueryType.TREND_PERCENTAGE:
            return self._build_trend_percentage_query(request)
        elif query_type == QueryType.COMPARISON:
            return self._build_comparison_query(request)
        elif query_type == QueryType.CORRELATION:
            return self._build_correlation_query(request)
        elif query_type == QueryType.HISTORY_TREND:
            return self._build_history_trend_query(request)
        else:
            raise ValueError(f"Unsupported query type: {query_type}")

    # ============================================================================
    # Distribution Queries (for Donut, Pie, Circle, Radial Bar charts)
    # ============================================================================

    def _build_distribution_query(self, request: ChartQueryRequest) -> Tuple[str, Dict[str, Any]]:
        """
        Build distribution query (GROUP BY single dimension).

        Queries ALL data, ranks values by count, and aggregates values beyond
        the limit as "Other". Guarantees exactly `limit` items + optional "Other".
        """
        params = {}
        dimension_field_id = request.select.dimension
        dim_resolved = self._resolver.resolve(dimension_field_id)
        dim_expr = dim_resolved.where_expr()
        include_null = request.options.includeNull

        def wrap_dimension(resolved: ResolvedField) -> str:
            """Resolve display expression for the dimension, handling array+JSONB+null."""
            base_expr = self._resolve_array_field(resolved)
            if not include_null:
                # JSONB-sourced expressions: cast to text so the outer CASE 'Other' works.
                if resolved.is_jsonb_sourced():
                    return f"({base_expr})::text"
                return base_expr
            # includeNull = True → map NULL/empty to '(Empty)'
            if resolved.is_native_array():
                col = resolved.where_expr()
                return (
                    f"CASE WHEN {col} IS NULL OR {col} = '{{}}' "
                    f"OR array_length({col}, 1) IS NULL OR {col}[1] IS NULL "
                    f"OR TRIM({col}[1]) = '' THEN '(Empty)' ELSE {col}[1] END"
                )
            if resolved.is_jsonb_sourced():
                return f"COALESCE(({base_expr})::text, '(Empty)')"
            return f"COALESCE({base_expr}::text, '(Empty)')"

        wrapped_dimension = wrap_dimension(dim_resolved)

        # Build FROM clause with JOINs
        from_clause, join_params = self._build_from_clause(request.from_)
        params.update(join_params)

        # Build WHERE clause
        where_clause, filter_params = self._build_where_clause(request.filters)
        params.update(filter_params)

        # Exclude NULL/empty values if includeNull is False
        if not include_null:
            arr_cond = self._array_not_empty_condition(dim_resolved)
            null_filter = arr_cond if arr_cond else f"{dim_expr} IS NOT NULL"
            where_clause = self._append_where(where_clause, null_filter)

        # Limit and sort
        params['limit'] = request.options.limit
        sort_order = request.options.sortOrder.value.upper()

        query = f"""
            WITH ranked AS (
                SELECT {wrapped_dimension} as label, COUNT(*) as value,
                       ROW_NUMBER() OVER (ORDER BY COUNT(*) {sort_order}) as rn
                {from_clause}
                {where_clause}
                GROUP BY {wrapped_dimension}
            )
            SELECT
                CASE WHEN rn <= %(limit)s THEN label ELSE 'Other' END as label,
                SUM(value) as value
            FROM ranked
            GROUP BY CASE WHEN rn <= %(limit)s THEN label ELSE 'Other' END
            ORDER BY value {sort_order}
        """

        logger.debug(f"Distribution query built: {len(params)} parameters")
        return query.strip(), params

    # ============================================================================
    # Metric Queries (for Gauge, Metric Card charts)
    # ============================================================================

    def _build_metric_query(self, request: ChartQueryRequest) -> Tuple[str, Dict[str, Any]]:
        """
        Build metric query (single aggregation).

        Supports four modes via options.metricType:
        - 'total': COUNT(*) with no filters
        - 'filtered': COUNT(*) with filters
        - 'percentage': (numerator COUNT / denominator COUNT) * 100
        - 'distinct': COUNT(DISTINCT dimension) with filters
        """
        params = {}
        metric_type = request.options.metricType or 'total'

        # Build FROM clause (shared by all modes)
        from_clause, join_params = self._build_from_clause(request.from_)
        params.update(join_params)

        if metric_type == 'percentage':
            # Percentage: two subqueries for numerator and denominator
            numerator_filters = list(request.comparisonFilters) + list(request.filters)
            num_where, num_params = self._build_where_clause(
                numerator_filters, param_prefix="num"
            )
            params.update(num_params)

            den_where, den_params = self._build_where_clause(
                request.comparisonFilters, param_prefix="den"
            )
            params.update(den_params)

            query = f"""
                SELECT
                    CASE WHEN den.total = 0 THEN 0
                         ELSE ROUND((num.total::numeric / den.total::numeric) * 100, 1)
                    END as value
                FROM
                    (SELECT COUNT(*) as total {from_clause} {num_where}) num,
                    (SELECT COUNT(*) as total {from_clause} {den_where}) den
            """
        elif metric_type == 'distinct' and request.select.dimension is not None:
            # Count distinct values of a field
            dim_resolved = self._resolver.resolve(request.select.dimension)
            dim_sql = self._resolve_array_field(dim_resolved)
            select_clause = f"COUNT(DISTINCT {dim_sql}) as value"

            where_clause, filter_params = self._build_where_clause(request.filters)
            params.update(filter_params)

            # Exclude NULL/empty array values
            arr_cond = self._array_not_empty_condition(dim_resolved)
            if arr_cond:
                if where_clause.strip():
                    where_clause = f"{where_clause} AND {arr_cond}"
                else:
                    where_clause = f"WHERE {arr_cond}"

            query = f"""
                SELECT {select_clause}
                {from_clause}
                {where_clause}
            """
        else:
            # total or filtered: aggregation from MetricSpec
            metric_spec = request.select.metric
            metric_sql = self._translate_metric_to_sql(metric_spec)
            select_clause = f"{metric_sql} as value"

            where_clause, filter_params = self._build_where_clause(request.filters)
            params.update(filter_params)

            query = f"""
                SELECT {select_clause}
                {from_clause}
                {where_clause}
            """

        logger.debug(f"Metric query built ({metric_type}): {len(params)} parameters")
        return query.strip(), params

    # ============================================================================
    # Trend Queries (for Line Chart, Timeseries charts)
    # ============================================================================

    def _build_trend_query(self, request: ChartQueryRequest) -> Tuple[str, Dict[str, Any]]:
        """Build trend query (time-series aggregation)."""
        params = {}
        time_resolved = self._resolver.resolve(request.select.timeField)
        time_expr = time_resolved.where_expr()
        interval = request.options.interval.value if request.options.interval else 'day'

        dim_resolved: Optional[ResolvedField] = None
        if request.select.dimension is not None:
            dim_resolved = self._resolver.resolve(request.select.dimension)

        # Build SELECT clause
        if dim_resolved is not None:
            dim_sql = self._resolve_array_field(dim_resolved)
            select_clause = f"""
                DATE_TRUNC('{interval}', {time_expr}) as timestamp,
                {dim_sql} as series,
                COUNT(*) as value
            """
            group_by_clause = f"GROUP BY DATE_TRUNC('{interval}', {time_expr}), {dim_sql}"
        else:
            select_clause = f"""
                DATE_TRUNC('{interval}', {time_expr}) as timestamp,
                COUNT(*) as value
            """
            group_by_clause = f"GROUP BY DATE_TRUNC('{interval}', {time_expr})"

        # Build FROM clause with JOINs
        from_clause, join_params = self._build_from_clause(request.from_)
        params.update(join_params)

        # Build WHERE clause (include time range filter if specified)
        where_clauses = []
        time_range_clause = None
        if request.options.last:
            time_range = self._parse_time_range(request.options.last)
            time_range_clause = f"{time_expr} >= NOW() - INTERVAL '{time_range}'"
            where_clauses.append(time_range_clause)

        # Add user-specified filters
        user_where_clause, filter_params = self._build_where_clause(request.filters)
        if user_where_clause:
            where_clauses.append(user_where_clause.replace("WHERE ", ""))
        params.update(filter_params)

        # Exclude NULL/empty array values for array dimension fields
        arr_cond = self._array_not_empty_condition(dim_resolved) if dim_resolved else None
        if arr_cond:
            where_clauses.append(arr_cond)

        # Limit dimension values for multi-series (top N by count)
        self._apply_dimension_limit(
            dim_resolved, request, time_range_clause, where_clauses, params, arr_cond=arr_cond
        )

        where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        order_by_clause = "ORDER BY timestamp ASC"

        params['limit'] = request.options.limit
        limit_clause = "LIMIT %(limit)s"

        query = f"""
            SELECT {select_clause}
            {from_clause}
            {where_clause}
            {group_by_clause}
            {order_by_clause}
            {limit_clause}
        """

        logger.debug(f"Trend query built: {len(params)} parameters")
        return query.strip(), params

    # ============================================================================
    # Trend Percentage Queries (for Line Chart percentage-over-time)
    # ============================================================================

    def _build_trend_percentage_query(self, request: ChartQueryRequest) -> Tuple[str, Dict[str, Any]]:
        """Build trend percentage query (percentage over time)."""
        params = {}
        time_resolved = self._resolver.resolve(request.select.timeField)
        time_expr = time_resolved.where_expr()
        interval = request.options.interval.value if request.options.interval else 'day'

        dim_resolved: Optional[ResolvedField] = None
        if request.select.dimension is not None:
            dim_resolved = self._resolver.resolve(request.select.dimension)

        # Build numerator conditions (comparison + filters = subset matching)
        numerator_filters = list(request.comparisonFilters) + list(request.filters)
        num_conditions, num_params = self._build_filter_conditions(
            numerator_filters, param_prefix="num"
        )
        params.update(num_params)

        # Build denominator conditions (comparison filters only = base set)
        den_conditions, den_params = self._build_filter_conditions(
            request.comparisonFilters, param_prefix="den"
        )
        params.update(den_params)

        num_filter = f"FILTER (WHERE {' AND '.join(num_conditions)})" if num_conditions else ""
        den_filter = f"FILTER (WHERE {' AND '.join(den_conditions)})" if den_conditions else ""

        value_expr = f"""CASE WHEN COUNT(*) {den_filter} = 0 THEN 0
             ELSE ROUND(
                 COUNT(*) {num_filter} * 100.0
                 / COUNT(*) {den_filter}, 1
             )
        END as value"""

        if dim_resolved is not None:
            dim_sql = self._resolve_array_field(dim_resolved)
            select_clause = f"""
            DATE_TRUNC('{interval}', {time_expr}) as timestamp,
            {dim_sql} as series,
            {value_expr}"""
            group_by_clause = f"GROUP BY DATE_TRUNC('{interval}', {time_expr}), {dim_sql}"
        else:
            select_clause = f"""
            DATE_TRUNC('{interval}', {time_expr}) as timestamp,
            {value_expr}"""
            group_by_clause = f"GROUP BY DATE_TRUNC('{interval}', {time_expr})"

        # Build FROM clause
        from_clause, join_params = self._build_from_clause(request.from_)
        params.update(join_params)

        # Build WHERE clause (time range only — filters go into FILTER expressions)
        where_clauses = []
        time_range_clause = None
        if request.options.last:
            time_range = self._parse_time_range(request.options.last)
            time_range_clause = f"{time_expr} >= NOW() - INTERVAL '{time_range}'"
            where_clauses.append(time_range_clause)

        arr_cond = self._array_not_empty_condition(dim_resolved) if dim_resolved else None
        if arr_cond:
            where_clauses.append(arr_cond)

        self._apply_dimension_limit(
            dim_resolved, request, time_range_clause, where_clauses, params, arr_cond=arr_cond
        )

        where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        order_by_clause = "ORDER BY timestamp ASC"
        params['limit'] = request.options.limit
        limit_clause = "LIMIT %(limit)s"

        query = f"""
        SELECT {select_clause}
        {from_clause}
        {where_clause}
        {group_by_clause}
        {order_by_clause}
        {limit_clause}
    """

        logger.debug(f"Trend percentage query built: {len(params)} parameters")
        return query.strip(), params

    # ============================================================================
    # History Trend Queries (snapshot time-series over assets_history)
    # ============================================================================

    def _build_history_trend_query(self, request: ChartQueryRequest) -> Tuple[str, Dict[str, Any]]:
        """Snapshot-history time-series over assets_history (#497/#946).

        Buckets the LAST snapshot per DATE_TRUNC(interval, archived_at) and
        counts matching rows, then appends the live 'now' bucket from `assets`
        so the series ends at the current value. Optional split-by-dimension
        and percentage mode (numerator=comparisonFilters+filters,
        denominator=comparisonFilters), mirroring _build_trend_percentage_query.
        Returns rows shaped {timestamp, value, series?} — same as `trend`.

        Every chart-exposable field now resolves to a column archived into
        assets_history (migration 136 mirrored os_eol_*/tags/posture_rules),
        so no field-availability guard is needed.
        """
        params: Dict[str, Any] = {}
        interval = request.options.interval.value if request.options.interval else 'day'
        is_pct = bool(request.options.showPercentage)

        dim_resolved: Optional[ResolvedField] = None
        dim_sql = None
        if request.select.dimension is not None:
            dim_resolved = self._resolver.resolve(request.select.dimension)
            dim_sql = self._resolve_array_field(dim_resolved)

        # Range predicate on archived_at (history leg only — the live leg is
        # always the current 'now' bucket). Interval string comes from
        # _parse_time_range, so it is safe to embed as the trend builders do.
        range_hist = ""
        if request.options.last:
            tr = self._parse_time_range(request.options.last)
            range_hist = f"WHERE archived_at >= NOW() - INTERVAL '{tr}'"

        cur_bucket = f"DATE_TRUNC('{interval}', NOW())"

        # ---- value expression (count vs percentage) ----
        if is_pct:
            num_filters = list(request.comparisonFilters) + list(request.filters)
            num_conds, num_params = self._build_filter_conditions(num_filters, param_prefix="num")
            den_conds, den_params = self._build_filter_conditions(request.comparisonFilters, param_prefix="den")
            params.update(num_params)
            params.update(den_params)
            num_f = f"FILTER (WHERE {' AND '.join(num_conds)})" if num_conds else ""
            den_f = f"FILTER (WHERE {' AND '.join(den_conds)})" if den_conds else ""
            value_expr = (
                f"CASE WHEN COUNT(*) {den_f} = 0 THEN 0 ELSE "
                f"ROUND(COUNT(*) {num_f} * 100.0 / COUNT(*) {den_f}, 1) END"
            )
            hist_join_where = ""   # filters live inside FILTER(), not WHERE
            live_where = ""
        else:
            value_expr = "COUNT(*)"
            hw, filter_params = self._build_where_clause(request.filters)
            params.update(filter_params)
            # History filters apply to the JOIN result (only h has asset
            # columns, so the unqualified filter columns bind to assets_history).
            hist_join_where = hw  # "WHERE ..." or ""
            live_where = hw

        # Series column, qualified per leg. Both legs use alias `h` (history
        # JOINs c+h; live is single-table `assets h`), so the same unqualified
        # dimension/filter expressions bind correctly in each.
        series_sel_h = f", {dim_sql} AS series" if dim_sql else ""
        series_grp_h = f", {dim_sql}" if dim_sql else ""
        series_sel_live = f", {dim_sql} AS series" if dim_sql else ""
        series_grp_live = f", {dim_sql}" if dim_sql else ""

        # Exclude NULL/empty array-dimension rows from the split so an empty
        # array doesn't render as a spurious NULL series or consume a top-N
        # slot (parity with _build_trend_query / _build_trend_percentage_query).
        arr_cond = self._array_not_empty_condition(dim_resolved) if dim_resolved else None

        # Cap multi-series output to the top-N dimension values by CURRENT count
        # (live assets), applied identically to both legs so the same series set
        # appears across the history buckets and the live 'now' point.
        if dim_sql and request.options.dimensionLimit:
            params['dimension_limit'] = request.options.dimensionLimit
            sub_where = f"WHERE {arr_cond}" if arr_cond else ""
            dim_limit_pred = (
                f"{dim_sql} IN (SELECT {dim_sql} FROM assets {sub_where} "
                f"GROUP BY {dim_sql} ORDER BY COUNT(*) DESC LIMIT %(dimension_limit)s)"
            )
            hist_join_where = self._append_where(hist_join_where, dim_limit_pred)
            live_where = self._append_where(live_where, dim_limit_pred)

        if arr_cond:
            hist_join_where = self._append_where(hist_join_where, arr_cond)
            live_where = self._append_where(live_where, arr_cond)

        history_leg = f"""
            SELECT c.bucket AS timestamp{series_sel_h}, {value_expr} AS value
            FROM (
                SELECT generation_run_id, DATE_TRUNC('{interval}', snap_ts) AS bucket
                FROM (
                    SELECT generation_run_id, MAX(archived_at) AS snap_ts,
                           ROW_NUMBER() OVER (
                               PARTITION BY DATE_TRUNC('{interval}', MAX(archived_at))
                               ORDER BY MAX(archived_at) DESC) AS rn
                    FROM assets_history
                    {range_hist}
                    GROUP BY generation_run_id
                ) s
                WHERE rn = 1 AND DATE_TRUNC('{interval}', snap_ts) < {cur_bucket}
            ) c
            JOIN assets_history h ON h.generation_run_id = c.generation_run_id
            {hist_join_where}
            GROUP BY c.bucket{series_grp_h}
        """

        live_leg = f"""
            SELECT {cur_bucket} AS timestamp{series_sel_live}, {value_expr} AS value
            FROM assets h
            {live_where}
            GROUP BY {cur_bucket}{series_grp_live}
        """

        query = f"""
            SELECT timestamp{', series' if dim_sql else ''}, value FROM (
                {history_leg}
                UNION ALL
                {live_leg}
            ) t
            ORDER BY timestamp ASC
        """
        logger.debug(f"History trend query built: {len(params)} parameters")
        return query.strip(), params

    def _apply_dimension_limit(
        self,
        dim_resolved: Optional[ResolvedField],
        request,
        time_range_clause,
        where_clauses,
        params,
        arr_cond=None,
    ):
        """Append a top-N dimension subquery to where_clauses if dimensionLimit is set."""
        if dim_resolved is not None and request.options.dimensionLimit:
            dim_sql = self._resolve_array_field(dim_resolved)
            sub_where_parts = []
            if time_range_clause:
                sub_where_parts.append(time_range_clause)
            if arr_cond is None:
                arr_cond = self._array_not_empty_condition(dim_resolved)
            if arr_cond:
                sub_where_parts.append(arr_cond)
            sub_where = ("WHERE " + " AND ".join(sub_where_parts)) if sub_where_parts else ""
            dim_limit_subquery = f"""{dim_sql} IN (
                SELECT {dim_sql} FROM {request.from_.table}
                {sub_where}
                GROUP BY {dim_sql}
                ORDER BY COUNT(*) DESC
                LIMIT %(dimension_limit)s
            )"""
            where_clauses.append(dim_limit_subquery)
            params['dimension_limit'] = request.options.dimensionLimit

    def _append_where(self, where_clause: str, condition: str) -> str:
        """Append a condition to a WHERE clause string, adding WHERE keyword if needed."""
        if where_clause.strip():
            return f"{where_clause} AND {condition}"
        return f"WHERE {condition}"

    def _build_filter_conditions(self, filters: List, param_prefix: str = "filter") -> Tuple[List[str], Dict[str, Any]]:
        """
        Build filter conditions as a list of SQL condition strings (without WHERE keyword).
        Helper for FILTER (WHERE ...) clauses in trend_percentage queries.
        """
        where_clause, params = self._build_where_clause(filters, param_prefix=param_prefix)
        if not where_clause:
            return [], params

        conditions_str = where_clause.replace("WHERE ", "", 1)
        return [conditions_str], params

    # ============================================================================
    # Comparison Queries (for Bar charts, Butterfly charts, Stacked Column)
    # ============================================================================

    def _build_comparison_query(self, request: ChartQueryRequest) -> Tuple[str, Dict[str, Any]]:
        """Build comparison query (side-by-side comparison of dimensions)."""
        params = {}
        x_resolved = self._resolver.resolve(request.select.xAxis)
        y_resolved = self._resolver.resolve(request.select.yAxis)

        x_expr = self._resolve_array_field(x_resolved)
        y_expr = self._resolve_array_field(y_resolved)

        # Build FROM clause with JOINs
        from_clause, join_params = self._build_from_clause(request.from_)
        params.update(join_params)

        # Build WHERE clause from user filters
        where_clause, filter_params = self._build_where_clause(request.filters)
        params.update(filter_params)

        # Exclude NULL/empty values for both fields
        for resolved in (x_resolved, y_resolved):
            arr_cond = self._array_not_empty_condition(resolved)
            null_filter = arr_cond if arr_cond else f"{resolved.where_expr()} IS NOT NULL"
            where_clause = self._append_where(where_clause, null_filter)

        group_limit = request.options.limit or 100
        segment_limit = request.options.segmentLimit
        params['group_limit'] = group_limit

        sort_by = request.options.sortBy or 'value'
        sort_order = request.options.sortOrder.value.upper()

        if segment_limit:
            params['segment_limit'] = segment_limit
            query = f"""
                WITH base AS (
                    SELECT {x_expr} as x_val, {y_expr} as y_val
                    {from_clause}
                    {where_clause}
                ),
                top_groups AS (
                    SELECT x_val,
                           ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) as rn
                    FROM base
                    GROUP BY x_val
                ),
                top_segments AS (
                    SELECT y_val,
                           ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) as rn
                    FROM base
                    GROUP BY y_val
                )
                SELECT
                    CASE WHEN tg.rn <= %(group_limit)s THEN b.x_val ELSE 'Other' END as x_axis,
                    CASE WHEN ts.rn <= %(segment_limit)s THEN b.y_val ELSE 'Other' END as y_axis,
                    COUNT(*) as value
                FROM base b
                JOIN top_groups tg ON b.x_val = tg.x_val
                JOIN top_segments ts ON b.y_val = ts.y_val
                GROUP BY
                    CASE WHEN tg.rn <= %(group_limit)s THEN b.x_val ELSE 'Other' END,
                    CASE WHEN ts.rn <= %(segment_limit)s THEN b.y_val ELSE 'Other' END
                ORDER BY {sort_by} {sort_order}
                LIMIT 5000
            """
        else:
            query = f"""
                WITH base AS (
                    SELECT {x_expr} as x_val, {y_expr} as y_val
                    {from_clause}
                    {where_clause}
                ),
                top_groups AS (
                    SELECT x_val,
                           ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) as rn
                    FROM base
                    GROUP BY x_val
                )
                SELECT
                    CASE WHEN tg.rn <= %(group_limit)s THEN b.x_val ELSE 'Other' END as x_axis,
                    b.y_val as y_axis,
                    COUNT(*) as value
                FROM base b
                JOIN top_groups tg ON b.x_val = tg.x_val
                GROUP BY
                    CASE WHEN tg.rn <= %(group_limit)s THEN b.x_val ELSE 'Other' END,
                    b.y_val
                ORDER BY {sort_by} {sort_order}
                LIMIT 5000
            """

        logger.debug(f"Comparison query built: group_limit={group_limit}, segment_limit={segment_limit}")
        return query.strip(), params

    # ============================================================================
    # Correlation Queries (for Sankey, Punchcard charts)
    # ============================================================================

    def _build_correlation_query(self, request: ChartQueryRequest) -> Tuple[str, Dict[str, Any]]:
        """Build correlation query (cross-tabulation of two dimensions)."""
        params = {}
        source_resolved = self._resolver.resolve(request.select.sourceField)
        target_resolved = self._resolver.resolve(request.select.targetField)

        source_expr = self._resolve_array_field(source_resolved)
        target_expr = self._resolve_array_field(target_resolved)

        # Build FROM clause with JOINs
        from_clause, join_params = self._build_from_clause(request.from_)
        params.update(join_params)

        # Build WHERE clause from user filters
        where_clause, filter_params = self._build_where_clause(request.filters)
        params.update(filter_params)

        # Exclude NULL/empty values for both fields
        for resolved in (source_resolved, target_resolved):
            arr_cond = self._array_not_empty_condition(resolved)
            null_filter = arr_cond if arr_cond else f"{resolved.where_expr()} IS NOT NULL"
            where_clause = self._append_where(where_clause, null_filter)

        row_limit = request.options.rowLimit or request.options.limit or 100
        column_limit = request.options.columnLimit or 100
        params['row_limit'] = row_limit
        params['column_limit'] = column_limit

        sort_by = request.options.sortBy or 'value'
        sort_order = request.options.sortOrder.value.upper()
        order_by_clause = f"ORDER BY {sort_by} {sort_order}"

        query = f"""
            WITH base AS (
                SELECT {source_expr} as src_val, {target_expr} as tgt_val
                {from_clause}
                {where_clause}
            ),
            top_sources AS (
                SELECT src_val,
                       ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) as rn
                FROM base
                GROUP BY src_val
            ),
            top_targets AS (
                SELECT tgt_val,
                       ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) as rn
                FROM base
                GROUP BY tgt_val
            )
            SELECT
                CASE WHEN ts.rn <= %(row_limit)s THEN b.src_val ELSE 'Other' END as source,
                CASE WHEN tt.rn <= %(column_limit)s THEN b.tgt_val ELSE 'Other' END as target,
                COUNT(*) as value
            FROM base b
            JOIN top_sources ts ON b.src_val = ts.src_val
            JOIN top_targets tt ON b.tgt_val = tt.tgt_val
            GROUP BY
                CASE WHEN ts.rn <= %(row_limit)s THEN b.src_val ELSE 'Other' END,
                CASE WHEN tt.rn <= %(column_limit)s THEN b.tgt_val ELSE 'Other' END
            {order_by_clause}
            LIMIT 5000
        """

        logger.debug(f"Correlation query built: row_limit={row_limit}, column_limit={column_limit}")
        return query.strip(), params

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def _translate_metric_to_sql(self, metric_spec: MetricSpec) -> str:
        """Translate a MetricSpec to a SQL aggregate expression.

        COUNT(*) is special-cased: it accepts a missing field_id. All other
        aggregates require a resolvable field_id.
        """
        if metric_spec is None:
            raise ValueError("Metric query requires a metric spec")

        agg = metric_spec.agg.value  # "COUNT", "SUM", "AVG", "MIN", "MAX", "COUNT_DISTINCT"

        if metric_spec.field_id is None:
            if agg == "COUNT":
                return "COUNT(*)"
            raise ValueError(f"Aggregate {agg} requires a field_id")

        resolved = self._resolver.resolve(metric_spec.field_id)
        col_expr = resolved.where_expr()
        if agg == "COUNT_DISTINCT":
            return f"COUNT(DISTINCT {col_expr})"
        return f"{agg}({col_expr})"

    def _build_from_clause(self, from_clause) -> Tuple[str, Dict[str, Any]]:
        """Build FROM clause with optional JOINs."""
        params = {}
        table = from_clause.table
        sql = f"FROM {table}"

        for join in from_clause.joins:
            join_type = join.type.value
            join_table = join.table
            join_condition = join.on
            sql += f"\n{join_type} JOIN {join_table} ON {join_condition}"

        return sql, params

    def _build_where_clause(self, filters: List, param_prefix: str = "filter") -> Tuple[str, Dict[str, Any]]:
        """
        Build WHERE clause from filter conditions.

        Each FilterCondition.field_id flows through FieldResolver:
        - `resolved.where_expr()` is the SQL expression (column, array subscript,
          JSONB path, etc.) — never a raw user string.
        - `resolved.is_array()` + `sql_kind == "column"` gate array-specific
          IS_NULL / IS_NOT_NULL handling.
        - `resolved.is_jsonb_sourced()` decides whether string comparisons
          need a ::text cast.
        - `isinstance(field_id, VirtualField)` routes confidence_level to
          `_build_cl_condition`.
        """
        if not filters:
            return "", {}

        params = {}
        conditions = []

        for i, filter_cond in enumerate(filters):
            field_id = filter_cond.field_id
            operator = filter_cond.operator
            value = filter_cond.value
            param_name = f"{param_prefix}_{i}_value"

            # Virtual field: confidence_level → numeric range on confidence column
            if isinstance(field_id, VirtualField):
                if field_id.name == 'confidence_level':
                    cl_condition = self._build_cl_condition(
                        operator, value, param_name, params
                    )
                    if cl_condition:
                        conditions.append(cl_condition)
                else:
                    # Resolver still runs so unknown virtuals surface as InvalidFieldError
                    self._resolver.resolve(field_id)
                    logger.warning(f"Unhandled virtual field in filter: {field_id.name}")
                continue

            # Resolve the field — InvalidFieldError propagates up to the caller.
            resolved = self._resolver.resolve(field_id)

            # For array columns, access first element using [1] in comparisons;
            # IS_NULL / IS_NOT_NULL below still use the raw column for array length checks.
            field_ref = self._resolve_array_field(resolved)
            raw_col = resolved.where_expr()
            is_array_column = resolved.is_native_array()
            jsonb_sourced = resolved.is_jsonb_sourced()

            def text_cast(expr: str, is_jsonb: bool) -> str:
                if is_jsonb:
                    return f"({expr})::text"
                return f"{expr}::text"

            if operator == FilterOperator.EQUALS:
                if jsonb_sourced:
                    conditions.append(f"{text_cast(field_ref, True)} = %({param_name})s")
                else:
                    conditions.append(f"{field_ref} = %({param_name})s")
                params[param_name] = value

            elif operator == FilterOperator.NOT_EQUALS:
                if jsonb_sourced:
                    conditions.append(f"{text_cast(field_ref, True)} != %({param_name})s")
                else:
                    conditions.append(f"{field_ref} != %({param_name})s")
                params[param_name] = value

            elif operator == FilterOperator.CONTAINS:
                conditions.append(f"{text_cast(field_ref, jsonb_sourced)} ILIKE %({param_name})s")
                params[param_name] = f"%{value}%"

            elif operator == FilterOperator.NOT_CONTAINS:
                conditions.append(f"{text_cast(field_ref, jsonb_sourced)} NOT ILIKE %({param_name})s")
                params[param_name] = f"%{value}%"

            elif operator == FilterOperator.STARTS_WITH:
                conditions.append(f"{text_cast(field_ref, jsonb_sourced)} ILIKE %({param_name})s")
                params[param_name] = f"{value}%"

            elif operator == FilterOperator.ENDS_WITH:
                conditions.append(f"{text_cast(field_ref, jsonb_sourced)} ILIKE %({param_name})s")
                params[param_name] = f"%{value}"

            elif operator == FilterOperator.GT:
                conditions.append(f"{field_ref} > %({param_name})s")
                params[param_name] = value

            elif operator == FilterOperator.GTE:
                conditions.append(f"{field_ref} >= %({param_name})s")
                params[param_name] = value

            elif operator == FilterOperator.LT:
                conditions.append(f"{field_ref} < %({param_name})s")
                params[param_name] = value

            elif operator == FilterOperator.LTE:
                conditions.append(f"{field_ref} <= %({param_name})s")
                params[param_name] = value

            elif operator == FilterOperator.IN:
                conditions.append(f"{field_ref} = ANY(%({param_name})s)")
                params[param_name] = value if isinstance(value, list) else [value]

            elif operator == FilterOperator.NOT_IN:
                conditions.append(f"{field_ref} != ALL(%({param_name})s)")
                params[param_name] = value if isinstance(value, list) else [value]

            elif operator == FilterOperator.IS_NULL:
                if is_array_column:
                    conditions.append(
                        f"({raw_col} IS NULL OR {raw_col} = '{{}}' "
                        f"OR array_length({raw_col}, 1) IS NULL)"
                    )
                else:
                    conditions.append(f"{raw_col} IS NULL")

            elif operator == FilterOperator.IS_NOT_NULL:
                if is_array_column:
                    conditions.append(
                        f"({raw_col} IS NOT NULL AND {raw_col} != '{{}}' "
                        f"AND array_length({raw_col}, 1) > 0)"
                    )
                else:
                    conditions.append(f"{raw_col} IS NOT NULL")

            elif operator == FilterOperator.BETWEEN:
                if isinstance(value, list) and len(value) == 2:
                    conditions.append(f"{field_ref} BETWEEN %({param_name}_min)s AND %({param_name}_max)s")
                    params[f"{param_name}_min"] = value[0]
                    params[f"{param_name}_max"] = value[1]
                else:
                    logger.warning(f"BETWEEN operator requires array [min, max], got: {value}")

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        return where_clause, params

    def _build_cl_condition(self, operator, value, param_name, params):
        """Translate confidence_level filter to numeric range on confidence column."""
        medium, high = self.confidence_thresholds

        def _level_sql(level, key_suffix):
            level = level.lower().strip() if isinstance(level, str) else str(level).lower()
            if level == 'high':
                params[f'{param_name}_{key_suffix}_h'] = high
                return f"confidence >= %({param_name}_{key_suffix}_h)s"
            elif level == 'medium':
                params[f'{param_name}_{key_suffix}_m'] = medium
                params[f'{param_name}_{key_suffix}_mh'] = high
                return f"(confidence >= %({param_name}_{key_suffix}_m)s AND confidence < %({param_name}_{key_suffix}_mh)s)"
            elif level == 'low':
                params[f'{param_name}_{key_suffix}_l'] = medium
                return (
                    f"(confidence < %({param_name}_{key_suffix}_l)s "
                    f"OR confidence IS NULL)"
                )
            return None

        if operator == FilterOperator.EQUALS:
            return _level_sql(value, 'eq')
        elif operator == FilterOperator.NOT_EQUALS:
            cond = _level_sql(value, 'neq')
            return f"NOT ({cond})" if cond else None
        elif operator == FilterOperator.IN:
            values = value if isinstance(value, list) else [value]
            parts = [_level_sql(v, f'in{i}') for i, v in enumerate(values)]
            parts = [p for p in parts if p]
            return f"({' OR '.join(parts)})" if parts else None
        logger.warning(f"Unsupported operator '{operator}' for virtual field confidence_level")
        return None

    def _parse_time_range(self, time_range: str) -> str:
        """
        Parse time range string (e.g., "7d", "24h", "30d") to PostgreSQL interval.
        """
        match = re.match(r'(\d+)([hdwmy])', time_range.lower())
        if not match:
            logger.warning(f"Invalid time range format: {time_range}, defaulting to 30 days")
            return "30 days"

        number, unit = match.groups()

        unit_map = {
            'h': 'hours',
            'd': 'days',
            'w': 'weeks',
            'm': 'months',
            'y': 'years'
        }

        pg_unit = unit_map.get(unit, 'days')
        return f"{number} {pg_unit}"
