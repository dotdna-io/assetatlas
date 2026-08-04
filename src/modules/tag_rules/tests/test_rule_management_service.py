# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for Rule Management Service

Tests CRUD operations, validation, and rule ID generation.
All database operations are mocked.
"""

import unittest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime
import json

from src.modules.tag_rules.services.rule_management_service import (
    RuleManagementService,
    create_rule,
    get_rule,
    list_rules
)
from src.modules.tag_rules.models.rule import TagRuleCreate, TagRuleUpdate, TagRule


def _make_rule_row(**overrides):
    """Build a dict mimicking RealDictCursor output for a tag_rules row."""
    now = datetime.now()
    defaults = {
        "id": "rule_123",
        "name": "Test Rule",
        "description": None,
        "type": "classification",
        "enabled": True,
        "target_filters": [{"field_id": {"kind": "static", "name": "OS Type"}, "operator": "equals", "value": "Windows"}],
        "tags": {"label": "Test", "color": "#000000"},
        "compliance_criteria": None,
        "detection_config": None,
        "created_at": now,
        "last_modified": now,
        "last_executed_at": None,
        "execution_count": 0,
        "last_execution_duration_ms": None,
        "last_execution_assets_matched": 0,
        "last_execution_assets_passed": None,
        "last_execution_assets_failed": None,
    }
    defaults.update(overrides)
    return defaults


class TestRuleIdGeneration(unittest.TestCase):
    """Test rule ID generation"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_db = Mock()
        self.service = RuleManagementService(self.mock_db)

    def test_generate_rule_id_format(self):
        """Test that generated rule IDs follow the correct format"""
        rule_id = self.service.generate_rule_id()

        # Should start with "rule_"
        self.assertTrue(rule_id.startswith("rule_"))

        # Should have 3 parts separated by underscores
        parts = rule_id.split("_")
        self.assertEqual(len(parts), 3)

        # Second part should be numeric timestamp
        self.assertTrue(parts[1].isdigit())

        # Third part should be 8-character hex
        self.assertEqual(len(parts[2]), 8)
        self.assertTrue(all(c in "0123456789abcdef" for c in parts[2]))

    def test_generate_rule_id_uniqueness(self):
        """Test that generated IDs are unique"""
        ids = [self.service.generate_rule_id() for _ in range(100)]
        self.assertEqual(len(ids), len(set(ids)))


