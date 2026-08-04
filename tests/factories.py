# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Lightweight test data builders for AssetAtlas.

All functions return plain dicts with sensible defaults.  Any field can be
overridden via **kwargs::

    from tests.factories import make_asset
    asset = make_asset(hostname="web-01", category="Server")
"""

import uuid
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_asset(**overrides) -> dict:
    """Build an asset dict matching the DB schema (``assets`` table)."""
    now = _now_iso()
    defaults = {
        "id": str(uuid.uuid4()),
        "hostname": "test-host-001",
        "ip": "10.0.0.1",
        "mac": "AA:BB:CC:DD:EE:01",
        "sn": "SN-TEST-001",
        "category": "Desktop",
        "subcategory": "Standard",
        "os_normalized": "Windows 11",
        "manufacturer": "Dell",
        "model": "OptiPlex 7090",
        "confidence": 0.85,
        "data_source": ["jamf"],
        "first_time": now,
        "last_time": now,
        "extended_attributes": {},
    }
    defaults.update(overrides)
    return defaults


def make_filter(**overrides) -> dict:
    """Build a filter DSL dict."""
    defaults = {
        "field": "category",
        "operator": "eq",
        "value": "Server",
    }
    defaults.update(overrides)
    return defaults


def make_tag_rule(**overrides) -> dict:
    """Build a tag rule payload."""
    defaults = {
        "name": "Test Tag Rule",
        "description": "Rule created by test factory",
        "conditions": [
            {
                "field": "category",
                "operator": "eq",
                "value": "Server",
            }
        ],
        "actions": [
            {
                "type": "set_tag",
                "tag_key": "env",
                "tag_value": "test",
            }
        ],
        "enabled": True,
        "priority": 100,
    }
    defaults.update(overrides)
    return defaults


def make_integration_config(**overrides) -> dict:
    """Build an integration configuration dict."""
    defaults = {
        "id": str(uuid.uuid4()),
        "name": "Test Integration",
        "connector_type": "jamf",
        "config": {
            "base_url": "https://jamf.example.com",
            "username": "api_user",
            "password": "api_pass",
        },
        "schedule_enabled": False,
        "schedule_cron": None,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    defaults.update(overrides)
    return defaults


def make_user(**overrides) -> dict:
    """Build a user dict."""
    defaults = {
        "id": str(uuid.uuid4()),
        "username": "testuser",
        "email": "testuser@example.com",
        "role": "admin",
        "is_active": True,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    defaults.update(overrides)
    return defaults


def make_jwt_user(role="admin", user_id="test-user-id", username="testuser") -> dict:
    """Build a user dict for the JWT user cache.

    Shape matches what ``user_lookup_loader`` expects to find under
    ``cache.get(f"jwt_user:{user_id}")`` — distinct from ``make_user``
    (which mirrors the DB row) because the cache value omits timestamps
    and carries ``full_name`` instead.
    """
    return {
        "id": user_id,
        "username": username,
        "role": role,
        "is_active": True,
        "email": f"{username}@test.local",
        "full_name": "Test User",
    }


def make_report(**overrides) -> dict:
    """Build a report dict."""
    defaults = {
        "id": str(uuid.uuid4()),
        "name": "Test Report",
        "description": "Auto-generated test report",
        "type": "asset_summary",
        "filters": [],
        "columns": ["hostname", "ip_address", "category"],
        "created_by": "testuser",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    defaults.update(overrides)
    return defaults


def make_alert(**overrides) -> dict:
    """Build an alert dict."""
    defaults = {
        "id": str(uuid.uuid4()),
        "name": "Test Alert",
        "description": "Alert created by test factory",
        "severity": "medium",
        "type": "compliance",
        "status": "active",
        "rule_id": str(uuid.uuid4()),
        "matched_count": 5,
        "created_at": _now_iso(),
        "resolved_at": None,
    }
    defaults.update(overrides)
    return defaults


def make_chart_query(**overrides) -> dict:
    """Build a chart query request dict."""
    defaults = {
        "chart_type": "bar",
        "group_by": "category",
        "metric": "count",
        "filters": [],
        "limit": 10,
    }
    defaults.update(overrides)
    return defaults


def posture_category_factory(**overrides) -> dict:
    """Build a posture_categories row dict."""
    defaults = {
        "id": "secured",
        "name": "Secured",
        "description": "desc",
        "icon": "M1",
        "applicable_categories": ["server", "workstation"],
        "display_order": 1,
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return defaults


def posture_sub_rule_factory(**overrides) -> dict:
    """Build a posture_sub_rules row dict."""
    now = datetime.now(timezone.utc)
    defaults = {
        "id": str(uuid.uuid4()),
        "category_id": "secured",
        "name": "test sub-rule",
        "description": None,
        "enabled": True,
        "target_filters": [
            {"field": "Category", "operator": "equals", "value": "server"}
        ],
        "compliance_criteria": {
            "logic": "any",
            "conditions": [
                {"field": "Data Source Category", "operator": "contains", "value": "EDR"}
            ],
        },
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return defaults
