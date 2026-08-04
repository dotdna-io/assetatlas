# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for Preview Service

Tests the preview/count functionality for the Tag Rules system.
All database operations are mocked.
"""

import unittest
from unittest.mock import Mock, MagicMock, patch
from src.modules.tag_rules.services.preview_service import PreviewService


def _make_service(mock_db=None):
    """Build a PreviewService with mocked query_engine and resolver."""
    if mock_db is None:
        mock_db = Mock()
    mock_qe = MagicMock()
    mock_resolver = MagicMock()
    svc = PreviewService(mock_db, query_engine=mock_qe, resolver=mock_resolver)
    return svc, mock_db, mock_qe, mock_resolver


class TestPreviewServiceCountMatching(unittest.TestCase):
    """Test count_matching_assets functionality"""

    def setUp(self):
        """Set up test fixtures"""
        self.service, self.mock_db, self.mock_qe, self.mock_resolver = _make_service()

    def test_count_matching_assets_simple(self):
        """Test basic asset counting"""
        self.mock_qe.build_count_query.return_value = (
            "SELECT COUNT(*) FROM assets WHERE LOWER(os_normalized) = LOWER(%(param_0_0)s)",
            {"param_0_0": "Windows"}
        )
        self.mock_db.execute_query.return_value = [{"count": 127}]

        target_filters = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"}]
        count, time_ms = self.service.count_matching_assets(target_filters)

        self.assertEqual(count, 127)
        self.assertIsInstance(time_ms, int)
        self.assertGreaterEqual(time_ms, 0)
        self.mock_qe.build_count_query.assert_called_once_with(target_filters)
        self.mock_db.execute_query.assert_called_once()

    def test_count_matching_assets_zero_results(self):
        """Test counting when no assets match"""
        self.mock_qe.build_count_query.return_value = ("SELECT COUNT(*) FROM assets WHERE 1=0", {})
        self.mock_db.execute_query.return_value = [{"count": 0}]

        target_filters = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "NonExistent"}]
        count, time_ms = self.service.count_matching_assets(target_filters)

        self.assertEqual(count, 0)
        self.assertIsInstance(time_ms, int)

    def test_count_matching_assets_empty_result(self):
        """Test counting when DB returns empty list"""
        self.mock_qe.build_count_query.return_value = ("SELECT COUNT(*) FROM assets", {})
        self.mock_db.execute_query.return_value = []

        target_filters = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"}]
        count, time_ms = self.service.count_matching_assets(target_filters)

        self.assertEqual(count, 0)

    def test_count_matching_assets_multiple_filters(self):
        """Test counting with multiple filter conditions"""
        self.mock_qe.build_count_query.return_value = (
            "SELECT COUNT(*) FROM assets WHERE LOWER(os_normalized) = LOWER(%(p0)s) AND LOWER(category) = LOWER(%(p1)s)",
            {"p0": "Windows", "p1": "workstation"}
        )
        self.mock_db.execute_query.return_value = [{"count": 45}]

        target_filters = [
            {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"},
            {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"}
        ]
        count, time_ms = self.service.count_matching_assets(target_filters)

        self.assertEqual(count, 45)

    def test_count_matching_assets_error_handling(self):
        """Test error handling during count query"""
        self.mock_qe.build_count_query.side_effect = Exception("Database error")

        target_filters = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"}]

        with self.assertRaises(Exception):
            self.service.count_matching_assets(target_filters)


class TestPreviewServiceCompliance(unittest.TestCase):
    """Test preview_compliance functionality"""

    def setUp(self):
        """Set up test fixtures"""
        self.service, self.mock_db, self.mock_qe, self.mock_resolver = _make_service()

    def test_preview_compliance_basic(self):
        """Test basic compliance preview with pass/fail"""
        self.mock_qe.build_asset_query.return_value = (
            "SELECT id, extended_attributes FROM assets WHERE LOWER(os_normalized) = LOWER(%(p0)s)",
            {"p0": "Windows"}
        )
        self.mock_db.execute_query.return_value = [
            {"id": "asset1", "extended_attributes": {"software": "Defender"}},
            {"id": "asset2", "extended_attributes": {"software": "Norton"}},
            {"id": "asset3", "extended_attributes": {"software": "None"}}
        ]
        self.mock_qe.evaluate_compliance.return_value = {
            "asset1": "pass",
            "asset2": "pass",
            "asset3": "fail"
        }

        target_filters = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"}]
        compliance_criteria = {
            "logic": "any",
            "conditions": []
        }

        total, passed, failed, time_ms = self.service.preview_compliance(
            target_filters,
            compliance_criteria
        )

        self.assertEqual(total, 3)
        self.assertEqual(passed, 2)
        self.assertEqual(failed, 1)
        self.assertIsInstance(time_ms, int)
        self.assertGreaterEqual(time_ms, 0)

    def test_preview_compliance_all_pass(self):
        """Test compliance when all assets pass"""
        self.mock_qe.build_asset_query.return_value = ("SELECT id, extended_attributes FROM assets", {})
        self.mock_db.execute_query.return_value = [
            {"id": "asset1", "extended_attributes": {"software": "Defender"}},
            {"id": "asset2", "extended_attributes": {"software": "Defender"}}
        ]
        self.mock_qe.evaluate_compliance.return_value = {
            "asset1": "pass",
            "asset2": "pass"
        }

        target_filters = [{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"}]
        compliance_criteria = {"logic": "any", "conditions": []}

        total, passed, failed, time_ms = self.service.preview_compliance(
            target_filters,
            compliance_criteria
        )

        self.assertEqual(total, 2)
        self.assertEqual(passed, 2)
        self.assertEqual(failed, 0)

    def test_preview_compliance_all_fail(self):
        """Test compliance when all assets fail"""
        self.mock_qe.build_asset_query.return_value = ("SELECT id, extended_attributes FROM assets", {})
        self.mock_db.execute_query.return_value = [
            {"id": "asset1", "extended_attributes": {"software": "None"}},
            {"id": "asset2", "extended_attributes": {"software": "None"}}
        ]
        self.mock_qe.evaluate_compliance.return_value = {
            "asset1": "fail",
            "asset2": "fail"
        }

        target_filters = [{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"}]
        compliance_criteria = {"logic": "any", "conditions": []}

        total, passed, failed, time_ms = self.service.preview_compliance(
            target_filters,
            compliance_criteria
        )

        self.assertEqual(total, 2)
        self.assertEqual(passed, 0)
        self.assertEqual(failed, 2)

    def test_preview_compliance_no_matching_assets(self):
        """Test compliance when no assets match target filters"""
        self.mock_qe.build_asset_query.return_value = ("SELECT id, extended_attributes FROM assets WHERE 1=0", {})
        self.mock_db.execute_query.return_value = []

        target_filters = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "NonExistent"}]
        compliance_criteria = {"logic": "any", "conditions": []}

        total, passed, failed, time_ms = self.service.preview_compliance(
            target_filters,
            compliance_criteria
        )

        self.assertEqual(total, 0)
        self.assertEqual(passed, 0)
        self.assertEqual(failed, 0)

    def test_preview_compliance_error_handling(self):
        """Test error handling during compliance preview"""
        self.mock_qe.build_asset_query.side_effect = Exception("Query build error")

        target_filters = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"}]
        compliance_criteria = {"logic": "any", "conditions": []}

        with self.assertRaises(Exception):
            self.service.preview_compliance(target_filters, compliance_criteria)

    def test_preview_compliance_slow_query_warning(self):
        """Test that slow queries trigger warning log"""
        self.mock_qe.build_asset_query.return_value = ("SELECT id, extended_attributes FROM assets", {})

        def slow_query(*args, **kwargs):
            import time
            time.sleep(1.1)  # Simulate 1100ms query (>1000ms threshold)
            return [{"id": "asset1", "extended_attributes": {}}]

        self.mock_db.execute_query.side_effect = slow_query
        self.mock_qe.evaluate_compliance.return_value = {"asset1": "pass"}

        target_filters = [{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"}]
        compliance_criteria = {"logic": "any", "conditions": []}

        total, passed, failed, time_ms = self.service.preview_compliance(
            target_filters,
            compliance_criteria
        )

        self.assertEqual(total, 1)
        self.assertGreater(time_ms, 1000)


class TestPreviewServicePreviewRule(unittest.TestCase):
    """Test preview_rule convenience method"""

    def setUp(self):
        """Set up test fixtures"""
        self.service, self.mock_db, self.mock_qe, self.mock_resolver = _make_service()

    def test_preview_rule_classification(self):
        """Test preview for classification rule"""
        with patch.object(self.service, 'count_matching_assets') as mock_count:
            mock_count.return_value = (127, 45)

            rule_data = {
                "type": "classification",
                "target_filters": [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"}],
                "tags": {"label": "Windows-Server", "color": "#0078d4"}
            }

            result = self.service.preview_rule(rule_data)

            self.assertEqual(result["total_matching"], 127)
            self.assertIsNone(result["pass_count"])
            self.assertIsNone(result["fail_count"])
            self.assertEqual(result["execution_time_ms"], 45)
            mock_count.assert_called_once_with(rule_data["target_filters"])

    def test_preview_rule_compliance(self):
        """Test preview for compliance rule"""
        with patch.object(self.service, 'preview_compliance') as mock_compliance:
            mock_compliance.return_value = (127, 104, 23, 234)

            rule_data = {
                "type": "compliance",
                "target_filters": [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"}],
                "tags": {"pass": "AV-Protected", "fail": "AV-Missing"},
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": []
                }
            }

            result = self.service.preview_rule(rule_data)

            self.assertEqual(result["total_matching"], 127)
            self.assertEqual(result["pass_count"], 104)
            self.assertEqual(result["fail_count"], 23)
            self.assertEqual(result["execution_time_ms"], 234)
            mock_compliance.assert_called_once_with(
                rule_data["target_filters"],
                rule_data["compliance_criteria"]
            )

    def test_preview_rule_compliance_without_criteria(self):
        """Test preview for compliance rule without criteria (falls back to count)"""
        with patch.object(self.service, 'count_matching_assets') as mock_count:
            mock_count.return_value = (50, 30)

            rule_data = {
                "type": "compliance",
                "target_filters": [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"}],
                "tags": {"pass": "AV-Protected", "fail": "AV-Missing"}
                # No compliance_criteria
            }

            result = self.service.preview_rule(rule_data)

            self.assertEqual(result["total_matching"], 50)
            self.assertIsNone(result["pass_count"])
            self.assertIsNone(result["fail_count"])
            self.assertEqual(result["execution_time_ms"], 30)

    def test_preview_rule_empty_filters(self):
        """Test preview with empty target filters"""
        with patch.object(self.service, 'count_matching_assets') as mock_count:
            mock_count.return_value = (0, 5)

            rule_data = {
                "type": "classification",
                "target_filters": [],
                "tags": {"label": "Test", "color": "#000000"}
            }

            result = self.service.preview_rule(rule_data)

            self.assertEqual(result["total_matching"], 0)


class TestPreviewServicePerformance(unittest.TestCase):
    """Test validate_performance functionality"""

    def setUp(self):
        """Set up test fixtures"""
        self.service, self.mock_db, self.mock_qe, self.mock_resolver = _make_service()

    def test_validate_performance_meets_target(self):
        """Test performance validation when query is fast enough"""
        with patch.object(self.service, 'count_matching_assets') as mock_count:
            mock_count.return_value = (127, 450)  # 450ms < 1000ms

            target_filters = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"}]
            result = self.service.validate_performance(target_filters, expected_max_ms=1000)

            self.assertEqual(result["execution_time_ms"], 450)
            self.assertTrue(result["meets_performance_target"])
            self.assertEqual(result["asset_count"], 127)
            self.assertEqual(result["expected_max_ms"], 1000)

    def test_validate_performance_exceeds_target(self):
        """Test performance validation when query is too slow"""
        with patch.object(self.service, 'count_matching_assets') as mock_count:
            mock_count.return_value = (127, 1500)  # 1500ms > 1000ms

            target_filters = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"}]
            result = self.service.validate_performance(target_filters, expected_max_ms=1000)

            self.assertEqual(result["execution_time_ms"], 1500)
            self.assertFalse(result["meets_performance_target"])
            self.assertEqual(result["asset_count"], 127)

    def test_validate_performance_custom_threshold(self):
        """Test performance validation with custom threshold"""
        with patch.object(self.service, 'count_matching_assets') as mock_count:
            mock_count.return_value = (50, 250)

            target_filters = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Linux"}]
            result = self.service.validate_performance(target_filters, expected_max_ms=200)

            self.assertEqual(result["execution_time_ms"], 250)
            self.assertFalse(result["meets_performance_target"])
            self.assertEqual(result["expected_max_ms"], 200)

    def test_validate_performance_edge_case_exact_threshold(self):
        """Test performance when execution time exactly equals threshold"""
        with patch.object(self.service, 'count_matching_assets') as mock_count:
            mock_count.return_value = (100, 1000)

            target_filters = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"}]
            result = self.service.validate_performance(target_filters, expected_max_ms=1000)

            self.assertTrue(result["meets_performance_target"])  # <= threshold


if __name__ == "__main__":
    unittest.main()