class TestSerializationDeserialization(unittest.TestCase):
    """Test JSONB serialization and deserialization"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_db = Mock()
        self.service = RuleManagementService(self.mock_db)

    def test_serialize_jsonb_fields(self):
        """Test JSONB field serialization"""
        rule_data = {
            "name": "Test Rule",
            "target_filters": [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"}],
            "tags": {"label": "Test", "color": "#000000"},
            "compliance_criteria": {"logic": "any", "conditions": []},
            "detection_config": None
        }

        serialized = self.service._serialize_jsonb_fields(rule_data)

        # JSONB fields should be JSON strings
        self.assertIsInstance(serialized["target_filters"], str)
        self.assertIsInstance(serialized["tags"], str)
        self.assertIsInstance(serialized["compliance_criteria"], str)
        self.assertIsNone(serialized["detection_config"])

        # Non-JSONB fields should be unchanged
        self.assertEqual(serialized["name"], "Test Rule")

    def test_serialize_jsonb_fields_already_strings(self):
        """Test serialization when fields are already strings"""
        rule_data = {
            "target_filters": '[]',
            "tags": '{}',
        }

        serialized = self.service._serialize_jsonb_fields(rule_data)

        # Should remain as strings
        self.assertEqual(serialized["target_filters"], '[]')
        self.assertEqual(serialized["tags"], '{}')

    def test_deserialize_jsonb_fields(self):
        """Test JSONB field deserialization"""
        columns = [
            "id", "name", "target_filters", "tags",
            "compliance_criteria", "detection_config"
        ]

        row = {
            "id": "rule_123",
            "name": "Test Rule",
            "target_filters": [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"}],
            "tags": {"label": "Test", "color": "#000000"},
            "compliance_criteria": {"logic": "any", "conditions": []},
            "detection_config": None
        }

        deserialized = self.service._deserialize_jsonb_fields(row, columns)

        # Should be deserialized to Python objects
        self.assertIsInstance(deserialized["target_filters"], list)
        self.assertIsInstance(deserialized["tags"], dict)
        self.assertIsInstance(deserialized["compliance_criteria"], dict)
        self.assertIsNone(deserialized["detection_config"])

    def test_deserialize_with_datetime(self):
        """Test deserialization with datetime fields"""
        columns = ["id", "created_at", "last_modified"]
        now = datetime.now()
        row = {"id": "rule_123", "created_at": now, "last_modified": now}

        deserialized = self.service._deserialize_jsonb_fields(row, columns)

        self.assertIsInstance(deserialized["created_at"], datetime)
        self.assertEqual(deserialized["created_at"], now)


class TestCreateRule(unittest.TestCase):
    """Test rule creation"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_db = Mock()
        self.service = RuleManagementService(self.mock_db, auto_execute=False)

    def test_create_rule_classification(self):
        """Test creating a classification rule"""
        rule_create = TagRuleCreate(
            name="Windows Server Classification",
            description="Tags Windows Server assets",
            type="classification",
            enabled=True,
            target_filters=[{"field_id": {"kind": "static", "name": "OS Type"}, "operator": "contains", "value": "Windows Server"}],
            tags={"label": "Windows-Server", "color": "#0078d4"}
        )

        # Mock database response (dict from RealDictCursor)
        self.mock_db.execute_query.return_value = [_make_rule_row(
            id="rule_123456_abc",
            name="Windows Server Classification",
            description="Tags Windows Server assets",
            target_filters=[{"field_id": {"kind": "static", "name": "OS Type"}, "operator": "contains", "value": "Windows Server"}],
            tags={"label": "Windows-Server", "color": "#0078d4"},
        )]

        result = self.service.create_rule(rule_create)

        # Verify result
        self.assertIsInstance(result, TagRule)
        self.assertEqual(result.name, "Windows Server Classification")
        self.assertEqual(result.type, "classification")
        self.assertEqual(result.execution_count, 0)

        # Verify database was called
        self.mock_db.execute_query.assert_called_once()
        call_args = self.mock_db.execute_query.call_args
        self.assertIn("INSERT INTO tag_rules", call_args[0][0])

    def test_create_rule_compliance(self):
        """Test creating a compliance rule"""
        rule_create = TagRuleCreate(
            name="AV Protection Check",
            type="compliance",
            enabled=True,
            target_filters=[{"field_id": {"kind": "static", "name": "Asset Type"}, "operator": "equals", "value": "workstation"}],
            tags={"pass": "AV-Protected", "fail": "AV-Missing"},
            compliance_criteria={
                "logic": "any",
                "conditions": [{"field_id": {"kind": "static", "name": "Software Installed"}, "operator": "contains", "value": "Defender"}]
            },
            detection_config={
                "enabled": True,
                "condition": "Pass rate below threshold",
                "threshold": 90,
                "severity": "High"
            }
        )

        self.mock_db.execute_query.return_value = [_make_rule_row(
            id="rule_789_def",
            name="AV Protection Check",
            type="compliance",
            target_filters=[{"field_id": {"kind": "static", "name": "Asset Type"}, "operator": "equals", "value": "workstation"}],
            tags={"pass": "AV-Protected", "fail": "AV-Missing"},
            compliance_criteria={"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Software Installed"}, "operator": "contains", "value": "Defender"}]},
            detection_config={"enabled": True, "condition": "Pass rate below threshold", "threshold": 90, "severity": "High"},
        )]

        result = self.service.create_rule(rule_create)

        self.assertIsInstance(result, TagRule)
        self.assertEqual(result.type, "compliance")
        self.assertIsNotNone(result.compliance_criteria)
        self.assertIsNotNone(result.detection_config)

    def test_create_rule_database_error(self):
        """Test error handling during rule creation"""
        rule_create = TagRuleCreate(
            name="Test Rule",
            type="classification",
            target_filters=[{"field_id": {"kind": "static", "name": "OS Type"}, "operator": "equals", "value": "Windows"}],
            tags={"label": "Test", "color": "#000000"}
        )

        self.mock_db.execute_query.side_effect = Exception("Database error")

        with self.assertRaises(Exception):
            self.service.create_rule(rule_create)

    def test_create_rule_no_data_returned(self):
        """Test when database returns empty result"""
        rule_create = TagRuleCreate(
            name="Test Rule",
            type="classification",
            target_filters=[{"field_id": {"kind": "static", "name": "OS Type"}, "operator": "equals", "value": "Windows"}],
            tags={"label": "Test", "color": "#000000"}
        )

        self.mock_db.execute_query.return_value = []

        with self.assertRaises(Exception) as context:
            self.service.create_rule(rule_create)

        self.assertIn("Failed to create rule", str(context.exception))


