# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Preview Service for Tag Rules

Provides real-time asset counting for the UI preview functionality.
Optimized for fast response times (<1 second for 100k+ assets).

Key Features:
- Fast COUNT queries (no full data retrieval)
- Compliance pass/fail preview counts
- Performance monitoring
- Error handling
"""

import logging
import time
from typing import Dict, List, Any, Optional, Tuple

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.fields import FieldResolver, coerce_field_id
from src.modules.tag_rules.services.query_engine_service import QueryEngineService

logger = logging.getLogger(__name__)


class PreviewService:
    """
    Service for counting assets matching filter criteria.

    Used by the UI preview endpoint to show asset counts in real-time
    as users build their filter rules.
    """

    def __init__(self, db_adapter: PostgreSQLAdapter, query_engine: QueryEngineService, resolver: FieldResolver):
        """
        Initialize preview service.

        Args:
            db_adapter: PostgreSQL database adapter
            query_engine: Shared QueryEngineService (injected)
            resolver: Shared FieldResolver (injected)
        """
        self.db = db_adapter
        self.query_engine = query_engine
        self._resolver = resolver

    def count_matching_assets(
        self,
        target_filters: List
    ) -> Tuple[int, int]:
        """
        Count assets matching target filters.

        Args:
            target_filters: List of FilterCondition models or dicts

        Returns:
            Tuple of (count, execution_time_ms)

        Example:
            count, time_ms = service.count_matching_assets([
                {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"}
            ])
            # Returns: (127, 45)
        """
        start_time = time.time()

        try:
            # Build COUNT query
            query, params = self.query_engine.build_count_query(target_filters)

            # Execute query
            result = self.db.execute_query(query, params)

            # Extract count from result
            count = 0
            if result and len(result) > 0:
                # Result is a dictionary with 'count' key (RealDictCursor)
                count = result[0].get('count', 0)

            execution_time_ms = int((time.time() - start_time) * 1000)

            logger.debug(
                f"Count query executed in {execution_time_ms}ms: {count} assets matched"
            )

            return count, execution_time_ms

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Error counting assets: {e}", exc_info=True)
            raise

    def preview_compliance(
        self,
        target_filters: List,
        compliance_criteria: Dict[str, Any]
    ) -> Tuple[int, int, int, int]:
        """
        Count assets matching filters and evaluate compliance.

        This provides pass/fail counts for the UI preview.

        Args:
            target_filters: Filters to match target assets
            compliance_criteria: Compliance criteria to evaluate

        Returns:
            Tuple of (total_matching, pass_count, fail_count, execution_time_ms)
        """
        start_time = time.time()

        try:
            # Step 1: Get matching assets
            # We need to fetch actual assets to evaluate compliance
            # Use select_columns to minimize data transfer
            select_columns = ["id", "extended_attributes"]

            # Add direct DB columns referenced by compliance criteria conditions
            # (JSONB fields are already included via extended_attributes)
            conditions = compliance_criteria.get("conditions", [])
            for condition in conditions:
                # Extract field_id (handles both dict and Pydantic model conditions)
                if isinstance(condition, dict):
                    field_id_raw = condition.get("field_id")
                else:
                    field_id_raw = getattr(condition, "field_id", None)

                if not field_id_raw:
                    continue

                try:
                    field_id = coerce_field_id(field_id_raw)
                    resolved = self._resolver.resolve(field_id)
                    db_column = resolved.where_expr()
                    if "->" not in db_column and db_column not in select_columns:
                        select_columns.append(db_column)
                except Exception as e:
                    logger.warning(f"Could not resolve field_id for compliance column: {e}")

            logger.debug(f"Select columns for compliance: {select_columns}")

            query, params = self.query_engine.build_asset_query(
                target_filters,
                select_columns=select_columns
            )

            # Execute query to get assets
            results = self.db.execute_query(query, params)

            total_matching = len(results)

            if total_matching == 0:
                execution_time_ms = int((time.time() - start_time) * 1000)
                return 0, 0, 0, execution_time_ms

            # Step 2: Convert results to list of dicts
            # Results are already dictionaries (RealDictCursor), so just use them directly
            asset_rows = []
            for row in results:
                asset_dict = {
                    "id": row.get("id"),
                    "extended_attributes": row.get("extended_attributes", {})
                }
                # Add any additional columns
                for col_name in select_columns[2:]:
                    if col_name in row:
                        asset_dict[col_name] = row[col_name]

                asset_rows.append(asset_dict)

            # Step 3: Evaluate compliance
            compliance_results = self.query_engine.evaluate_compliance(
                asset_rows,
                compliance_criteria
            )

            # Step 4: Count pass/fail
            pass_count = sum(1 for result in compliance_results.values() if result == "pass")
            fail_count = sum(1 for result in compliance_results.values() if result == "fail")

            execution_time_ms = int((time.time() - start_time) * 1000)

            logger.debug(
                f"Compliance preview executed in {execution_time_ms}ms: "
                f"{total_matching} total, {pass_count} pass, {fail_count} fail"
            )

            # Log warning if query is slow
            if execution_time_ms > 1000:
                logger.warning(
                    f"Slow compliance preview query: {execution_time_ms}ms for {total_matching} assets"
                )

            return total_matching, pass_count, fail_count, execution_time_ms

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Error in compliance preview: {e}", exc_info=True)
            raise

    def preview_rule(
        self,
        rule_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Preview a complete rule (convenience method).

        Args:
            rule_data: Complete rule data including target_filters and optional compliance_criteria

        Returns:
            Dictionary with preview results
        """
        rule_type = rule_data.get("type")
        target_filters = rule_data.get("target_filters", [])
        compliance_criteria = rule_data.get("compliance_criteria")

        if rule_type == "compliance" and compliance_criteria:
            # Compliance rule with pass/fail evaluation
            total, passed, failed, time_ms = self.preview_compliance(
                target_filters,
                compliance_criteria
            )

            return {
                "total_matching": total,
                "pass_count": passed,
                "fail_count": failed,
                "execution_time_ms": time_ms
            }
        else:
            # Classification rule or compliance without criteria
            count, time_ms = self.count_matching_assets(target_filters)

            return {
                "total_matching": count,
                "pass_count": None,
                "fail_count": None,
                "execution_time_ms": time_ms
            }

    def validate_performance(
        self,
        target_filters: List,
        expected_max_ms: int = 1000
    ) -> Dict[str, Any]:
        """
        Validate query performance meets expectations.

        Args:
            target_filters: Filters to test
            expected_max_ms: Maximum acceptable execution time in milliseconds

        Returns:
            Dictionary with performance metrics
        """
        count, time_ms = self.count_matching_assets(target_filters)

        meets_target = time_ms <= expected_max_ms

        return {
            "execution_time_ms": time_ms,
            "meets_performance_target": meets_target,
            "asset_count": count,
            "expected_max_ms": expected_max_ms
        }
