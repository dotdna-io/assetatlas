# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Chart Repository

Executes chart queries against the database with connection pooling,
timeouts, and error handling.

Created: 2025-11-19
"""

from typing import Dict, List, Any, Tuple
from datetime import datetime
from decimal import Decimal
import time
import logging
import psycopg2

logger = logging.getLogger(__name__)


class ChartRepository:
    """
    Repository for executing chart data queries.

    Uses the existing PostgreSQL adapter with connection pooling
    to execute chart queries safely and efficiently.
    """

    # Query timeout in seconds (prevent long-running queries)
    QUERY_TIMEOUT = 30

    def __init__(self, db_adapter):
        """
        Initialize repository with database adapter.

        Args:
            db_adapter: PostgreSQLAdapter instance with connection pool
        """
        self.db = db_adapter
        logger.info("ChartRepository initialized")

    def execute_query(
        self,
        sql: str,
        params: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Execute chart query and return results.

        Args:
            sql: Parameterized SQL query string
            params: Query parameters dictionary

        Returns:
            Tuple of (results, execution_time_ms)
            - results: List of dictionaries with query results
            - execution_time_ms: Query execution time in milliseconds

        Raises:
            TimeoutError: If query exceeds timeout limit
            psycopg2.Error: If database error occurs
            Exception: For other unexpected errors
        """
        start_time = time.time()

        try:
            logger.debug(f"Executing chart query with {len(params)} parameters")
            logger.debug(f"SQL: {sql[:200]}...")  # Log first 200 chars

            # Execute the query (timeout is handled by PostgreSQL connection settings)
            results = self.db.execute_query(sql, params)

            # Calculate execution time
            execution_time = int((time.time() - start_time) * 1000)  # Convert to ms

            logger.info(
                f"Chart query executed successfully: "
                f"{len(results)} rows, {execution_time}ms"
            )

            return results, execution_time

        except psycopg2.extensions.QueryCanceledError as e:
            execution_time = int((time.time() - start_time) * 1000)
            logger.error(
                f"Query timeout after {execution_time}ms "
                f"(limit: {self.QUERY_TIMEOUT}s): {str(e)}"
            )
            raise TimeoutError(
                f"Query exceeded timeout limit of {self.QUERY_TIMEOUT} seconds"
            ) from e

        except psycopg2.Error as e:
            execution_time = int((time.time() - start_time) * 1000)
            logger.error(
                f"Database error after {execution_time}ms: {str(e)}",
                exc_info=True
            )
            # Re-raise with more context
            raise psycopg2.Error(f"Database query failed: {str(e)}") from e

        except Exception as e:
            execution_time = int((time.time() - start_time) * 1000)
            logger.error(
                f"Unexpected error executing chart query after {execution_time}ms: {str(e)}",
                exc_info=True
            )
            raise

    # Color palette for distribution charts (Material Design colors)
    # Ordered to match mock data: red, orange, blue, green, purple, then others
    CHART_COLORS = [
        '#EF4444',  # Red
        '#F97316',  # Orange
        '#3B82F6',  # Blue
        '#10B981',  # Green
        '#A855F7',  # Purple
        '#F59E0B',  # Amber
        '#8B5CF6',  # Violet
        '#EC4899',  # Pink
        '#14B8A6',  # Teal
        '#6366F1',  # Indigo
        '#06B6D4',  # Cyan
        '#84CC16',  # Lime
    ]

    # Predefined colors from mock data (specific values get consistent colors)
    # Categories take priority over subcategories
    PREDEFINED_COLORS = {
        'embedded_pc': '#D1D5DB',
        'firewall': '#374151',
        'gateway': '#D1D5DB',
        'iot': '#93C5FD',
        'laptop': '#374151',
        'miscellaneous': '#3B82F6',
        'mobile': '#2563EB',
        'network': '#0891B2',
        'ot': '#06B6D4',
        'phone': '#4B5563',
        'printer': '#1F2937',
        'server': '#60A5FA',
        'switch': '#E5E7EB',
        'thin_client': '#4B5563',
        'unknown': '#6B7280',
        'video_conference': '#9CA3AF',
        'workstation': '#1E40AF',
    }

    def transform_results(
        self,
        results: List[Dict[str, Any]],
        query_type: str
    ) -> List[Dict[str, Any]]:
        """
        Transform raw query results to chart-friendly format.

        Different chart types expect different data structures.
        This method handles common transformations like:
        - Converting None to null
        - Formatting dates
        - Converting Decimal to float
        - Adding default fields
        - For distribution queries: adding percentages and colors

        Args:
            results: Raw query results from database
            query_type: Type of query (distribution, metric, trend, etc.)

        Returns:
            Transformed results ready for chart rendering
        """
        # Basic data type transformations
        transformed = []

        for row in results:
            transformed_row = {}

            for key, value in row.items():
                # Handle different data types
                if value is None:
                    transformed_row[key] = None

                elif isinstance(value, datetime):
                    # Format datetime as ISO string
                    transformed_row[key] = value.isoformat()

                elif isinstance(value, Decimal):
                    # Convert Decimal to float for JSON serialization
                    transformed_row[key] = float(value)

                else:
                    transformed_row[key] = value

            transformed.append(transformed_row)

        # Query-type specific enhancements
        if query_type == 'distribution':
            transformed = self._enhance_distribution_results(transformed)
        elif query_type == 'metric':
            transformed = self._enhance_metric_results(transformed)
        elif query_type in ('trend', 'trend_percentage', 'history_trend'):
            transformed = self._enhance_trend_results(transformed)
        elif query_type == 'comparison':
            transformed = self._enhance_comparison_results(transformed)
        elif query_type == 'correlation':
            transformed = self._enhance_correlation_results(transformed)

        logger.debug(f"Transformed {len(transformed)} rows for {query_type} query")
        return transformed

    def _enhance_distribution_results(
        self,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Enhance distribution query results with percentages and colors.

        Adds:
        - percentage: Calculated from total values
        - color: Assigned from color palette based on position

        Args:
            results: Raw distribution query results

        Returns:
            Enhanced results with percentage and color fields
        """
        if not results:
            return results

        # Sort by value descending (largest first). The SQL builder already
        # applied options.limit and rolled overflow into a single 'Other'
        # (query_builder._build_distribution_query), so we do NOT re-cap here —
        # the wizard's "Max bars/slices" setting is authoritative (#946/#497).
        final_results = sorted(results, key=lambda x: x.get('value', 0), reverse=True)

        # Calculate total for percentage calculation
        total = sum(row.get('value', 0) for row in final_results)

        if total == 0:
            logger.warning("Distribution query total is 0, cannot calculate percentages")
            return results

        # Enhance each row with percentage and color
        enhanced = []
        for idx, row in enumerate(final_results):
            enhanced_row = row.copy()

            # Add percentage (rounded to 2 decimal places)
            value = row.get('value', 0)
            percentage = round((value / total) * 100, 2)
            enhanced_row['percentage'] = percentage

            # Add color: use CHART_COLORS palette in order (red, orange, blue, green, purple, ...)
            # Predefined colors disabled to allow sequential color assignment
            color_index = idx % len(self.CHART_COLORS)
            enhanced_row['color'] = self.CHART_COLORS[color_index]

            enhanced.append(enhanced_row)

        logger.debug(
            f"Enhanced {len(enhanced)} distribution results "
            f"(total={total}, percentages calculated)"
        )
        return enhanced

    def _enhance_metric_results(
        self,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Enhance metric query results with formatting and display information.

        Adds:
        - formattedValue: Human-readable formatted value with thousand separators
        - displayValue: Shortened format for large numbers (e.g., "5.4K", "2.3M")

        Args:
            results: Raw metric query results

        Returns:
            Enhanced results with formatting fields
        """
        if not results or len(results) == 0:
            return results

        # Metric queries typically return a single row with a 'value' field
        enhanced = []
        for row in results:
            enhanced_row = row.copy()

            # Get the value (should be numeric)
            value = row.get('value')

            # Convert string/Decimal values to float for processing
            numeric_value = None
            if value is not None:
                try:
                    if isinstance(value, (int, float)):
                        numeric_value = float(value)
                    elif isinstance(value, str):
                        # Handle Decimal values that come through as strings
                        numeric_value = float(value)
                except (ValueError, TypeError):
                    logger.warning(f"Could not convert value to numeric: {value}")

            if numeric_value is not None:
                # Add formatted value with thousand separators
                # For decimal values, format with 2 decimal places
                if numeric_value == int(numeric_value):
                    enhanced_row['formattedValue'] = f"{int(numeric_value):,}"
                else:
                    enhanced_row['formattedValue'] = f"{numeric_value:,.2f}"

                # Add display value (shortened for large numbers)
                enhanced_row['displayValue'] = self._format_display_value(numeric_value)

            enhanced.append(enhanced_row)

        logger.debug(f"Enhanced {len(enhanced)} metric results with formatting")
        return enhanced

    def _format_display_value(self, value: float) -> str:
        """
        Format a numeric value for compact display.

        Examples:
            1234 → "1.2K"
            1234567 → "1.2M"
            45 → "45"
            1500000 → "1.5M"

        Args:
            value: Numeric value to format

        Returns:
            Formatted string with K/M/B suffix for large numbers
        """
        if value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.1f}B"
        elif value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        elif value >= 1_000:
            return f"{value / 1_000:.1f}K"
        else:
            # For small numbers, show as integer if whole number, else 1 decimal
            if value == int(value):
                return str(int(value))
            else:
                return f"{value:.1f}"

    def _enhance_trend_results(
        self,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Enhance trend query results with series colors and metadata.

        For multi-series trends, assigns colors to each series.
        Ensures data is sorted by timestamp.

        Adds:
        - color: Assigned color for each series (multi-series only)

        Args:
            results: Raw trend query results

        Returns:
            Enhanced results with series colors
        """
        if not results:
            return results

        # Check if this is a multi-series trend (has 'series' field)
        is_multi_series = any('series' in row for row in results)

        if is_multi_series:
            # Extract unique series names and assign colors
            series_names = sorted(set(row.get('series') for row in results if row.get('series')))
            series_colors = {}
            for idx, series_name in enumerate(series_names):
                color_index = idx % len(self.CHART_COLORS)
                series_colors[series_name] = self.CHART_COLORS[color_index]

            # Add color to each data point based on series
            enhanced = []
            for row in results:
                enhanced_row = row.copy()
                series_name = row.get('series')
                if series_name:
                    enhanced_row['color'] = series_colors[series_name]
                enhanced.append(enhanced_row)

            logger.debug(
                f"Enhanced {len(enhanced)} trend results with {len(series_names)} series colors"
            )
            return enhanced
        else:
            # Single-series trend - no additional enhancement needed
            logger.debug(f"Single-series trend: {len(results)} data points")
            return results

    def _enhance_comparison_results(
        self,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Enhance comparison query results with color assignments.

        Assigns colors to x_axis values for consistent visualization
        in bar charts, stacked columns, and butterfly charts.

        Adds:
        - x_color: Color assigned to x_axis value
        - y_color: Color assigned to y_axis value (for stacked/grouped charts)

        Args:
            results: Raw comparison query results

        Returns:
            Enhanced results with color assignments
        """
        if not results:
            return results

        # Extract unique x_axis and y_axis values
        x_values = sorted(set(row.get('x_axis') for row in results if row.get('x_axis') is not None))
        y_values = sorted(set(row.get('y_axis') for row in results if row.get('y_axis') is not None))

        # Assign colors to x_axis values
        x_colors = {}
        for idx, x_value in enumerate(x_values):
            color_index = idx % len(self.CHART_COLORS)
            x_colors[x_value] = self.CHART_COLORS[color_index]

        # Assign colors to y_axis values (for stacked/grouped visualizations)
        y_colors = {}
        for idx, y_value in enumerate(y_values):
            color_index = idx % len(self.CHART_COLORS)
            y_colors[y_value] = self.CHART_COLORS[color_index]

        # Add colors to each data point
        enhanced = []
        for row in results:
            enhanced_row = row.copy()

            # Add x_axis color
            x_value = row.get('x_axis')
            if x_value and x_value in x_colors:
                enhanced_row['x_color'] = x_colors[x_value]

            # Add y_axis color
            y_value = row.get('y_axis')
            if y_value and y_value in y_colors:
                enhanced_row['y_color'] = y_colors[y_value]

            enhanced.append(enhanced_row)

        logger.debug(
            f"Enhanced {len(enhanced)} comparison results: "
            f"{len(x_values)} x-axis values, {len(y_values)} y-axis values"
        )
        return enhanced

    def _enhance_correlation_results(
        self,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Enhance correlation query results with color assignments for nodes.

        For Sankey diagrams and flow visualizations, assigns consistent colors
        to both source and target nodes for better visual distinction.

        Adds:
        - source_color: Color assigned to source node
        - target_color: Color assigned to target node

        Args:
            results: Raw correlation query results

        Returns:
            Enhanced results with color assignments for source and target nodes
        """
        if not results:
            return results

        # Extract unique source and target values
        # Handle target as string, list, or None
        source_values = sorted(set(row.get('source') for row in results if row.get('source') is not None))

        # For target, we need to handle different types (string, list, None)
        target_values_set = set()
        for row in results:
            target = row.get('target')
            if target is not None:
                if isinstance(target, list):
                    # If target is a list, add each element
                    for item in target:
                        if item:
                            target_values_set.add(item)
                elif target:
                    # If target is a string, add it directly
                    target_values_set.add(target)
        target_values = sorted(target_values_set)

        # Assign colors to source values
        source_colors = {}
        for idx, source_value in enumerate(source_values):
            color_index = idx % len(self.CHART_COLORS)
            source_colors[source_value] = self.CHART_COLORS[color_index]

        # Assign colors to target values
        # Use offset so target colors are different from source colors
        target_colors = {}
        offset = len(source_values)  # Start from where sources left off
        for idx, target_value in enumerate(target_values):
            color_index = (idx + offset) % len(self.CHART_COLORS)
            target_colors[target_value] = self.CHART_COLORS[color_index]

        # Add colors to each data point
        enhanced = []
        for row in results:
            enhanced_row = row.copy()

            # Add source color
            source_value = row.get('source')
            if source_value and source_value in source_colors:
                enhanced_row['source_color'] = source_colors[source_value]

            # Add target color(s)
            # If target is a list, add a list of colors
            # If target is a string, add a single color
            target_value = row.get('target')
            if target_value is not None:
                if isinstance(target_value, list):
                    # Map each target item to its color
                    target_color_list = []
                    for item in target_value:
                        if item and item in target_colors:
                            target_color_list.append(target_colors[item])
                    if target_color_list:
                        enhanced_row['target_color'] = target_color_list
                elif target_value in target_colors:
                    enhanced_row['target_color'] = target_colors[target_value]

            enhanced.append(enhanced_row)

        logger.debug(
            f"Enhanced {len(enhanced)} correlation results: "
            f"{len(source_values)} source nodes, {len(target_values)} target nodes"
        )
        return enhanced

    def get_row_count(self, sql: str, params: Dict[str, Any]) -> int:
        """
        Get total row count for a query (without LIMIT).

        Useful for pagination metadata.

        Args:
            sql: Original SQL query
            params: Query parameters

        Returns:
            Total number of rows matching the query

        Note:
            This removes LIMIT/OFFSET clauses and wraps the query in COUNT(*)
        """
        try:
            # Remove LIMIT and OFFSET from query
            count_sql = sql.split('LIMIT')[0]  # Remove everything after LIMIT

            # Wrap in COUNT(*)
            count_sql = f"SELECT COUNT(*) as total FROM ({count_sql}) as count_query"

            # Remove limit/offset from parameters
            count_params = {k: v for k, v in params.items() if k not in ['limit', 'offset']}

            # Execute count query
            result = self.db.execute_query(count_sql, count_params)
            total = result[0]['total'] if result else 0

            logger.debug(f"Total row count: {total}")
            return total

        except Exception as e:
            logger.warning(f"Failed to get row count: {str(e)}")
            # Return 0 if count fails (non-critical error)
            return 0