class TestCreateRuleAutoExecute(unittest.TestCase):
    """Auto-execute on create — regression for #553.

    Pre-fix: RuleManagementService lazy-constructed a RuleExecutionService
    with a stale 1-arg signature, raising TypeError that was swallowed by the
    broad except. Counters stayed at 0 even though the rule was enabled.
    Post-fix: the execution service is injected by the factory; create_rule
    invokes it and returns the rule with refreshed counters.
    """

    def _build_rule_create(self):
        return TagRuleCreate(
            name="Auto-Exec Test",
            type="classification",
            enabled=True,
            target_filters=[{"field_id": {"kind": "static", "name": "OS Type"}, "operator": "equals", "value": "Windows"}],
            tags={"label": "Auto-Exec", "color": "#000000"},
        )

    def test_create_rule_invokes_injected_execution_service(self):
        """Auto-execute fires once on create when execution_service is injected."""
        mock_db = Mock()
        mock_exec = Mock()
        mock_exec.execute_rule.return_value = {"assets_matched": 42}

        # Insert returns the freshly created row (counters at 0).
        # get_rule (called after execute_rule) returns the row with refreshed counters.
        mock_db.execute_query.side_effect = [
            [_make_rule_row(name="Auto-Exec Test", last_execution_assets_matched=0)],
            [_make_rule_row(name="Auto-Exec Test", last_execution_assets_matched=42)],
        ]

        service = RuleManagementService(mock_db, auto_execute=True, execution_service=mock_exec)
        result = service.create_rule(self._build_rule_create())

        mock_exec.execute_rule.assert_called_once()
        call_kwargs = mock_exec.execute_rule.call_args.kwargs
        # rule_id is the service-generated id passed to INSERT — not asserted here,
        # just that execute_rule was called with the api trigger and produced refreshed counters.
        self.assertTrue(call_kwargs["rule_id"].startswith("rule_"))
        self.assertEqual(call_kwargs["triggered_by"], "api")
        self.assertEqual(result.last_execution_assets_matched, 42)

    def test_create_rule_skips_auto_execute_when_disabled(self):
        """auto_execute=False (TestConfig path) must not call execute_rule."""
        mock_db = Mock()
        mock_exec = Mock()
        mock_db.execute_query.return_value = [_make_rule_row(id="rule_abc", name="Auto-Exec Test")]

        service = RuleManagementService(mock_db, auto_execute=False, execution_service=mock_exec)
        service.create_rule(self._build_rule_create())

        mock_exec.execute_rule.assert_not_called()

    def test_create_rule_skips_auto_execute_when_service_missing(self):
        """No injected execution service → no-op (don't crash, don't construct stale service)."""
        mock_db = Mock()
        mock_db.execute_query.return_value = [_make_rule_row(id="rule_abc", name="Auto-Exec Test")]

        service = RuleManagementService(mock_db, auto_execute=True, execution_service=None)
        # Must not raise — pre-fix this path lazy-constructed RuleExecutionService(db)
        # with a stale signature and silently swallowed the TypeError.
        result = service.create_rule(self._build_rule_create())
        self.assertEqual(result.id, "rule_abc")

    def test_create_rule_swallows_execution_failure(self):
        """If execute_rule raises, the create call still succeeds — write is primary."""
        mock_db = Mock()
        mock_exec = Mock()
        mock_exec.execute_rule.side_effect = RuntimeError("kaboom")
        mock_db.execute_query.return_value = [_make_rule_row(id="rule_abc", name="Auto-Exec Test")]

        service = RuleManagementService(mock_db, auto_execute=True, execution_service=mock_exec)
        result = service.create_rule(self._build_rule_create())

        mock_exec.execute_rule.assert_called_once()
        self.assertEqual(result.id, "rule_abc")

    def test_create_rule_skips_auto_execute_when_rule_disabled(self):
        """Rule with enabled=False on creation should not auto-execute."""
        mock_db = Mock()
        mock_exec = Mock()
        rule_create = TagRuleCreate(
            name="Disabled Rule",
            type="classification",
            enabled=False,
            target_filters=[{"field_id": {"kind": "static", "name": "OS Type"}, "operator": "equals", "value": "Windows"}],
            tags={"label": "Disabled", "color": "#000000"},
        )
        mock_db.execute_query.return_value = [_make_rule_row(id="rule_abc", name="Disabled Rule", enabled=False)]

        service = RuleManagementService(mock_db, auto_execute=True, execution_service=mock_exec)
        service.create_rule(rule_create)

        mock_exec.execute_rule.assert_not_called()

    def test_update_rule_auto_execute_uses_allowed_triggered_by(self):
        """Update auto-execute must pass `triggered_by="api"` to satisfy the
        `rule_execution_history_triggered_by_check` constraint
        ('scheduled' | 'manual' | 'api' | 'asset_generation_pipeline').

        Pre-fix: passed `"rule_update"` which violated the check constraint;
        the rule executed but the history row insert failed silently.
        """
        mock_db = Mock()
        mock_exec = Mock()
        mock_exec.execute_rule.return_value = {"assets_matched": 7}

        # update_rule does: UPDATE ... RETURNING * → get_rule() SELECT (refresh after execute)
        mock_db.execute_query.side_effect = [
            [_make_rule_row(id="rule_abc", name="Renamed", enabled=True)],
            [_make_rule_row(id="rule_abc", name="Renamed", enabled=True, last_execution_assets_matched=7)],
        ]

        service = RuleManagementService(mock_db, auto_execute=True, execution_service=mock_exec)
        service.update_rule("rule_abc", TagRuleUpdate(name="Renamed"))

        mock_exec.execute_rule.assert_called_once()
        call_kwargs = mock_exec.execute_rule.call_args.kwargs
        self.assertEqual(call_kwargs["triggered_by"], "api")


