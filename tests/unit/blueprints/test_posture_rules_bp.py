# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for /api/posture-rules blueprint (mocked services)."""
from unittest.mock import MagicMock, patch
import pytest
from datetime import datetime, timezone

from src.modules.posture_rules.services.sub_rule_management_service import (
    CategoryNotFoundError,
    SubRuleNotFoundError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def posture_services(blueprint_app):
    read = MagicMock()
    mgmt = MagicMock()
    eval_ = MagicMock()
    preview = MagicMock()
    blueprint_app.posture_category_read_service = read
    blueprint_app.posture_sub_rule_mgmt_service = mgmt
    blueprint_app.posture_evaluation_service = eval_
    blueprint_app.posture_preview_service = preview
    return {"read": read, "mgmt": mgmt, "eval": eval_, "preview": preview}


def _sub_rule_row():
    now = datetime.now(timezone.utc)
    return {
        "id": "sr-1",
        "category_id": "secured",
        "name": "x",
        "description": None,
        "enabled": True,
        "target_filters": [{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}],
        "compliance_criteria": {
            "logic": "any",
            "conditions": [{"field_id": {"kind": "static", "name": "a"}, "operator": "equals", "value": "b"}],
        },
        "display_order": 0,
        "created_at": now,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

class TestPostureRulesAuthRequired:
    """All posture-rules endpoints require a valid JWT."""

    @pytest.mark.parametrize("method,path", [
        ("GET",    "/api/posture-rules"),
        ("GET",    "/api/posture-rules/secured"),
        ("PATCH",  "/api/posture-rules/categories/secured"),
        ("POST",   "/api/posture-rules/secured/sub-rules"),
        ("PUT",    "/api/posture-rules/sub-rules/sr-1"),
        ("DELETE", "/api/posture-rules/sub-rules/sr-1"),
        ("POST",   "/api/posture-rules/sub-rules/sr-1/toggle"),
        ("GET",    "/api/posture-rules/sub-rules/sr-1/history"),
        ("PUT",    "/api/posture-rules/secured/sub-rules/order"),
        ("POST",   "/api/posture-rules/sub-rules/preview"),
        ("POST",   "/api/posture-rules/sub-rules/preview-output"),
        ("POST",   "/api/posture-rules/execute"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/posture-rules — list categories
# ---------------------------------------------------------------------------

class TestListCategories:
    def test_200(self, client, posture_services, authed_headers):
        posture_services["read"].list_categories.return_value = {"categories": []}
        resp = client.get("/api/posture-rules", headers=authed_headers("user"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "categories" in body["data"]


# ---------------------------------------------------------------------------
# GET /api/posture-rules/<category_id>
# ---------------------------------------------------------------------------

class TestGetCategory:
    def test_200(self, client, posture_services, authed_headers):
        posture_services["read"].get_category.return_value = {
            "id": "secured",
            "name": "Secured",
            "description": "d",
            "icon": "i",
            "applicable_categories": [],
            "display_order": 1,
            "sub_rules": [],
            "stats": {"pass": 0, "fail": 0, "n_a": 0},
            "created_at": datetime.now(timezone.utc),
        }
        resp = client.get(
            "/api/posture-rules/secured",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True

    def test_404_when_missing(self, client, posture_services, authed_headers):
        posture_services["read"].get_category.return_value = None
        resp = client.get(
            "/api/posture-rules/unknown",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/posture-rules/<category_id>/sub-rules — admin
# ---------------------------------------------------------------------------

class TestCreateSubRule:
    def test_201(self, client, posture_services, authed_headers):
        posture_services["mgmt"].create.return_value = _sub_rule_row()
        resp = client.post(
            "/api/posture-rules/secured/sub-rules",
            json={
                "name": "x",
                "target_filters": [
                    {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"},
                ],
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [{"field_id": {"kind": "static", "name": "a"}, "operator": "equals", "value": "b"}],
                },
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 201

    def test_404_when_category_missing(self, client, posture_services, authed_headers):
        posture_services["mgmt"].create.side_effect = CategoryNotFoundError("nope")
        resp = client.post(
            "/api/posture-rules/unknown/sub-rules",
            json={
                "name": "x",
                "target_filters": [
                    {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"},
                ],
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [{"field_id": {"kind": "static", "name": "a"}, "operator": "equals", "value": "b"}],
                },
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_400_on_invalid_body(self, client, posture_services, authed_headers):
        # Missing required target_filters and compliance_criteria
        resp = client.post(
            "/api/posture-rules/secured/sub-rules",
            json={"name": "x"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_403_as_regular_user(self, client, posture_services, authed_headers):
        resp = client.post(
            "/api/posture-rules/secured/sub-rules",
            json={
                "name": "x",
                "target_filters": [
                    {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"},
                ],
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [{"field_id": {"kind": "static", "name": "a"}, "operator": "equals", "value": "b"}],
                },
            },
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/posture-rules/sub-rules/<sub_rule_id> — admin
# ---------------------------------------------------------------------------

class TestUpdateSubRule:
    def test_200(self, client, posture_services, authed_headers):
        posture_services["mgmt"].update.return_value = _sub_rule_row()
        resp = client.put(
            "/api/posture-rules/sub-rules/sr-1",
            json={
                "name": "x",
                "target_filters": [
                    {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"},
                ],
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [{"field_id": {"kind": "static", "name": "a"}, "operator": "equals", "value": "b"}],
                },
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200

    def test_404(self, client, posture_services, authed_headers):
        posture_services["mgmt"].update.side_effect = SubRuleNotFoundError("nope")
        resp = client.put(
            "/api/posture-rules/sub-rules/sr-1",
            json={
                "name": "x",
                "target_filters": [
                    {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"},
                ],
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [{"field_id": {"kind": "static", "name": "a"}, "operator": "equals", "value": "b"}],
                },
            },
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/posture-rules/sub-rules/<sub_rule_id> — admin
# ---------------------------------------------------------------------------

class TestDeleteSubRule:
    def test_204(self, client, posture_services, authed_headers):
        posture_services["mgmt"].delete.return_value = None
        resp = client.delete(
            "/api/posture-rules/sub-rules/sr-1",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 204

    def test_404(self, client, posture_services, authed_headers):
        posture_services["mgmt"].delete.side_effect = SubRuleNotFoundError("nope")
        resp = client.delete(
            "/api/posture-rules/sub-rules/sr-1",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/posture-rules/sub-rules/<sub_rule_id>/toggle — admin
# ---------------------------------------------------------------------------

class TestToggleSubRule:
    def test_200(self, client, posture_services, authed_headers):
        posture_services["mgmt"].toggle.return_value = _sub_rule_row()
        resp = client.post(
            "/api/posture-rules/sub-rules/sr-1/toggle",
            json={"enabled": False},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/posture-rules/sub-rules/<sub_rule_id>/history — admin
# ---------------------------------------------------------------------------

class TestGetSubRuleHistory:
    def test_200(self, client, posture_services, authed_headers):
        posture_services["mgmt"].get_history.return_value = []
        resp = client.get(
            "/api/posture-rules/sub-rules/sr-1/history",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["entries"] == []

    def test_403_regular_user(self, client, posture_services, authed_headers):
        resp = client.get(
            "/api/posture-rules/sub-rules/sr-1/history",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/posture-rules/sub-rules/preview — any authenticated user
# ---------------------------------------------------------------------------

class TestPreview:
    def test_200(self, client, posture_services, authed_headers):
        posture_services["preview"].preview.return_value = {"matched": 3, "pass": 2, "fail": 1}
        resp = client.post(
            "/api/posture-rules/sub-rules/preview",
            json={
                "target_filters": [
                    {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"},
                ],
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [{"field_id": {"kind": "static", "name": "a"}, "operator": "equals", "value": "b"}],
                },
            },
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True

    def test_400_on_missing_body(self, client, posture_services, authed_headers):
        resp = client.post(
            "/api/posture-rules/sub-rules/preview",
            data="null",
            content_type="application/json",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/posture-rules/execute — admin
# ---------------------------------------------------------------------------

class TestExecute:
    def test_200(self, client, posture_services, authed_headers):
        posture_services["eval"].evaluate_all.return_value = {
            "updated_count": 10,
            "duration_ms": 250,
        }
        resp = client.post(
            "/api/posture-rules/execute",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["updated_count"] == 10

    def test_403_as_regular_user(self, client, posture_services, authed_headers):
        resp = client.post(
            "/api/posture-rules/execute",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PATCH /api/posture-rules/categories/<category_id> — admin
# ---------------------------------------------------------------------------

class TestUpdateCategory:
    def test_200(self, client, posture_services, authed_headers):
        posture_services["mgmt"].update_applicable_categories.return_value = {
            "id": "secured",
            "name": "Secured",
            "description": "d",
            "icon": "i",
            "applicable_categories": ["server"],
            "display_order": 1,
        }
        resp = client.patch(
            "/api/posture-rules/categories/secured",
            json={"applicable_categories": ["server"]},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["applicable_categories"] == ["server"]

    def test_404_when_category_missing(self, client, posture_services, authed_headers):
        posture_services["mgmt"].update_applicable_categories.side_effect = (
            CategoryNotFoundError("Unknown posture category 'unknown'")
        )
        resp = client.patch(
            "/api/posture-rules/categories/unknown",
            json={"applicable_categories": ["server"]},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_400_on_invalid_token(self, client, posture_services, authed_headers):
        resp = client.patch(
            "/api/posture-rules/categories/secured",
            json={"applicable_categories": ["server", "toaster"]},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_403_as_regular_user(self, client, posture_services, authed_headers):
        resp = client.patch(
            "/api/posture-rules/categories/secured",
            json={"applicable_categories": ["server"]},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/posture-rules/<category_id>/sub-rules/order — admin
# ---------------------------------------------------------------------------

class TestReorderSubRules:
    def test_204(self, client, posture_services, authed_headers):
        posture_services["mgmt"].reorder_sub_rules.return_value = None
        resp = client.put(
            "/api/posture-rules/secured/sub-rules/order",
            json={"ordered_ids": ["sr-1", "sr-2"]},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 204

    def test_404_unknown_category(self, client, posture_services, authed_headers):
        posture_services["mgmt"].reorder_sub_rules.side_effect = (
            CategoryNotFoundError("nope")
        )
        resp = client.put(
            "/api/posture-rules/unknown/sub-rules/order",
            json={"ordered_ids": ["sr-1"]},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_404_unknown_subrule(self, client, posture_services, authed_headers):
        posture_services["mgmt"].reorder_sub_rules.side_effect = (
            SubRuleNotFoundError("nope")
        )
        resp = client.put(
            "/api/posture-rules/secured/sub-rules/order",
            json={"ordered_ids": ["bogus"]},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_400_on_empty_ids(self, client, posture_services, authed_headers):
        resp = client.put(
            "/api/posture-rules/secured/sub-rules/order",
            json={"ordered_ids": []},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_403_regular_user(self, client, posture_services, authed_headers):
        resp = client.put(
            "/api/posture-rules/secured/sub-rules/order",
            json={"ordered_ids": ["sr-1"]},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/posture-rules/sub-rules/preview-output — any authenticated user
# ---------------------------------------------------------------------------

class TestPreviewOutput:
    def test_200(self, client, posture_services, authed_headers):
        posture_services["preview"].preview_output.return_value = {
            "matched": 3,
            "passed": 2,
            "samples": [
                {"hostname": "host-a", "output": "corp.example.com"},
                {"hostname": "host-b", "output": "other.local"},
            ],
        }
        resp = client.post(
            "/api/posture-rules/sub-rules/preview-output",
            json={
                "target_filters": [
                    {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"},
                ],
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [
                        {"field_id": {"kind": "static", "name": "Domain"}, "operator": "is not empty", "value": ""},
                    ],
                },
                "output_mode": "field",
                "output_field_id": {"kind": "static", "name": "Domain"},
            },
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["passed"] == 2
        assert len(body["data"]["samples"]) == 2

    def test_400_on_missing_output_field_id(self, client, posture_services, authed_headers):
        """field mode with no output_field_id is rejected by the model_validator."""
        resp = client.post(
            "/api/posture-rules/sub-rules/preview-output",
            json={
                "target_filters": [
                    {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "workstation"},
                ],
                "compliance_criteria": {
                    "logic": "any",
                    "conditions": [
                        {"field_id": {"kind": "static", "name": "Domain"}, "operator": "is not empty", "value": ""},
                    ],
                },
                "output_mode": "field",
                # missing output_field_id
            },
            headers=authed_headers("user"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Audit emission (#1150, slice 2) — all seven mutations log with the vocab
# ---------------------------------------------------------------------------

class TestPostureAudit:
    """Each mutating endpoint emits exactly one AuditLogger event carrying
    the shared vocabulary and the real JWT user context. The two preview
    endpoints stay silent by design (compute-only, nothing is mutated)."""

    _RULE_BODY = {
        "name": "x",
        "target_filters": [
            {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"},
        ],
        "compliance_criteria": {
            "logic": "any",
            "conditions": [{"field_id": {"kind": "static", "name": "a"}, "operator": "equals", "value": "b"}],
        },
    }

    @patch("src.api.blueprints.posture_rules.AuditLogger")
    def test_create_sub_rule(self, mock_audit, client, posture_services, authed_headers):
        posture_services["mgmt"].create.return_value = _sub_rule_row()
        resp = client.post(
            "/api/posture-rules/secured/sub-rules",
            json=self._RULE_BODY,
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 201
        mock_audit.log_config_change.assert_called_once()
        _, kwargs = mock_audit.log_config_change.call_args
        assert kwargs["resource"] == "posture_rule"
        assert kwargs["resource_id"] == "sr-1"
        assert kwargs["action"] == "create"
        assert (kwargs["user_id"], kwargs["username"]) == ("test-user-id", "testuser")

    @patch("src.api.blueprints.posture_rules.AuditLogger")
    def test_update_sub_rule(self, mock_audit, client, posture_services, authed_headers):
        posture_services["mgmt"].update.return_value = _sub_rule_row()
        resp = client.put(
            "/api/posture-rules/sub-rules/sr-1",
            json=self._RULE_BODY,
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        mock_audit.log_config_change.assert_called_once()
        _, kwargs = mock_audit.log_config_change.call_args
        assert kwargs["resource"] == "posture_rule"
        assert kwargs["resource_id"] == "sr-1"
        assert kwargs["action"] == "update"
        assert (kwargs["user_id"], kwargs["username"]) == ("test-user-id", "testuser")

    @patch("src.api.blueprints.posture_rules.AuditLogger")
    def test_delete_sub_rule(self, mock_audit, client, posture_services, authed_headers):
        posture_services["mgmt"].delete.return_value = None
        resp = client.delete(
            "/api/posture-rules/sub-rules/sr-1",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 204
        mock_audit.log_data_operation.assert_called_once()
        _, kwargs = mock_audit.log_data_operation.call_args
        assert kwargs["resource"] == "posture_rule"
        assert kwargs["resource_id"] == "sr-1"
        assert kwargs["action"] == "delete"
        assert (kwargs["user_id"], kwargs["username"]) == ("test-user-id", "testuser")

    @patch("src.api.blueprints.posture_rules.AuditLogger")
    def test_toggle_normalizes_to_resulting_state(self, mock_audit, client, posture_services, authed_headers):
        # Resulting row says enabled=True → 'enable' regardless of request verb.
        posture_services["mgmt"].toggle.return_value = _sub_rule_row()
        resp = client.post(
            "/api/posture-rules/sub-rules/sr-1/toggle",
            json={"enabled": True},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        _, kwargs = mock_audit.log_config_change.call_args
        assert kwargs["action"] == "enable"
        assert kwargs["resource"] == "posture_rule"
        assert (kwargs["user_id"], kwargs["username"]) == ("test-user-id", "testuser")

        mock_audit.reset_mock()
        disabled_row = {**_sub_rule_row(), "enabled": False}
        posture_services["mgmt"].toggle.return_value = disabled_row
        resp = client.post(
            "/api/posture-rules/sub-rules/sr-1/toggle",
            json={"enabled": False},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        _, kwargs = mock_audit.log_config_change.call_args
        assert kwargs["action"] == "disable"

    @patch("src.api.blueprints.posture_rules.AuditLogger")
    def test_update_category(self, mock_audit, client, posture_services, authed_headers):
        posture_services["mgmt"].update_applicable_categories.return_value = {
            "id": "secured",
            "applicable_categories": ["server"],
        }
        resp = client.patch(
            "/api/posture-rules/categories/secured",
            json={"applicable_categories": ["server"]},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        mock_audit.log_config_change.assert_called_once()
        _, kwargs = mock_audit.log_config_change.call_args
        assert kwargs["resource"] == "posture_rule"
        assert kwargs["resource_id"] == "secured"
        assert kwargs["action"] == "update"
        assert kwargs["details"] == {"applicable_categories": ["server"]}
        assert (kwargs["user_id"], kwargs["username"]) == ("test-user-id", "testuser")

    @patch("src.api.blueprints.posture_rules.AuditLogger")
    def test_reorder_sub_rules(self, mock_audit, client, posture_services, authed_headers):
        posture_services["mgmt"].reorder_sub_rules.return_value = None
        resp = client.put(
            "/api/posture-rules/secured/sub-rules/order",
            json={"ordered_ids": ["sr-1", "sr-2"]},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 204
        mock_audit.log_config_change.assert_called_once()
        _, kwargs = mock_audit.log_config_change.call_args
        assert kwargs["resource"] == "posture_rule"
        assert kwargs["resource_id"] == "secured"
        assert kwargs["action"] == "reorder"
        assert kwargs["details"] == {"ordered_ids": ["sr-1", "sr-2"]}
        assert (kwargs["user_id"], kwargs["username"]) == ("test-user-id", "testuser")

    @patch("src.api.blueprints.posture_rules.AuditLogger")
    def test_execute_all(self, mock_audit, client, posture_services, authed_headers):
        posture_services["eval"].evaluate_all.return_value = {
            "updated_count": 10,
            "duration_ms": 250,
            "skipped": False,
        }
        resp = client.post(
            "/api/posture-rules/execute",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        mock_audit.log_bulk_operation.assert_called_once()
        _, kwargs = mock_audit.log_bulk_operation.call_args
        assert kwargs["resource"] == "posture_rule"
        assert kwargs["action"] == "execute"
        assert kwargs["count"] == 10
        assert (kwargs["user_id"], kwargs["username"]) == ("test-user-id", "testuser")
