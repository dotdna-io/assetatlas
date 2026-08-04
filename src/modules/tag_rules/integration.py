# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration module for Tag Rules with Asset Generation Pipeline

This module provides a simple interface for the asset generation pipeline
to trigger tag rule execution automatically after assets are saved.

Integration Status: ✅ ENABLED
- Automatically executes after Stage 9 (Save Assets & Providers)
- Runs as Stage 9.5 in the 12-stage asset generation pipeline
- Does not fail the entire pipeline if tag rules fail
- Triggered by: asset_generation_pipeline
- Tracks: generation_run_id for audit trail

Integration in asset_generation_service.py:
    Line 37: from src.modules.tag_rules.integration import execute_tag_rules_after_asset_generation
    Line 637-664: Stage 9.5 - Tag Rules Execution

Pipeline Flow:
    Stage 9: Save Assets & Providers → Stage 9.5: Execute Tag Rules → Stage 10: Change Tracking

Error Handling:
- Tag rule failures are logged but don't stop asset generation
- Each rule execution is tracked independently
- Failed rules are reported in the statistics

Last Updated: 2025-11-03
"""

import logging
from typing import Optional, Dict, Any
from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.shared.field_resolver_factory import build_field_resolver
from src.modules.tag_rules.services.query_engine_service import QueryEngineService
from src.modules.tag_rules.services.rule_execution_service import RuleExecutionService
from src.modules.tag_rules.services.rule_management_service import RuleManagementService
from src.modules.tag_rules.services.tags_column_service import refresh_tags_column

logger = logging.getLogger(__name__)


def execute_tag_rules_after_asset_generation(
    db_adapter: PostgreSQLAdapter,
    generation_run_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute all enabled tag rules after asset generation.

    This is the main integration point called from the asset generation pipeline.

    Args:
        db_adapter: Database adapter instance
        generation_run_id: Generation run ID for tracking

    Returns:
        Dictionary with execution results

    Example:
        result = execute_tag_rules_after_asset_generation(
            db_adapter=self.db_adapter,
            generation_run_id="550e8400-e29b-41d4-a716-446655440000"
        )
        # Returns: {
        #     "status": "success",
        #     "rules_executed": 5,
        #     "success_count": 5,
        #     "failed_count": 0,
        #     "total_execution_time_ms": 1234
        # }
    """
    logger.info(
        f"Executing tag rules after asset generation "
        f"(generation_run_id: {generation_run_id})"
    )

    try:
        resolver = build_field_resolver(db_adapter)
        query_engine = QueryEngineService(resolver=resolver)
        rule_mgmt = RuleManagementService(db_adapter)
        execution_service = RuleExecutionService(
            db_adapter=db_adapter,
            query_engine=query_engine,
            resolver=resolver,
            rule_mgmt=rule_mgmt,
        )

        result = execution_service.execute_all_enabled_rules(
            triggered_by="asset_generation_pipeline",
            generation_run_id=generation_run_id,
        )

        # Refresh denormalized tags JSONB column
        refresh_result = refresh_tags_column(db_adapter)
        if refresh_result["status"] != "success":
            logger.warning(f"Tags column refresh failed: {refresh_result.get('error')}")

        logger.info(
            f"Tag rules execution completed: {result['rules_executed']} rules, "
            f"{result['success_count']} succeeded, {result['failed_count']} failed "
            f"in {result['total_execution_time_ms']}ms"
        )

        return result

    except Exception as e:
        logger.error(f"Error executing tag rules: {e}", exc_info=True)
        return {
            "status": "failed",
            "error": str(e),
            "rules_executed": 0,
            "success_count": 0,
            "failed_count": 0,
            "total_execution_time_ms": 0
        }