class TestGetRule(unittest.TestCase):
    """Test retrieving rules"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_db = Mock()
        self.service = RuleManagementService(self.mock_db)

    def test_get_rule_found(self):
        """Test getting an existing rule"""
        self.mock_db.execute_query.return_value = [_make_rule_row(
            id="rule_123",
            name="Test Rule",
            description="Test Description",
        )]

        result = self.service.get_rule("rule_123")

        self.assertIsNotNone(result)
        self.assertIsInstance(result, TagRule)
        self.assertEqual(result.id, "rule_123")
        self.assertEqual(result.name, "Test Rule")

    def test_get_rule_not_found(self):
        """Test getting a non-existent rule"""
        self.mock_db.execute_query.return_value = []

        result = self.service.get_rule("nonexistent")

        self.assertIsNone(result)

    def test_get_rule_database_error(self):
        """Test error handling when fetching rule"""
        self.mock_db.execute_query.side_effect = Exception("Database error")

        with self.assertRaises(Exception):
            self.service.get_rule("rule_123")


class TestUpdateRule(unittest.TestCase):
    """Test rule updates"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_db = Mock()
        self.service = RuleManagementService(self.mock_db)

    def test_update_rule_single_field(self):
        """Test updating a single field"""
        rule_update = TagRuleUpdate(enabled=False)

        self.mock_db.execute_query.return_value = [_make_rule_row(
            id="rule_123",
            name="Test Rule",
            description="Description",
            enabled=False,
        )]

        result = self.service.update_rule("rule_123", rule_update)

        self.assertIsNotNone(result)
        self.assertEqual(result.enabled, False)

        # Verify UPDATE query was called
        call_args = self.mock_db.execute_query.call_args[0][0]
        self.assertIn("UPDATE tag_rules", call_args)
        self.assertIn("enabled", call_args)

    def test_update_rule_multiple_fields(self):
        """Test updating multiple fields"""
        rule_update = TagRuleUpdate(
            name="Updated Name",
            description="Updated Description",
            enabled=False
        )

        self.mock_db.execute_query.return_value = [_make_rule_row(
            id="rule_123",
            name="Updated Name",
            description="Updated Description",
            enabled=False,
        )]

        result = self.service.update_rule("rule_123", rule_update)

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Updated Name")
        self.assertEqual(result.description, "Updated Description")

    def test_update_rule_not_found(self):
        """Test updating a non-existent rule"""
        rule_update = TagRuleUpdate(enabled=False)
        self.mock_db.execute_query.return_value = []

        result = self.service.update_rule("nonexistent", rule_update)

        self.assertIsNone(result)

    def test_update_rule_no_fields(self):
        """Test update with no fields provided"""
        rule_update = TagRuleUpdate()

        # Should call get_rule instead
        with patch.object(self.service, 'get_rule') as mock_get:
            mock_get.return_value = Mock(spec=TagRule)
            result = self.service.update_rule("rule_123", rule_update)
            mock_get.assert_called_once_with("rule_123")

    def test_update_rule_database_error(self):
        """Test error handling during update"""
        rule_update = TagRuleUpdate(enabled=False)
        self.mock_db.execute_query.side_effect = Exception("Database error")

        with self.assertRaises(Exception):
            self.service.update_rule("rule_123", rule_update)


