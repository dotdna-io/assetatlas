# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the tag rules blueprint (auth required, most writes admin-only).

Endpoints tested:
- GET    /api/tag-rules                   — list rules (any role)
- GET    /api/tag-rules/<id>              — get rule (any role)
- POST   /api/tag-rules                   — create rule (admin)
- PUT    /api/tag-rules/<id>              — update rule (admin)
- DELETE /api/tag-rules/<id>              — delete rule (admin)
- POST   /api/tag-rules/<id>/toggle       — toggle enabled (admin)
- POST   /api/tag-rules/preview           — preview matches (any role)
- POST   /api/tag-rules/<id>/execute      — execute rule (admin)
- POST   /api/tag-rules/execute-all       — execute all (admin)
- GET    /api/tag-rules/fields            — field metadata (any role)
- GET    /api/tag-rules/<id>/statistics   — rule stats (any role)
"""

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

class TestTagRulesAuthRequired:
    """All tag-rules endpoints require a valid JWT."""

    @pytest.mark.parametrize("method,path", [
        ("GET",    "/api/tag-rules"),
        ("POST",   "/api/tag-rules"),
        ("GET",    "/api/tag-rules/some-id"),
        ("PUT",    "/api/tag-rules/some-id"),
        ("DELETE", "/api/tag-rules/some-id"),
        ("POST",   "/api/tag-rules/some-id/toggle"),
        ("POST",   "/api/tag-rules/preview"),
        ("POST",   "/api/tag-rules/some-id/execute"),
        ("POST",   "/api/tag-rules/execute-all"),
        ("GET",    "/api/tag-rules/fields"),
        ("GET",    "/api/tag-rules/some-id/statistics"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# RBAC: admin-only endpoints return 403 for regular users
# ---------------------------------------------------------------------------

class TestTagRulesAdminOnly:
    """Write endpoints require admin role (403 for regular users)."""

    def test_create_rule_as_user_returns_403(self, client, authed_headers):
        resp = client.post(
            "/api/tag-rules",
            json={"name": "test", "type": "classification"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403

    def test_update_rule_as_user_returns_403(self, client, authed_headers):
        resp = client.put(
            "/api/tag-rules/some-id",
            json={"name": "updated"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403

    def test_delete_rule_as_user_returns_403(self, client, authed_headers):
        resp = client.delete(
            "/api/tag-rules/some-id",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403

    def test_toggle_rule_as_user_returns_403(self, client, authed_headers):
        resp = client.post(
            "/api/tag-rules/some-id/toggle",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403

    def test_execute_rule_as_user_returns_403(self, client, authed_headers):
        resp = client.post(
            "/api/tag-rules/some-id/execute",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403

    def test_execute_all_as_user_returns_403(self, client, authed_headers):
        resp = client.post(
            "/api/tag-rules/execute-all",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/tag-rules — list (any role)
# ---------------------------------------------------------------------------

class TestListRules:
    """GET /api/tag-rules — paginated rule list."""

    def test_list_rules_success(self, client, authed_headers, blueprint_app):
        mock_list = MagicMock()
        mock_list.rules = []
        mock_list.total = 0
        mock_list.page = 1
        mock_list.limit = 50
        blueprint_app.rule_mgmt_service.list_rules.return_value = mock_list

        resp = client.get("/api/tag-rules", headers=authed_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "rules" in body["data"]
        assert "pagination" in body["data"]

    def test_list_rules_with_type_filter(self, client, authed_headers, blueprint_app):
        mock_list = MagicMock()
        mock_list.rules = []
        mock_list.total = 0
        mock_list.page = 1
        mock_list.limit = 50
        blueprint_app.rule_mgmt_service.list_rules.return_value = mock_list

        resp = client.get(
            "/api/tag-rules?type=classification&enabled=true",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        blueprint_app.rule_mgmt_service.list_rules.assert_called_once_with(
            page=1, limit=50, rule_type="classification", enabled_only=True,
        )


# ---------------------------------------------------------------------------
# GET /api/tag-rules/<rule_id>
# ---------------------------------------------------------------------------

class TestGetRule:
    """GET /api/tag-rules/<rule_id>"""

    def test_get_rule_success(self, client, authed_headers, blueprint_app):
        mock_rule = MagicMock()
        mock_rule.model_dump.return_value = {
            "id": "r1", "name": "Test Rule", "enabled": True,
        }
        blueprint_app.rule_mgmt_service.get_rule.return_value = mock_rule

        resp = client.get("/api/tag-rules/r1", headers=authed_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["rule"]["name"] == "Test Rule"

    def test_get_rule_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.rule_mgmt_service.get_rule.return_value = None

        resp = client.get("/api/tag-rules/nonexistent", headers=authed_headers("user"))
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/tag-rules — create (admin)
# ---------------------------------------------------------------------------

class TestCreateRule:
    """POST /api/tag-rules — admin only."""

    @patch("src.api.blueprints.tag_rules.AuditLogger")
    def test_create_rule_success(self, mock_audit, client, authed_headers, blueprint_app):
        mock_rule = MagicMock()
        mock_rule.id = "new-rule-id"
        mock_rule.name = "New Rule"
        mock_rule.type = "classification"
        mock_rule.enabled = True
        mock_rule.tags = {"label": "test-tag"}
        mock_rule.model_dump.return_value = {
            "id": "new-rule-id", "name": "New Rule", "type": "classification",
        }
        blueprint_app.rule_mgmt_service.create_rule.return_value = mock_rule

        resp = client.post(
            "/api/tag-rules",
            json={
                "name": "New Rule",
                "type": "classification",
                "enabled": True,
                "target_filters": [
                    {
                        "field_id": {"kind": "static", "name": "os_type"},
                        "operator": "equals",
                        "value": "Windows",
                    },
                ],
                "tags": {"label": "test-tag", "color": "#0078d4"},
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["rule"]["id"] == "new-rule-id"

    def test_create_rule_missing_body_returns_400(self, client, authed_headers):
        resp = client.post(
            "/api/tag-rules",
            data="null",
            content_type="application/json",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# PUT /api/tag-rules/<rule_id> — update (admin)
# ---------------------------------------------------------------------------

class TestUpdateRule:
    """PUT /api/tag-rules/<rule_id> — admin only."""

    @patch("src.api.blueprints.tag_rules.AuditLogger")
    def test_update_rule_success(self, mock_audit, client, authed_headers, blueprint_app):
        # Old rule for audit trail
        old_rule = MagicMock()
        old_rule.name = "Old Name"
        old_rule.enabled = True

        updated_rule = MagicMock()
        updated_rule.name = "Updated"
        updated_rule.enabled = True
        updated_rule.model_dump.return_value = {"id": "r1", "name": "Updated"}

        blueprint_app.rule_mgmt_service.get_rule.return_value = old_rule
        blueprint_app.rule_mgmt_service.update_rule.return_value = updated_rule

        resp = client.put(
            "/api/tag-rules/r1",
            json={"name": "Updated"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["rule"]["name"] == "Updated"

    def test_update_rule_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.rule_mgmt_service.get_rule.return_value = None

        resp = client.put(
            "/api/tag-rules/nonexistent",
            json={"name": "x"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/tag-rules/<rule_id> — admin
# ---------------------------------------------------------------------------

class TestDeleteRule:
    """DELETE /api/tag-rules/<rule_id> — admin only."""

    @patch("src.api.blueprints.tag_rules.AuditLogger")
    def test_delete_rule_success(self, mock_audit, client, authed_headers, blueprint_app):
        mock_rule = MagicMock()
        mock_rule.name = "Doomed Rule"
        mock_rule.type = "classification"
        mock_rule.tags = {"label": "test"}
        blueprint_app.rule_mgmt_service.get_rule.return_value = mock_rule
        blueprint_app.rule_mgmt_service.delete_rule.return_value = True

        resp = client.delete(
            "/api/tag-rules/r1", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "deleted" in body["data"]["message"].lower()

    def test_delete_rule_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.rule_mgmt_service.get_rule.return_value = None

        resp = client.delete(
            "/api/tag-rules/nonexistent", headers=authed_headers("admin"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/tag-rules/<rule_id>/toggle — admin
# ---------------------------------------------------------------------------

class TestToggleRule:
    """POST /api/tag-rules/<rule_id>/toggle — admin only."""

    @patch("src.api.blueprints.tag_rules.AuditLogger")
    def test_toggle_rule_enables(self, mock_audit, client, authed_headers, blueprint_app):
        # Rule starts disabled
        mock_rule = MagicMock()
        mock_rule.enabled = False
        mock_rule.name = "Rule X"

        toggled = MagicMock()
        toggled.enabled = True
        toggled.name = "Rule X"
        toggled.model_dump.return_value = {"id": "r1", "name": "Rule X", "enabled": True}

        blueprint_app.rule_mgmt_service.get_rule.return_value = mock_rule
        blueprint_app.rule_mgmt_service.update_rule.return_value = toggled

        resp = client.post(
            "/api/tag-rules/r1/toggle", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["rule"]["enabled"] is True
        assert "enabled" in body["data"]["message"].lower()

        # Slice 2 of #1150: the audit action is normalized to the resulting
        # state — 'enable', never the old free-text 'toggle'.
        _, kwargs = mock_audit.log_config_change.call_args
        assert kwargs["action"] == "enable"

    @patch("src.api.blueprints.tag_rules.AuditLogger")
    def test_toggle_rule_disables(self, mock_audit, client, authed_headers, blueprint_app):
        # Rule starts enabled
        mock_rule = MagicMock()
        mock_rule.enabled = True
        mock_rule.name = "Rule X"

        toggled = MagicMock()
        toggled.enabled = False
        toggled.name = "Rule X"
        toggled.model_dump.return_value = {"id": "r1", "name": "Rule X", "enabled": False}

        blueprint_app.rule_mgmt_service.get_rule.return_value = mock_rule
        blueprint_app.rule_mgmt_service.update_rule.return_value = toggled

        resp = client.post(
            "/api/tag-rules/r1/toggle", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200

        _, kwargs = mock_audit.log_config_change.call_args
        assert kwargs["action"] == "disable"

    def test_toggle_rule_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.rule_mgmt_service.get_rule.return_value = None

        resp = client.post(
            "/api/tag-rules/nonexistent/toggle", headers=authed_headers("admin"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/tag-rules/preview — any role
# ---------------------------------------------------------------------------

class TestPreviewRule:
    """POST /api/tag-rules/preview — available to all authenticated users."""

    def test_preview_simple_count(self, client, authed_headers, blueprint_app):
        blueprint_app.preview_service.count_matching_assets.return_value = (42, 15.5)

        resp = client.post(
            "/api/tag-rules/preview",
            json={
                "target_filters": [
                    {
                        "field_id": {"kind": "static", "name": "os_type"},
                        "operator": "equals",
                        "value": "Windows",
                    },
                ],
            },
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["total_matching"] == 42

    def test_preview_compliance(self, client, authed_headers, blueprint_app):
        blueprint_app.preview_service.preview_compliance.return_value = (100, 80, 20, 25.0)

        resp = client.post(
            "/api/tag-rules/preview",
            json={
                "target_filters": [
                    {
                        "field_id": {"kind": "static", "name": "OS Type"},
                        "operator": "contains",
                        "value": "Server",
                    },
                ],
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [
                        {
                            "field_id": {"kind": "static", "name": "Software Installed"},
                            "operator": "contains",
                            "value": "Defender",
                        },
                    ],
                },
            },
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["pass_count"] == 80
        assert body["data"]["fail_count"] == 20

    def test_preview_missing_body_returns_400(self, client, authed_headers):
        resp = client.post(
            "/api/tag-rules/preview",
            data="null",
            content_type="application/json",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/tag-rules/<rule_id>/execute — admin
# ---------------------------------------------------------------------------

class TestExecuteRule:
    """POST /api/tag-rules/<rule_id>/execute — admin only."""

    def test_execute_rule_success(self, client, authed_headers, blueprint_app):
        blueprint_app.execution_service.execute_rule.return_value = {
            "status": "completed",
            "execution_id": "exec-1",
            "assets_affected": 10,
        }

        resp = client.post(
            "/api/tag-rules/r1/execute", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["result"]["status"] == "completed"

    def test_execute_rule_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.execution_service.execute_rule.side_effect = ValueError(
            "Rule not found"
        )

        resp = client.post(
            "/api/tag-rules/nonexistent/execute", headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_execute_rule_failure_returns_400(self, client, authed_headers, blueprint_app):
        # Clear any side_effect from previous tests (reset_mock does not clear it)
        blueprint_app.execution_service.execute_rule.side_effect = None
        blueprint_app.execution_service.execute_rule.return_value = {
            "status": "failed",
            "error_message": "SQL error",
            "execution_id": "exec-2",
        }

        resp = client.post(
            "/api/tag-rules/r1/execute", headers=authed_headers("admin"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/tag-rules/execute-all — admin
# ---------------------------------------------------------------------------

class TestExecuteAllRules:
    """POST /api/tag-rules/execute-all — admin only."""

    @patch("src.api.blueprints.tag_rules.AuditLogger")
    def test_execute_all_success(self, mock_audit, client, authed_headers, blueprint_app):
        blueprint_app.execution_service.execute_all_enabled_rules.return_value = {
            "total_executed": 5,
            "successful": 4,
            "failed": 1,
            "total_assets_affected": 200,
        }

        resp = client.post(
            "/api/tag-rules/execute-all", headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["result"]["total_executed"] == 5


# ---------------------------------------------------------------------------
# GET /api/tag-rules/fields — any role
# ---------------------------------------------------------------------------

class TestGetFields:
    """GET /api/tag-rules/fields — field catalog via FieldResolver."""

    def test_fields_returns_entries(self, client, authed_headers, blueprint_app):
        # Wire field_resolver.catalog() to return a catalog whose entries() gives one item
        mock_catalog = MagicMock()
        mock_catalog.entries.return_value = []
        blueprint_app.field_resolver.catalog.return_value = mock_catalog

        with patch(
            "src.api.blueprints.tag_rules.build_catalog_response",
            return_value={"entries": []},
        ) as mock_bcr:
            resp = client.get("/api/tag-rules/fields", headers=authed_headers("user"))

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "entries" in body["data"]
        # Exact-list pin of the route's include_buckets (src/api/blueprints/tag_rules.py) — a new bucket means updating both.
        mock_bcr.assert_called_once_with(
            mock_catalog,
            include_buckets=["target", "advanced", "extended_attributes", "tags", "enrichment", "virtual", "posture", "unified"],
        )

# ---------------------------------------------------------------------------
# GET /api/tag-rules/<rule_id>/statistics — any role
# ---------------------------------------------------------------------------

class TestRuleStatistics:
    """GET /api/tag-rules/<rule_id>/statistics"""

    def test_statistics_success(self, client, authed_headers, blueprint_app):
        blueprint_app.rule_mgmt_service.get_rule_statistics.return_value = {
            "total_executions": 10,
            "avg_duration_ms": 42.5,
            "last_executed": "2025-01-01T00:00:00",
        }

        resp = client.get(
            "/api/tag-rules/r1/statistics", headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "statistics" in body["data"]

    def test_statistics_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.rule_mgmt_service.get_rule_statistics.return_value = None

        resp = client.get(
            "/api/tag-rules/nonexistent/statistics", headers=authed_headers("user"),
        )
        assert resp.status_code == 404
