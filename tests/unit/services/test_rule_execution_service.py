# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for RuleExecutionService

Tests execute_rule() for classification/compliance rules,
disabled rules, rule-not-found, different triggered_by values,
and execute_all_enabled_rules().
"""

from unittest.mock import MagicMock, patch
from datetime import datetime
import pytest

from src.modules.tag_rules.models.rule import (
    TagRule,
    TagRulesList,
    FilterCondition,
    ComplianceCriteria,
    DetectionConfig,
)


def _make_classification_rule(**overrides):
    """Build a minimal TagRule for classification tests."""
    defaults = dict(
        id="rule_cls_001",
        name="Test Classification",
        description="test",
        type="classification",
        enabled=True,
        target_filters=[
            FilterCondition(
                field_id={"kind": "static", "name": "OS Normalized"},
                operator="equals",
                value="Windows",
            )
        ],
        tags={"label": "Win-Tag", "color": "#0078d4"},
        compliance_criteria=None,
        detection_config=None,
        created_at=datetime(2025, 1, 1),
        last_modified=datetime(2025, 1, 1),
    )
    defaults.update(overrides)
    return TagRule(**defaults)


def _make_compliance_rule(**overrides):
    """Build a minimal TagRule for compliance tests."""
    defaults = dict(
        id="rule_cmp_001",
        name="Test Compliance",
        description="test",
        type="compliance",
        enabled=True,
        target_filters=[
            FilterCondition(
                field_id={"kind": "static", "name": "OS Normalized"},
                operator="equals",
                value="Windows",
            )
        ],
        tags={"pass": "AV-OK", "fail": "AV-Missing"},
        compliance_criteria=ComplianceCriteria(
            logic="any",
            conditions=[
                FilterCondition(
                    field_id={"kind": "extended_attribute", "source": "CrowdStrike", "attribute": "software"},
                    operator="contains",
                    value="Defender",
                )
            ],
        ),
        detection_config=None,
        created_at=datetime(2025, 1, 1),
        last_modified=datetime(2025, 1, 1),
    )
    defaults.update(overrides)
    return TagRule(**defaults)


class TestRuleExecutionService:
    """Tests for RuleExecutionService."""

    @pytest.fixture
    def service(self):
        """Create a RuleExecutionService with all dependencies injected as mocks."""
        db_adapter = MagicMock()
        conn_manager = MagicMock()
        db_adapter.conn_manager = conn_manager

        query_engine = MagicMock()
        resolver = MagicMock()
        rule_mgmt = MagicMock()

        from src.modules.tag_rules.services.rule_execution_service import RuleExecutionService
        svc = RuleExecutionService(
            db_adapter=db_adapter,
            query_engine=query_engine,
            resolver=resolver,
            rule_mgmt=rule_mgmt,
        )

        return svc, db_adapter, query_engine, rule_mgmt

    # ------------------------------------------------------------------
    # execute_rule — classification (happy path)
    # ------------------------------------------------------------------
    def test_execute_classification_rule_success(self, service):
        svc, db, qe, rm = service
        rule = _make_classification_rule()
        rm.get_rule.return_value = rule

        # query_engine builds query, db returns matching asset IDs
        qe.build_asset_query.return_value = ("SELECT id FROM assets WHERE ...", {})
        db.execute_query.side_effect = [
            # First call: asset query returns 3 matched assets
            [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}],
            # _apply_tags: DELETE existing tags (fetch=False via conn scope)
            None,
            # _apply_tags: INSERT new tags (fetch=False via conn scope)
            None,
            # _remove_tags_not_in_list: DELETE returns 1 removed
            [{"id": 99}],
            # _record_execution_history: INSERT RETURNING id
            [{"id": 101}],
            # _update_rule_statistics: UPDATE (fetch=False)
            None,
        ]

        result = svc.execute_rule("rule_cls_001", triggered_by="manual")

        assert result["status"] == "success"
        assert result["execution_id"] == 101
        assert result["assets_matched"] == 3
        assert result["tags_applied"] == 3
        assert result["tags_removed"] == 1
        assert "execution_time_ms" in result
        rm.get_rule.assert_called_once_with("rule_cls_001")

    # ------------------------------------------------------------------
    # execute_rule — Prometheus metric wiring (#922)
    # ------------------------------------------------------------------
    def test_execute_rule_emits_prometheus_metric(self, service):
        """A successful execution moves the tag_rule_executions counter for the
        rule's labels off zero (drives the real execute_rule path)."""
        from src.api.utils import metrics as m

        svc, db, qe, rm = service
        # Unique id/name so the labelled counter starts at a clean zero.
        rule = _make_classification_rule(id="rule_metrics_unique", name="Metrics Rule")
        rm.get_rule.return_value = rule
        qe.build_asset_query.return_value = ("SELECT id FROM assets WHERE ...", {})
        db.execute_query.side_effect = [
            [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}],  # matched assets
            None,  # _apply_tags DELETE
            None,  # _apply_tags INSERT
            [{"id": 99}],  # _remove_tags_not_in_list
            [{"id": 101}],  # _record_execution_history
            None,  # _update_rule_statistics
        ]

        def _count(**labels):
            for family in m.tag_rule_executions.collect():
                for s in family.samples:
                    if s.name == "assetatlas_tag_rule_executions_total" and all(
                        s.labels.get(k) == v for k, v in labels.items()
                    ):
                        return s.value
            return 0.0

        result = svc.execute_rule("rule_metrics_unique", triggered_by="manual")

        assert result["status"] == "success"
        assert (
            _count(
                rule_id="rule_metrics_unique",
                rule_name="Metrics Rule",
                rule_type="classification",
                status="success",
            )
            == 1.0
        )

    # ------------------------------------------------------------------
    # execute_rule — rule not found
    # ------------------------------------------------------------------
    def test_execute_rule_not_found(self, service):
        svc, db, qe, rm = service
        rm.get_rule.return_value = None

        # _record_execution_history for the failed execution
        db.execute_query.return_value = [{"id": 200}]

        result = svc.execute_rule("rule_missing")

        assert result["status"] == "failed"
        assert "not found" in result["error_message"].lower()
        assert result["execution_id"] == 200

    # ------------------------------------------------------------------
    # execute_rule — disabled rule removes all tags
    # ------------------------------------------------------------------
    def test_execute_disabled_rule_removes_tags(self, service):
        svc, db, qe, rm = service
        rule = _make_classification_rule(enabled=False)
        rm.get_rule.return_value = rule

        db.execute_query.side_effect = [
            # _remove_all_tags_for_rule: DELETE RETURNING
            [{"id": 1}, {"id": 2}],
            # _record_execution_history
            [{"id": 300}],
        ]

        result = svc.execute_rule("rule_cls_001")

        assert result["status"] == "success"
        assert result["tags_removed"] == 2
        assert result["assets_matched"] == 0
        assert result["tags_applied"] == 0
        assert "disabled" in result["message"].lower()

    # ------------------------------------------------------------------
    # execute_rule — different triggered_by values
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "triggered_by",
        ["manual", "api", "scheduled", "asset_generation_pipeline"],
    )
    def test_execute_rule_triggered_by_values(self, service, triggered_by):
        svc, db, qe, rm = service
        rule = _make_classification_rule()
        rm.get_rule.return_value = rule

        qe.build_asset_query.return_value = ("SELECT ...", {})
        # No assets matched — simplest path
        db.execute_query.side_effect = [
            [],          # asset query → 0 matches
            [],          # _remove_all_tags_for_rule
            [{"id": 1}], # _record_execution_history
            None,         # _update_rule_statistics
        ]

        result = svc.execute_rule("rule_cls_001", triggered_by=triggered_by)

        assert result["status"] == "success"

    # ------------------------------------------------------------------
    # execute_rule — compliance rule (happy path)
    # ------------------------------------------------------------------
    def test_execute_compliance_rule_success(self, service):
        svc, db, qe, rm = service
        rule = _make_compliance_rule()
        rm.get_rule.return_value = rule

        # resolver.resolve used for select_columns — return mock that gives no extra column
        svc._resolver.resolve.return_value.where_expr.return_value = "extended_attributes->'CrowdStrike'->>'software'"

        qe.build_asset_query.return_value = ("SELECT ...", {})
        qe.evaluate_compliance.return_value = {
            "a1": "pass",
            "a2": "fail",
            "a3": "pass",
        }

        db.execute_query.side_effect = [
            # asset query returns 3 rows
            [
                {"id": "a1", "extended_attributes": {}},
                {"id": "a2", "extended_attributes": {}},
                {"id": "a3", "extended_attributes": {}},
            ],
            # _apply_tags (pass): DELETE
            None,
            # _apply_tags (pass): INSERT
            None,
            # _apply_tags (fail): DELETE
            None,
            # _apply_tags (fail): INSERT
            None,
            # _remove_tags_not_in_list: DELETE
            [],
            # _record_execution_history
            [{"id": 400}],
            # _update_rule_statistics
            None,
        ]

        result = svc.execute_rule("rule_cmp_001")

        assert result["status"] == "success"
        assert result["assets_matched"] == 3
        assert result["assets_passed"] == 2
        assert result["assets_failed"] == 1

    # ------------------------------------------------------------------
    # execute_rule — exception during execution records failure
    # ------------------------------------------------------------------
    def test_execute_rule_exception_records_failure(self, service):
        svc, db, qe, rm = service
        rule = _make_classification_rule()
        rm.get_rule.return_value = rule

        qe.build_asset_query.side_effect = RuntimeError("query engine exploded")

        # _record_execution_history for the failed execution
        db.execute_query.return_value = [{"id": 500}]

        result = svc.execute_rule("rule_cls_001")

        assert result["status"] == "failed"
        assert "query engine exploded" in result["error_message"]
        assert result["execution_id"] == 500

    # ------------------------------------------------------------------
    # execute_all_enabled_rules — no enabled rules
    # ------------------------------------------------------------------
    def test_execute_all_no_rules(self, service):
        svc, db, qe, rm = service
        rm.list_rules.return_value = TagRulesList(rules=[], total=0)

        result = svc.execute_all_enabled_rules()

        assert result["status"] == "success"
        assert result["rules_executed"] == 0
        # The no-rules early return must expose the SAME count keys the pipeline
        # caller logs (integration.py:104-108, direct key access) — otherwise that
        # logging raises KeyError and the tag-rules stage is recorded as a spurious
        # failure on a run that actually succeeded.
        assert result["success_count"] == 0
        assert result["failed_count"] == 0
        assert result["total_execution_time_ms"] == 0

    # ------------------------------------------------------------------
    # execute_all_enabled_rules — multiple rules
    # ------------------------------------------------------------------
    def test_execute_all_multiple_rules(self, service):
        svc, db, qe, rm = service

        rules = [
            _make_classification_rule(id="rule_1"),
            _make_classification_rule(id="rule_2"),
        ]
        rm.list_rules.return_value = TagRulesList(rules=rules, total=2)

        # Patch execute_rule to avoid deep mocking
        with patch.object(svc, "execute_rule") as mock_exec:
            mock_exec.return_value = {"status": "success"}
            result = svc.execute_all_enabled_rules(
                triggered_by="asset_generation_pipeline",
                generation_run_id="gen-run-001",
            )

        assert result["rules_executed"] == 2
        assert result["success_count"] == 2
        assert result["failed_count"] == 0
        assert result["status"] == "success"

    # ------------------------------------------------------------------
    # execute_all_enabled_rules — partial failure
    # ------------------------------------------------------------------
    def test_execute_all_partial_failure(self, service):
        svc, db, qe, rm = service

        rules = [
            _make_classification_rule(id="rule_ok"),
            _make_classification_rule(id="rule_bad"),
        ]
        rm.list_rules.return_value = TagRulesList(rules=rules, total=2)

        call_count = 0

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"status": "success"}
            return {"status": "failed", "error_message": "boom"}

        with patch.object(svc, "execute_rule", side_effect=_side_effect):
            result = svc.execute_all_enabled_rules()

        assert result["status"] == "partial"
        assert result["success_count"] == 1
        assert result["failed_count"] == 1

    # ------------------------------------------------------------------
    # execute_rule with triggered_by_user and generation_run_id
    # ------------------------------------------------------------------
    def test_execute_rule_passes_user_and_run_id(self, service):
        svc, db, qe, rm = service
        rule = _make_classification_rule()
        rm.get_rule.return_value = rule

        qe.build_asset_query.return_value = ("SELECT ...", {})
        db.execute_query.side_effect = [
            [],           # no matches
            [],           # _remove_all_tags_for_rule
            [{"id": 600}], # _record_execution_history
            None,          # _update_rule_statistics
        ]

        result = svc.execute_rule(
            "rule_cls_001",
            triggered_by="api",
            triggered_by_user="admin@example.com",
            generation_run_id="gen-123",
        )

        assert result["status"] == "success"
        assert result["execution_id"] == 600