class TestDeleteRule(unittest.TestCase):
    """Test rule deletion"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_db = Mock()
        self.service = RuleManagementService(self.mock_db)

    def test_delete_rule_success(self):
        """Test successful rule deletion"""
        self.mock_db.execute_query.return_value = [("rule_123",)]

        result = self.service.delete_rule("rule_123")

        self.assertTrue(result)

        # Verify DELETE query was called
        call_args = self.mock_db.execute_query.call_args[0][0]
        self.assertIn("DELETE FROM tag_rules", call_args)

    def test_delete_rule_not_found(self):
        """Test deleting a non-existent rule"""
        self.mock_db.execute_query.return_value = []

        result = self.service.delete_rule("nonexistent")

        self.assertFalse(result)

    def test_delete_rule_database_error(self):
        """Test error handling during deletion"""
        self.mock_db.execute_query.side_effect = Exception("Database error")

        with self.assertRaises(Exception):
            self.service.delete_rule("rule_123")


class TestListRules(unittest.TestCase):
    """Test listing rules with pagination and filtering"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_db = Mock()
        self.service = RuleManagementService(self.mock_db)

    def test_list_rules_basic(self):
        """Test basic rule listing"""
        call_count = [0]

        def mock_execute(query, params):
            call_count[0] += 1
            if call_count[0] == 1:  # COUNT query
                return [{"count": 3}]
            else:  # SELECT query
                return [
                    _make_rule_row(id="rule_1", name="Rule 1"),
                    _make_rule_row(id="rule_2", name="Rule 2", type="compliance",
                                   tags={"pass": "Pass", "fail": "Fail"},
                                   compliance_criteria={"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "Software Installed"}, "operator": "contains", "value": "Defender"}]}),
                    _make_rule_row(id="rule_3", name="Rule 3", enabled=False),
                ]

        self.mock_db.execute_query.side_effect = mock_execute

        result = self.service.list_rules(page=1, limit=50)

        self.assertEqual(result.total, 3)
        self.assertEqual(len(result.rules), 3)
        self.assertEqual(result.page, 1)
        self.assertEqual(result.limit, 50)
        self.assertEqual(call_count[0], 2)

    def test_list_rules_with_pagination(self):
        """Test rule listing with pagination"""
        call_count = [0]

        def mock_execute(query, params):
            call_count[0] += 1
            if call_count[0] == 1:  # COUNT query
                return [{"count": 10}]
            else:  # SELECT query
                return [_make_rule_row(id=f"rule_{i}", name=f"Rule {i}") for i in range(5)]

        self.mock_db.execute_query.side_effect = mock_execute

        result = self.service.list_rules(page=2, limit=5)

        self.assertEqual(result.total, 10)
        self.assertEqual(len(result.rules), 5)
        self.assertEqual(result.page, 2)
        self.assertEqual(result.limit, 5)
        self.assertEqual(call_count[0], 2)

    def test_list_rules_filter_by_type(self):
        """Test filtering rules by type"""
        call_count = [0]

        def mock_execute(query, params):
            call_count[0] += 1
            if call_count[0] == 1:  # First call is COUNT
                return [{"count": 2}]
            else:  # Second call is SELECT
                return [
                    _make_rule_row(id="rule_1", name="Rule 1", type="compliance",
                                   tags={"pass": "Pass", "fail": "Fail"},
                                   compliance_criteria={"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "OS Type"}, "operator": "equals", "value": "Windows"}]}),
                    _make_rule_row(id="rule_2", name="Rule 2", type="compliance",
                                   tags={"pass": "Pass", "fail": "Fail"},
                                   compliance_criteria={"logic": "any", "conditions": [{"field_id": {"kind": "static", "name": "OS Type"}, "operator": "equals", "value": "Windows"}]}),
                ]

        self.mock_db.execute_query.side_effect = mock_execute

        result = self.service.list_rules(rule_type="compliance")

        self.assertEqual(result.total, 2)
        self.assertTrue(all(r.type == "compliance" for r in result.rules))

        # Verify both queries were called
        self.assertEqual(call_count[0], 2)

    def test_list_rules_enabled_only(self):
        """Test filtering for enabled rules only"""
        call_count = [0]

        def mock_execute(query, params):
            call_count[0] += 1
            if call_count[0] == 1:  # COUNT query
                return [{"count": 2}]
            else:  # SELECT query
                return [
                    _make_rule_row(id="rule_1", name="Rule 1"),
                    _make_rule_row(id="rule_2", name="Rule 2"),
                ]

        self.mock_db.execute_query.side_effect = mock_execute

        result = self.service.list_rules(enabled_only=True)

        self.assertEqual(result.total, 2)
        self.assertTrue(all(r.enabled for r in result.rules))
        self.assertEqual(call_count[0], 2)

    def test_list_rules_empty(self):
        """Test listing when no rules exist"""
        def mock_execute(query, params):
            if "COUNT(*)" in query:
                return [{"count": 0}]
            else:
                return []

        self.mock_db.execute_query.side_effect = mock_execute

        result = self.service.list_rules()

        self.assertEqual(result.total, 0)
        self.assertEqual(len(result.rules), 0)

    def test_list_rules_database_error(self):
        """Test error handling during list"""
        self.mock_db.execute_query.side_effect = Exception("Database error")

        with self.assertRaises(Exception):
            self.service.list_rules()


class TestEnableDisableRule(unittest.TestCase):
    """Test enable/disable convenience methods"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_db = Mock()
        self.service = RuleManagementService(self.mock_db)

    def test_enable_rule(self):
        """Test enabling a rule"""
        with patch.object(self.service, 'update_rule') as mock_update:
            mock_rule = Mock(spec=TagRule)
            mock_rule.enabled = True
            mock_update.return_value = mock_rule

            result = self.service.enable_rule("rule_123")

            mock_update.assert_called_once()
            call_args = mock_update.call_args[0]
            self.assertEqual(call_args[0], "rule_123")
            self.assertTrue(call_args[1].enabled)

    def test_disable_rule(self):
        """Test disabling a rule"""
        with patch.object(self.service, 'update_rule') as mock_update:
            mock_rule = Mock(spec=TagRule)
            mock_rule.enabled = False
            mock_update.return_value = mock_rule

            result = self.service.disable_rule("rule_123")

            mock_update.assert_called_once()
            call_args = mock_update.call_args[0]
            self.assertEqual(call_args[0], "rule_123")
            self.assertFalse(call_args[1].enabled)


class TestGetRuleStatistics(unittest.TestCase):
    """Test rule statistics retrieval"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_db = Mock()
        self.service = RuleManagementService(self.mock_db)

    def test_get_rule_statistics_found(self):
        """Test getting statistics for existing rule"""
        mock_rule = Mock(spec=TagRule)
        mock_rule.execution_count = 42
        mock_rule.last_executed_at = datetime.now()
        mock_rule.last_execution_duration_ms = 2340
        mock_rule.last_execution_assets_matched = 127
        mock_rule.last_execution_assets_passed = 104
        mock_rule.last_execution_assets_failed = 23

        with patch.object(self.service, 'get_rule') as mock_get:
            mock_get.return_value = mock_rule

            stats = self.service.get_rule_statistics("rule_123")

            self.assertIsNotNone(stats)
            self.assertEqual(stats["execution_count"], 42)
            self.assertEqual(stats["last_execution_duration_ms"], 2340)
            self.assertEqual(stats["last_assets_matched"], 127)

    def test_get_rule_statistics_not_found(self):
        """Test getting statistics for non-existent rule"""
        with patch.object(self.service, 'get_rule') as mock_get:
            mock_get.return_value = None

            stats = self.service.get_rule_statistics("nonexistent")

            self.assertIsNone(stats)


class TestHelperFunctions(unittest.TestCase):
    """Test module-level helper functions"""

    def test_create_rule_helper(self):
        """Test create_rule helper function"""
        mock_db = Mock()

        with patch('src.modules.tag_rules.services.rule_management_service.RuleManagementService') as MockService:
            mock_instance = MockService.return_value
            mock_instance.create_rule.return_value = Mock(spec=TagRule)

            rule_create = TagRuleCreate(
                name="Test",
                type="classification",
                target_filters=[{"field_id": {"kind": "static", "name": "OS Type"}, "operator": "equals", "value": "Windows"}],
                tags={"label": "Test", "color": "#000000"}
            )

            result = create_rule(mock_db, rule_create)

            MockService.assert_called_once_with(mock_db)
            mock_instance.create_rule.assert_called_once_with(rule_create)

    def test_get_rule_helper(self):
        """Test get_rule helper function"""
        mock_db = Mock()

        with patch('src.modules.tag_rules.services.rule_management_service.RuleManagementService') as MockService:
            mock_instance = MockService.return_value
            mock_instance.get_rule.return_value = Mock(spec=TagRule)

            result = get_rule(mock_db, "rule_123")

            MockService.assert_called_once_with(mock_db)
            mock_instance.get_rule.assert_called_once_with("rule_123")

    def test_list_rules_helper(self):
        """Test list_rules helper function"""
        mock_db = Mock()

        with patch('src.modules.tag_rules.services.rule_management_service.RuleManagementService') as MockService:
            mock_instance = MockService.return_value
            mock_instance.list_rules.return_value = Mock()

            result = list_rules(mock_db, page=2, limit=20, rule_type="compliance", enabled_only=True)

            MockService.assert_called_once_with(mock_db)
            mock_instance.list_rules.assert_called_once_with(2, 20, "compliance", True)


if __name__ == "__main__":
    unittest.main()
