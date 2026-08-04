# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Rule Execution Service for Tag Rules

Handles execution of tag rules to apply/remove tags on assets.

Key Features:
- Execute individual rules or all enabled rules
- Classification rule execution (simple tagging)
- Compliance rule execution (pass/fail evaluation)
- Tag lifecycle management (apply, remove, track)
- Execution history tracking
- Alert generation for compliance failures
- Integration with asset generation pipeline

Execution Patterns:
1. On rule creation → Execute immediately
2. On rule update → Execute that specific rule
3. During asset generation → Execute all enabled rules
"""

import logging
import time
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
import uuid

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.fields import FieldResolver
from src.modules.tag_rules.services.query_engine_service import QueryEngineService
from src.modules.tag_rules.services.rule_management_service import RuleManagementService
from src.modules.tag_rules.models.rule import TagRule
from src.modules.tag_rules.services.tags_column_service import refresh_tags_column
from src.api.utils.metrics import track_tag_rule_execution

logger = logging.getLogger(__name__)


class RuleExecutionService:
    """
    Service for executing tag rules on assets.

    Handles both classification and compliance rules, manages tag lifecycle,
    tracks execution history, and generates alerts.
    """

    def __init__(
        self,
        db_adapter: PostgreSQLAdapter,
        query_engine: QueryEngineService,
        resolver: FieldResolver,
        rule_mgmt: RuleManagementService,
    ):
        """
        Initialize rule execution service.

        Args:
            db_adapter: PostgreSQL database adapter
            query_engine: Shared QueryEngineService (injected)
            resolver: Shared FieldResolver (injected)
            rule_mgmt: Shared RuleManagementService (injected)
        """
        self.db = db_adapter
        self.query_engine = query_engine
        self._resolver = resolver
        self.rule_mgmt = rule_mgmt

    def execute_rule(
        self,
        rule_id: str,
        triggered_by: str = "manual",
        triggered_by_user: Optional[str] = None,
        generation_run_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute a single rule by ID.

        This is called:
        - After rule creation/update via API (triggered_by="api")
        - Manually via API (triggered_by="manual")
        - From scheduled jobs (triggered_by="scheduled")
        - From asset generation pipeline (triggered_by="asset_generation_pipeline")

        Args:
            rule_id: Rule ID to execute
            triggered_by: What triggered execution (api/manual/scheduled/asset_generation_pipeline)
            triggered_by_user: Optional user who triggered execution
            generation_run_id: Optional generation run ID

        Returns:
            Dictionary with execution results

        Example:
            result = service.execute_rule(
                "rule_1234567890_abc123",
                triggered_by="api",
                triggered_by_user="admin@example.com"
            )
            # Returns: {
            #     "execution_id": 12345,
            #     "status": "success",
            #     "assets_matched": 127,
            #     "assets_passed": 104,
            #     "assets_failed": 23,
            #     "tags_applied": 127,
            #     "tags_removed": 5,
            #     "execution_time_ms": 2340
            # }
        """
        start_time = time.time()
        execution_id = None
        rule = None

        def _emit_metric(status, added=0, removed=0):
            """Best-effort Prometheus emit — reads ``rule``/``rule_id`` from the
            enclosing scope and never breaks rule execution (#922). ``rule`` may
            still be None on the not-found path, so fall back to the id/unknown.
            """
            try:
                track_tag_rule_execution(
                    rule_id=rule_id,
                    rule_name=rule.name if rule is not None else rule_id,
                    rule_type=rule.type if rule is not None else "unknown",
                    status=status,
                    assignments_added=added,
                    assignments_removed=removed,
                )
            except Exception:
                logger.debug(
                    "tag-rule metric emit failed (non-fatal)", exc_info=True
                )

        try:
            # Get rule
            rule = self.rule_mgmt.get_rule(rule_id)
            if not rule:
                raise ValueError(f"Rule not found: {rule_id}")

            logger.info(f"Executing rule: {rule_id} ({rule.name}) - triggered by: {triggered_by}")

            # If rule is disabled, remove all its tags
            if not rule.enabled:
                logger.info(f"Rule {rule_id} is disabled, removing all tags")
                tags_removed = self._remove_all_tags_for_rule(rule_id)
                execution_time_ms = int((time.time() - start_time) * 1000)

                # Record execution history
                execution_id = self._record_execution_history(
                    rule_id=rule_id,
                    execution_time_ms=execution_time_ms,
                    status="success",
                    assets_matched=0,
                    assets_passed=None,
                    assets_failed=None,
                    tags_applied=0,
                    tags_removed=tags_removed,
                    triggered_by=triggered_by,
                    triggered_by_user=triggered_by_user,
                    generation_run_id=generation_run_id
                )

                logger.info(f"Rule {rule_id} disabled: removed {tags_removed} tags")

                _emit_metric("success", removed=tags_removed)
                return {
                    "status": "success",
                    "message": "Rule is disabled, all tags removed",
                    "execution_id": execution_id,
                    "assets_matched": 0,
                    "tags_applied": 0,
                    "tags_removed": tags_removed,
                    "execution_time_ms": execution_time_ms
                }

            # Execute based on rule type
            if rule.type == "classification":
                result = self._execute_classification_rule(rule)
            elif rule.type == "compliance":
                result = self._execute_compliance_rule(rule)
            else:
                raise ValueError(f"Unknown rule type: {rule.type}")

            execution_time_ms = int((time.time() - start_time) * 1000)

            # Record execution history
            execution_id = self._record_execution_history(
                rule_id=rule_id,
                execution_time_ms=execution_time_ms,
                status="success",
                assets_matched=result["assets_matched"],
                assets_passed=result.get("assets_passed"),
                assets_failed=result.get("assets_failed"),
                tags_applied=result["tags_applied"],
                tags_removed=result["tags_removed"],
                triggered_by=triggered_by,
                triggered_by_user=triggered_by_user,
                generation_run_id=generation_run_id
            )

            # Update rule statistics
            self._update_rule_statistics(rule_id, execution_time_ms, result)

            # Check for alert conditions (compliance rules only)
            if rule.type == "compliance" and rule.detection_config:
                self._check_and_generate_alerts(rule, result, execution_id)

            logger.info(
                f"Rule {rule_id} executed successfully: "
                f"{result['assets_matched']} matched, "
                f"{result['tags_applied']} tags applied, "
                f"{result['tags_removed']} tags removed in {execution_time_ms}ms"
            )

            _emit_metric(
                "success",
                added=result["tags_applied"],
                removed=result["tags_removed"],
            )
            return {
                "execution_id": execution_id,
                "status": "success",
                "execution_time_ms": execution_time_ms,
                **result
            }

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Error executing rule {rule_id}: {e}", exc_info=True)

            # Record failed execution
            if execution_id is None:
                execution_id = self._record_execution_history(
                    rule_id=rule_id,
                    execution_time_ms=execution_time_ms,
                    status="failed",
                    error_message=str(e),
                    assets_matched=0,
                    tags_applied=0,
                    tags_removed=0,
                    triggered_by=triggered_by,
                    triggered_by_user=triggered_by_user,
                    generation_run_id=generation_run_id
                )

            _emit_metric("failed")
            return {
                "execution_id": execution_id,
                "status": "failed",
                "error_message": str(e),
                "execution_time_ms": execution_time_ms
            }

    def execute_all_enabled_rules(
        self,
        triggered_by: str = "asset_generation_pipeline",
        generation_run_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute all enabled rules.

        This is called during asset generation pipeline to apply all
        active tag rules to assets.

        Args:
            triggered_by: What triggered execution
            generation_run_id: Optional generation run ID

        Returns:
            Dictionary with overall execution results
        """
        start_time = time.time()

        logger.info(f"Executing all enabled rules - triggered by: {triggered_by}")

        # Get all enabled rules
        rules_list = self.rule_mgmt.list_rules(enabled_only=True, limit=1000)

        if not rules_list.rules:
            logger.info("No enabled rules found")
            return {
                "status": "success",
                "rules_executed": 0,
                "success_count": 0,
                "failed_count": 0,
                "total_execution_time_ms": 0
            }

        # Execute each rule
        results = []
        success_count = 0
        failed_count = 0

        for rule in rules_list.rules:
            try:
                result = self.execute_rule(
                    rule_id=rule.id,
                    triggered_by=triggered_by,
                    generation_run_id=generation_run_id
                )
                results.append(result)

                if result["status"] == "success":
                    success_count += 1
                else:
                    failed_count += 1

            except Exception as e:
                logger.error(f"Error executing rule {rule.id}: {e}", exc_info=True)
                failed_count += 1

        # Refresh denormalized tags JSONB column once after all rules
        refresh_tags_column(self.db)

        total_execution_time_ms = int((time.time() - start_time) * 1000)

        logger.info(
            f"Executed {len(rules_list.rules)} rules in {total_execution_time_ms}ms: "
            f"{success_count} succeeded, {failed_count} failed"
        )

        return {
            "status": "success" if failed_count == 0 else "partial",
            "rules_executed": len(rules_list.rules),
            "success_count": success_count,
            "failed_count": failed_count,
            "total_execution_time_ms": total_execution_time_ms,
            "results": results
        }

    def _execute_classification_rule(self, rule: TagRule) -> Dict[str, Any]:
        """
        Execute a classification rule.

        Classification rules apply a single tag to all matching assets.

        Args:
            rule: Classification rule to execute

        Returns:
            Dictionary with execution results
        """
        target_filters = rule.target_filters

        # Build query to find matching assets
        query, params = self.query_engine.build_asset_query(
            target_filters,
            select_columns=["id"]
        )

        # Execute query
        results = self.db.execute_query(query, params)
        asset_ids = [row['id'] for row in results]

        assets_matched = len(asset_ids)

        if assets_matched == 0:
            logger.debug(f"No assets matched rule {rule.id}")
            # Remove any existing tags from this rule (assets no longer match)
            tags_removed = self._remove_all_tags_for_rule(rule.id)
            return {
                "assets_matched": 0,
                "tags_applied": 0,
                "tags_removed": tags_removed
            }

        # Apply tag to all matching assets
        tag_name = rule.tags["label"]
        tags_applied = self._apply_tags(
            asset_ids=asset_ids,
            tag_name=tag_name,
            rule_id=rule.id,
            tag_type="classification"
        )

        # Remove tags from assets that no longer match
        tags_removed = self._remove_tags_not_in_list(
            rule_id=rule.id,
            current_asset_ids=asset_ids
        )

        return {
            "assets_matched": assets_matched,
            "tags_applied": tags_applied,
            "tags_removed": tags_removed
        }

    def _execute_compliance_rule(self, rule: TagRule) -> Dict[str, Any]:
        """
        Execute a compliance rule.

        Compliance rules evaluate pass/fail criteria and apply
        appropriate tags based on the result.

        Args:
            rule: Compliance rule to execute

        Returns:
            Dictionary with execution results including pass/fail counts
        """
        target_filters = rule.target_filters

        # Build query to find matching assets
        # Need extended_attributes for compliance evaluation
        select_columns = ["id", "extended_attributes"]

        # Add any non-JSONB columns that compliance criteria references
        if rule.compliance_criteria:
            for condition in rule.compliance_criteria.conditions:
                try:
                    resolved = self._resolver.resolve(condition.field_id)
                    db_column = resolved.where_expr()

                    # Only add if it's a direct column (not JSONB path)
                    if "->" not in db_column and db_column not in select_columns:
                        select_columns.append(db_column)
                        logger.debug(
                            f"Added column '{db_column}' to SELECT for compliance field {condition.field_id!r}"
                        )
                except Exception as e:
                    logger.warning(f"Could not resolve compliance condition field: {e}")

        query, params = self.query_engine.build_asset_query(
            target_filters,
            select_columns=select_columns
        )

        # Execute query
        results = self.db.execute_query(query, params)
        assets_matched = len(results)

        if assets_matched == 0:
            logger.debug(f"No assets matched rule {rule.id}")
            tags_removed = self._remove_all_tags_for_rule(rule.id)
            return {
                "assets_matched": 0,
                "assets_passed": 0,
                "assets_failed": 0,
                "tags_applied": 0,
                "tags_removed": tags_removed
            }

        # Convert results to asset rows for evaluation
        # Include all selected columns (not just id and extended_attributes)
        asset_rows = []
        for row in results:
            asset_dict = {}
            for col in select_columns:
                asset_dict[col] = row.get(col)
            asset_rows.append(asset_dict)

        # Evaluate compliance
        compliance_results = self.query_engine.evaluate_compliance(
            asset_rows,
            rule.compliance_criteria.model_dump()
        )

        # Separate pass/fail asset IDs
        passed_asset_ids = [aid for aid, result in compliance_results.items() if result == "pass"]
        failed_asset_ids = [aid for aid, result in compliance_results.items() if result == "fail"]

        assets_passed = len(passed_asset_ids)
        assets_failed = len(failed_asset_ids)

        # Apply pass tags
        pass_tag_name = rule.tags["pass"]
        pass_tags_applied = self._apply_tags(
            asset_ids=passed_asset_ids,
            tag_name=pass_tag_name,
            rule_id=rule.id,
            tag_type="pass"
        )

        # Apply fail tags
        fail_tag_name = rule.tags["fail"]
        fail_tags_applied = self._apply_tags(
            asset_ids=failed_asset_ids,
            tag_name=fail_tag_name,
            rule_id=rule.id,
            tag_type="fail"
        )

        total_tags_applied = pass_tags_applied + fail_tags_applied

        # Remove tags from assets that no longer match the rule at all
        current_asset_ids = passed_asset_ids + failed_asset_ids
        tags_removed = self._remove_tags_not_in_list(
            rule_id=rule.id,
            current_asset_ids=current_asset_ids
        )

        return {
            "assets_matched": assets_matched,
            "assets_passed": assets_passed,
            "assets_failed": assets_failed,
            "tags_applied": total_tags_applied,
            "tags_removed": tags_removed
        }

    def _apply_tags(
        self,
        asset_ids: List[str],
        tag_name: str,
        rule_id: str,
        tag_type: str
    ) -> int:
        """
        Apply tags to assets using batch insert.

        Strategy: Complete replacement - all existing tags for this rule/tag_name
        combination are deleted first, then new tags are inserted.

        Both operations run inside a single connection scope so that the
        DELETE and INSERT are committed atomically.  If the INSERT fails
        the scoped connection is rolled back, preventing data loss (#71).

        Args:
            asset_ids: List of asset IDs to tag
            tag_name: Tag name to apply
            rule_id: Rule ID that generated the tag
            tag_type: Type of tag (classification, pass, fail)

        Returns:
            Number of tags applied
        """
        if not asset_ids:
            return 0

        conn_manager = self.db.conn_manager
        conn_manager.begin_scope()
        error = None
        try:
            # Step 1: Delete existing tags for this rule and tag_name combination
            # Reason: Complete replacement strategy - any change replaces all previous entries
            delete_query = """
                DELETE FROM asset_tags_v2
                WHERE tag_rule_id = %s
                  AND tag_name = %s
            """
            self.db.execute_query(delete_query, [rule_id, tag_name], fetch=False, commit=False)
            logger.debug(f"Deleted existing tags for rule {rule_id}, tag {tag_name}")

            # Step 2: Batch insert new tags (SINGLE QUERY)
            values_placeholders = ', '.join(
                ['(%s, %s, %s, %s, NOW())'] * len(asset_ids)
            )
            insert_query = f"""
                INSERT INTO asset_tags_v2 (
                    asset_id, tag_name, tag_rule_id, tag_type, applied_at
                )
                VALUES {values_placeholders}
            """

            # Flatten params: [asset_id1, tag_name, rule_id, tag_type, asset_id2, ...]
            insert_params = []
            for asset_id in asset_ids:
                insert_params.extend([asset_id, tag_name, rule_id, tag_type])

            self.db.execute_query(insert_query, insert_params, fetch=False, commit=False)
            logger.info(f"Inserted {len(asset_ids)} new tags for rule {rule_id}, tag {tag_name}")

            # Commit both operations atomically
            with conn_manager.get_connection() as conn:
                conn.commit()

            return len(asset_ids)

        except Exception as e:
            error = e
            logger.error(f"Error in batch tag application: {e}", exc_info=True)
            raise
        finally:
            conn_manager.end_scope(error=error)

    def _remove_tags_not_in_list(
        self,
        rule_id: str,
        current_asset_ids: List[str]
    ) -> int:
        """
        Remove tags from assets that no longer match the rule.

        Args:
            rule_id: Rule ID
            current_asset_ids: List of asset IDs that currently match

        Returns:
            Number of tags removed
        """
        if not current_asset_ids:
            # No assets match, remove all tags for this rule
            return self._remove_all_tags_for_rule(rule_id)

        try:
            # Delete tags for this rule that are NOT in the current list
            query = """
                DELETE FROM asset_tags_v2
                WHERE tag_rule_id = %(rule_id)s
                  AND asset_id NOT IN %(asset_ids)s
                RETURNING id
            """

            # Convert list to tuple for PostgreSQL IN clause
            result = self.db.execute_query(query, {
                "rule_id": rule_id,
                "asset_ids": tuple(current_asset_ids)
            })

            tags_removed = len(result) if result else 0
            return tags_removed

        except Exception as e:
            logger.error(f"Error removing outdated tags for rule {rule_id}: {e}", exc_info=True)
            return 0

    def _remove_all_tags_for_rule(self, rule_id: str) -> int:
        """
        Remove all tags for a rule.

        Args:
            rule_id: Rule ID

        Returns:
            Number of tags removed
        """
        try:
            query = """
                DELETE FROM asset_tags_v2
                WHERE tag_rule_id = %(rule_id)s
                RETURNING id
            """

            result = self.db.execute_query(query, {"rule_id": rule_id})
            tags_removed = len(result) if result else 0

            return tags_removed

        except Exception as e:
            logger.error(f"Error removing all tags for rule {rule_id}: {e}", exc_info=True)
            return 0

    def _record_execution_history(
        self,
        rule_id: str,
        execution_time_ms: int,
        status: str,
        assets_matched: int,
        tags_applied: int,
        tags_removed: int,
        triggered_by: str,
        assets_passed: Optional[int] = None,
        assets_failed: Optional[int] = None,
        error_message: Optional[str] = None,
        triggered_by_user: Optional[str] = None,
        generation_run_id: Optional[str] = None
    ) -> int:
        """
        Record execution history in database.

        Args:
            rule_id: Rule ID
            execution_time_ms: Execution time in milliseconds
            status: Execution status (success, failed, partial)
            assets_matched: Number of assets matched
            tags_applied: Number of tags applied
            tags_removed: Number of tags removed
            triggered_by: What triggered execution
            assets_passed: Assets passed (compliance only)
            assets_failed: Assets failed (compliance only)
            error_message: Error message if failed
            triggered_by_user: User who triggered
            generation_run_id: Generation run ID

        Returns:
            Execution history ID
        """
        try:
            query = """
                INSERT INTO rule_execution_history (
                    rule_id, executed_at, execution_time_ms, status, error_message,
                    assets_matched, assets_passed, assets_failed,
                    tags_applied, tags_removed,
                    triggered_by, triggered_by_user, generation_run_id
                )
                VALUES (
                    %(rule_id)s, NOW(), %(execution_time_ms)s, %(status)s, %(error_message)s,
                    %(assets_matched)s, %(assets_passed)s, %(assets_failed)s,
                    %(tags_applied)s, %(tags_removed)s,
                    %(triggered_by)s, %(triggered_by_user)s, %(generation_run_id)s
                )
                RETURNING id
            """

            result = self.db.execute_query(query, {
                "rule_id": rule_id,
                "execution_time_ms": execution_time_ms,
                "status": status,
                "error_message": error_message,
                "assets_matched": assets_matched,
                "assets_passed": assets_passed,
                "assets_failed": assets_failed,
                "tags_applied": tags_applied,
                "tags_removed": tags_removed,
                "triggered_by": triggered_by,
                "triggered_by_user": triggered_by_user,
                "generation_run_id": generation_run_id
            })

            execution_id = result[0]['id'] if result and len(result) > 0 else None
            return execution_id

        except Exception as e:
            logger.error(f"Error recording execution history: {e}", exc_info=True)
            return None

    def _update_rule_statistics(
        self,
        rule_id: str,
        execution_time_ms: int,
        result: Dict[str, Any]
    ):
        """
        Update rule statistics after execution.

        Args:
            rule_id: Rule ID
            execution_time_ms: Execution time
            result: Execution result dictionary
        """
        try:
            query = """
                UPDATE tag_rules
                SET last_executed_at = NOW(),
                    execution_count = execution_count + 1,
                    last_execution_duration_ms = %(execution_time_ms)s,
                    last_execution_assets_matched = %(assets_matched)s,
                    last_execution_assets_passed = %(assets_passed)s,
                    last_execution_assets_failed = %(assets_failed)s
                WHERE id = %(rule_id)s
            """

            self.db.execute_query(query, {
                "rule_id": rule_id,
                "execution_time_ms": execution_time_ms,
                "assets_matched": result.get("assets_matched", 0),
                "assets_passed": result.get("assets_passed"),
                "assets_failed": result.get("assets_failed")
            }, fetch=False)

        except Exception as e:
            logger.error(f"Error updating rule statistics: {e}", exc_info=True)

    def _check_and_generate_alerts(
        self,
        rule: TagRule,
        result: Dict[str, Any],
        execution_id: int
    ):
        """
        Check if alert conditions are met and generate alerts.

        Args:
            rule: Rule that was executed
            result: Execution result
            execution_id: Execution history ID
        """
        if not rule.detection_config or not rule.detection_config.enabled:
            return

        detection = rule.detection_config
        condition = detection.condition
        threshold = detection.threshold

        assets_matched = result.get("assets_matched", 0)
        assets_passed = result.get("assets_passed", 0)
        assets_failed = result.get("assets_failed", 0)

        should_alert = False
        actual_value = None

        # Check alert conditions
        if condition == "Pass rate below threshold":
            if assets_matched > 0:
                pass_rate = (assets_passed / assets_matched) * 100
                actual_value = pass_rate
                if pass_rate < threshold:
                    should_alert = True

        elif condition == "Fail rate above threshold":
            if assets_matched > 0:
                fail_rate = (assets_failed / assets_matched) * 100
                actual_value = fail_rate
                if fail_rate > threshold:
                    should_alert = True

        elif condition == "Pass count below threshold":
            actual_value = assets_passed
            if assets_passed < threshold:
                should_alert = True

        elif condition == "Fail count above threshold":
            actual_value = assets_failed
            if assets_failed > threshold:
                should_alert = True

        if should_alert:
            self._create_alert(
                rule_id=rule.id,
                execution_id=execution_id,
                condition=condition,
                threshold_value=threshold,
                actual_value=actual_value,
                severity=detection.severity
            )

    def _create_alert(
        self,
        rule_id: str,
        execution_id: int,
        condition: str,
        threshold_value: float,
        actual_value: float,
        severity: str
    ):
        """
        Create a detection alert.

        Args:
            rule_id: Rule ID
            execution_id: Execution history ID
            condition: Alert condition
            threshold_value: Threshold value
            actual_value: Actual value
            severity: Alert severity
        """
        try:
            # Check if there's already an active alert for this rule
            check_query = """
                SELECT id
                FROM detection_alerts
                WHERE rule_id = %(rule_id)s
                  AND status = 'active'
                LIMIT 1
            """

            existing = self.db.execute_query(check_query, {"rule_id": rule_id})

            if existing and len(existing) > 0:
                # Update existing alert
                update_query = """
                    UPDATE detection_alerts
                    SET triggered_at = NOW(),
                        execution_id = %(execution_id)s,
                        threshold_value = %(threshold_value)s,
                        actual_value = %(actual_value)s,
                        severity = %(severity)s
                    WHERE id = %(alert_id)s
                """
                self.db.execute_query(update_query, {
                    "alert_id": existing[0]['id'],
                    "execution_id": execution_id,
                    "threshold_value": threshold_value,
                    "actual_value": actual_value,
                    "severity": severity
                }, fetch=False)
            else:
                # Create new alert
                message = f"{condition}: threshold {threshold_value}, actual {actual_value:.1f}"

                insert_query = """
                    INSERT INTO detection_alerts (
                        rule_id, execution_id, triggered_at,
                        condition, threshold_value, actual_value,
                        severity, status, message
                    )
                    VALUES (
                        %(rule_id)s, %(execution_id)s, NOW(),
                        %(condition)s, %(threshold_value)s, %(actual_value)s,
                        %(severity)s, 'active', %(message)s
                    )
                """

                self.db.execute_query(insert_query, {
                    "rule_id": rule_id,
                    "execution_id": execution_id,
                    "condition": condition,
                    "threshold_value": threshold_value,
                    "actual_value": actual_value,
                    "severity": severity,
                    "message": message
                }, fetch=False)

                logger.warning(
                    f"Alert generated for rule {rule_id}: {message} (severity: {severity})"
                )

        except Exception as e:
            logger.error(f"Error creating alert: {e}", exc_info=True)
