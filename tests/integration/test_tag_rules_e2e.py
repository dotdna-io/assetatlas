# SPDX-License-Identifier: AGPL-3.0-or-later
"""
End-to-end integration test for the tag rules system.

Exercises: create rule -> execute rule -> verify tags applied -> SAVEPOINT rolls back.
Uses real database with SAVEPOINT/ROLLBACK so no data persists.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from psycopg2 import extras

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.tag_rules.services.rule_management_service import RuleManagementService
from src.modules.tag_rules.services.rule_execution_service import RuleExecutionService
from src.modules.tag_rules.models.rule import TagRuleCreate


# ---------------------------------------------------------------------------
# Helper: thin connection-manager wrapper around a raw psycopg2 connection
# ---------------------------------------------------------------------------

class _SavepointConnManager:
    """Mimics PostgreSQLConnectionManager for the adapter and services."""

    def __init__(self, conn):
        self._conn = conn

    def begin_scope(self):
        pass

    def end_scope(self, error=None):
        pass

    def is_scope_active(self):
        return True

    @contextmanager
    def get_connection(self, autocommit=False):
        yield self._conn

    @contextmanager
    def get_cursor(self, cursor_factory=None, autocommit=False):
        if cursor_factory is not None:
            cur = self._conn.cursor(cursor_factory=cursor_factory)
        else:
            cur = self._conn.cursor()
        try:
            yield cur, self._conn
        finally:
            cur.close()


def _make_adapter(conn):
    """Create a PostgreSQLAdapter using a savepointed connection."""
    cm = _SavepointConnManager(conn)
    adp = PostgreSQLAdapter.__new__(PostgreSQLAdapter)
    adp.conn_manager = cm
    return adp


def _insert_test_assets(adapter, count=3):
    """Insert test assets and return their IDs.

    Assets have os_normalized='Windows 11' so a classification rule
    filtering on 'OS Type contains Windows' will match them.
    """
    now = datetime.now(timezone.utc)
    ids = [uuid.uuid4() for _ in range(count)]
    assets = []
    for i, aid in enumerate(ids):
        assets.append({
            "id": str(aid),
            "asset": f"tagrule-host-{i}",
            "hostname": [f"tagrule-host-{i}"],
            "fqdn": [f"tagrule-host-{i}.example.com"],
            "ip": [f"10.99.0.{i + 1}"],
            "mac": [f"AA:BB:CC:DD:EE:{i:02d}"],
            "sn": [f"SN-TR-{i:03d}"],
            "category": "Desktop",
            "subcategory": None,
            "os_normalized": "Windows 11",
            "os": ["Windows 11 23H2"],
            "os_orig": ["Windows 11"],
            "mac_vendor_enriched": None,
            "manufacturer": "Dell",
            "model": "OptiPlex",
            "data_source": ["test"],
            "data_source_category": None,
            "vendor_category": None,
            "confidence": 0.85,
            "confidence_breakdown": {},
            "ipam_location": None,
            "owner": None,
            "location": None,
            "user_field": None,
            "extended_attributes": {},
            "os_enrichment": None,
            "os_eol_status": None,
            "os_eol_date": None,
            "os_support_end_date": None,
            "os_extended_support_date": None,
            "os_is_lts": None,
            "os_eol_checked_at": None,
            "first_time": now,
            "last_time": now,
            "first_seen": now,
            "last_seen": now,
            "last_generation_run_id": None,
        })
    adapter.bulk_upsert_assets(assets)
    return ids


# =====================================================================
# Tests
# =====================================================================

@pytest.mark.integration
class TestTagRulesEndToEnd:
    """End-to-end tag rule: create -> execute -> verify tags -> rollback."""

    def test_classification_rule_lifecycle(self, db_transaction):
        """Create a classification rule, execute it, verify tags are applied."""
        conn = db_transaction
        adapter = _make_adapter(conn)

        # Insert test assets
        asset_ids = _insert_test_assets(adapter, count=3)

        # Create rule management service with auto_execute=False
        # (we will execute manually to control the flow)
        rule_mgmt = RuleManagementService(adapter, auto_execute=False)

        # Create a classification rule matching os_normalized containing 'Windows'
        rule_create = TagRuleCreate(
            name="Integration Test Classification",
            description="Tags Windows desktops",
            type="classification",
            enabled=True,
            target_filters=[
                {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"}
            ],
            tags={"label": "Windows-Desktop", "color": "#0078d4"},
        )
        rule = rule_mgmt.create_rule(rule_create)
        assert rule is not None
        assert rule.id.startswith("rule_")

        # Execute the rule
        exec_service = RuleExecutionService(adapter)
        # Override the rule_mgmt to reuse ours (which has auto_execute=False)
        exec_service.rule_mgmt = rule_mgmt
        result = exec_service.execute_rule(rule.id, triggered_by="manual")

        assert result["status"] == "success"
        assert result["assets_matched"] == 3
        assert result["tags_applied"] == 3

        # Verify tags exist in asset_tags_v2
        cur = conn.cursor(cursor_factory=extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM asset_tags_v2 WHERE tag_rule_id = %s",
            (rule.id,),
        )
        tag_rows = cur.fetchall()
        cur.close()

        assert len(tag_rows) == 3
        tagged_asset_ids = {str(r["asset_id"]) for r in tag_rows}
        expected_ids = {str(aid) for aid in asset_ids}
        assert tagged_asset_ids == expected_ids

        for row in tag_rows:
            assert row["tag_name"] == "Windows-Desktop"
            assert row["tag_type"] == "classification"

    def test_rule_execution_records_history(self, db_transaction):
        """Rule execution should create a record in rule_execution_history."""
        conn = db_transaction
        adapter = _make_adapter(conn)

        # Insert one test asset
        _insert_test_assets(adapter, count=1)

        rule_mgmt = RuleManagementService(adapter, auto_execute=False)
        rule_create = TagRuleCreate(
            name="History Test Rule",
            description="Tests execution history recording",
            type="classification",
            enabled=True,
            target_filters=[
                {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"}
            ],
            tags={"label": "HistoryTest", "color": "#ff0000"},
        )
        rule = rule_mgmt.create_rule(rule_create)

        exec_service = RuleExecutionService(adapter)
        exec_service.rule_mgmt = rule_mgmt
        result = exec_service.execute_rule(rule.id, triggered_by="manual")

        assert result["status"] == "success"
        assert result["execution_id"] is not None

        # Check execution history
        cur = conn.cursor(cursor_factory=extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM rule_execution_history WHERE rule_id = %s",
            (rule.id,),
        )
        history_rows = cur.fetchall()
        cur.close()

        assert len(history_rows) >= 1
        latest = history_rows[0]
        assert latest["status"] == "success"
        assert latest["triggered_by"] == "manual"

    def test_disabled_rule_removes_tags(self, db_transaction):
        """Executing a disabled rule should remove all its tags."""
        conn = db_transaction
        adapter = _make_adapter(conn)

        asset_ids = _insert_test_assets(adapter, count=2)
        rule_mgmt = RuleManagementService(adapter, auto_execute=False)

        rule_create = TagRuleCreate(
            name="Disable Test Rule",
            type="classification",
            enabled=True,
            target_filters=[
                {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"}
            ],
            tags={"label": "WillBeRemoved", "color": "#00ff00"},
        )
        rule = rule_mgmt.create_rule(rule_create)

        exec_service = RuleExecutionService(adapter)
        exec_service.rule_mgmt = rule_mgmt

        # Execute enabled
        exec_service.execute_rule(rule.id, triggered_by="manual")

        # Verify tags exist
        cur = conn.cursor(cursor_factory=extras.RealDictCursor)
        cur.execute(
            "SELECT COUNT(*) as cnt FROM asset_tags_v2 WHERE tag_rule_id = %s",
            (rule.id,),
        )
        assert cur.fetchone()["cnt"] == 2
        cur.close()

        # Disable the rule
        from src.modules.tag_rules.models.rule import TagRuleUpdate
        rule_mgmt.update_rule(rule.id, TagRuleUpdate(enabled=False))

        # Execute again (disabled)
        result = exec_service.execute_rule(rule.id, triggered_by="manual")
        assert result["status"] == "success"
        assert result.get("tags_removed", 0) == 2

        # Verify tags removed
        cur = conn.cursor(cursor_factory=extras.RealDictCursor)
        cur.execute(
            "SELECT COUNT(*) as cnt FROM asset_tags_v2 WHERE tag_rule_id = %s",
            (rule.id,),
        )
        assert cur.fetchone()["cnt"] == 0
        cur.close()

    def test_delete_rule(self, db_transaction):
        """Deleting a rule should remove it from tag_rules table."""
        conn = db_transaction
        adapter = _make_adapter(conn)

        rule_mgmt = RuleManagementService(adapter, auto_execute=False)

        rule_create = TagRuleCreate(
            name="Delete Me Rule",
            type="classification",
            enabled=False,
            target_filters=[
                {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Linux"}
            ],
            tags={"label": "Deletable", "color": "#999999"},
        )
        rule = rule_mgmt.create_rule(rule_create)

        # Verify it exists
        assert rule_mgmt.get_rule(rule.id) is not None

        # Delete
        deleted = rule_mgmt.delete_rule(rule.id)
        assert deleted is True

        # Verify it's gone
        assert rule_mgmt.get_rule(rule.id) is None
