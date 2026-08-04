# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the integrations CRUD blueprint (admin-only).

All endpoints live under /api/integrations and require admin role.

Endpoints tested:
- GET    /api/integrations                      — list integrations
- POST   /api/integrations                      — create integration
- GET    /api/integrations/<id>                 — get integration details
- PUT    /api/integrations/<id>                 — update integration
- DELETE /api/integrations/<id>                 — delete integration
- GET    /api/integrations/<id>/test            — test connection
- POST   /api/integrations/<id>/sample          — get sample data
- GET    /api/integrations/<id>/schema          — get field schema
- POST   /api/integrations/<id>/run             — manual run
- PATCH  /api/integrations/<id>/enable          — enable integration
- PATCH  /api/integrations/<id>/disable         — disable integration
- GET    /api/integrations/stats                — get statistics
- GET    /api/integrations/logo-map             — get logo map (any auth)
"""

import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INTEGRATION_ID = "11111111-1111-1111-1111-111111111111"


def _make_integration(**overrides):
    """Create a MagicMock that behaves like an integration domain object."""
    integration = MagicMock()
    integration.id = overrides.get("id", INTEGRATION_ID)
    integration.name = overrides.get("name", "Test Jamf")
    integration.integration_type = overrides.get("integration_type", "jamf")
    integration.status = overrides.get("status", "enabled")
    integration.category = overrides.get("category", "mdm")
    integration.logo = overrides.get("logo", "jamf.png")
    integration.description = overrides.get("description", "Test integration")
    integration.config = overrides.get("config", {"url": "https://jamf.example.com"})
    integration.schedule_enabled = overrides.get("schedule_enabled", False)
    integration.schedule_cron = overrides.get("schedule_cron", None)
    integration.to_dict.return_value = {
        "id": str(integration.id),
        "name": integration.name,
        "integration_type": integration.integration_type,
        "status": integration.status,
        "config": integration.config,
    }
    return integration


# =============================================================================
# Auth / RBAC
# =============================================================================

class TestIntegrationsAuth:
    """Most integration endpoints require JWT + admin."""

    @pytest.mark.parametrize("method,path", [
        ("GET",    "/api/integrations"),
        ("POST",   "/api/integrations"),
        ("GET",    f"/api/integrations/{INTEGRATION_ID}"),
        ("PUT",    f"/api/integrations/{INTEGRATION_ID}"),
        ("DELETE", f"/api/integrations/{INTEGRATION_ID}"),
        ("GET",    f"/api/integrations/{INTEGRATION_ID}/test"),
        ("POST",   f"/api/integrations/{INTEGRATION_ID}/sample"),
        ("GET",    f"/api/integrations/{INTEGRATION_ID}/schema"),
        ("POST",   f"/api/integrations/{INTEGRATION_ID}/run"),
        ("PATCH",  f"/api/integrations/{INTEGRATION_ID}/enable"),
        ("PATCH",  f"/api/integrations/{INTEGRATION_ID}/disable"),
        ("GET",    "/api/integrations/stats"),
        ("GET",    "/api/integrations/logos"),
        ("POST",   "/api/integrations/logos/upload"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401

    def test_logo_file_endpoint_is_public(self, client, blueprint_app, tmp_path):
        """``GET /api/integrations/logos/file/<name>`` is intentionally unauthenticated.

        Image tags don't include the Authorization header, so the served
        bytes have to be reachable without a JWT — same trust profile as
        the bundled /logos/<name> assets served by the UI container.
        """
        # Configure the upload directory to a tempdir we control.
        blueprint_app.config['UPLOADED_LOGOS_DIR'] = str(tmp_path)
        sha = "a" * 64
        (tmp_path / f"{sha}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        # No headers — this must succeed.
        resp = client.get(f"/api/integrations/logos/file/{sha}.png")
        assert resp.status_code == 200
        assert resp.headers.get("Cache-Control") == "public, max-age=2592000, immutable"

    @pytest.mark.parametrize("method,path", [
        ("GET",    "/api/integrations"),
        ("POST",   "/api/integrations"),
        ("GET",    f"/api/integrations/{INTEGRATION_ID}"),
        ("PUT",    f"/api/integrations/{INTEGRATION_ID}"),
        ("DELETE", f"/api/integrations/{INTEGRATION_ID}"),
        ("GET",    "/api/integrations/stats"),
    ])
    def test_non_admin_returns_403(self, client, auth_headers, _prime_regular_user, method, path):
        resp = getattr(client, method.lower())(path, headers=auth_headers("user"))
        assert resp.status_code == 403

    def test_logo_map_accessible_to_any_role(self, client, auth_headers, blueprint_app):
        """GET /api/integrations/logo-map does NOT require admin."""
        blueprint_app.integration_service.list_integrations.return_value = []
        resp = client.get("/api/integrations/logo-map", headers=auth_headers("user"))
        assert resp.status_code == 200


# =============================================================================
# List integrations (GET /api/integrations)
# =============================================================================

class TestListIntegrations:
    """GET /api/integrations"""

    def test_list_returns_200(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.list_integrations.return_value = [integration]
        blueprint_app.integration_service.batch_repository.get_latest_batches_for_all_integrations.return_value = {}

        resp = client.get("/api/integrations", headers=auth_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["count"] == 1

    def test_list_filters_by_status(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.list_integrations.return_value = []
        blueprint_app.integration_service.batch_repository.get_latest_batches_for_all_integrations.return_value = {}

        client.get(
            "/api/integrations?status=enabled",
            headers=auth_headers("admin"),
        )

        call_kwargs = blueprint_app.integration_service.list_integrations.call_args
        # status should be an IntegrationStatus enum
        assert call_kwargs[1]["status"] is not None

    def test_list_invalid_status_returns_400(self, client, auth_headers, blueprint_app):
        resp = client.get(
            "/api/integrations?status=invalid_status",
            headers=auth_headers("admin"),
        )
        assert resp.status_code == 400

    def test_list_excludes_manual_by_default(self, client, auth_headers, blueprint_app):
        """Manual/file-based integrations are excluded by default."""
        manual_int = _make_integration(config={"file_source": "/tmp/data.csv"})
        blueprint_app.integration_service.list_integrations.return_value = [manual_int]
        blueprint_app.integration_service.batch_repository.get_latest_batches_for_all_integrations.return_value = {}

        resp = client.get("/api/integrations", headers=auth_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["count"] == 0  # excluded

    def test_list_enriches_with_batch_data(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.list_integrations.return_value = [integration]
        blueprint_app.integration_service.batch_repository.get_latest_batches_for_all_integrations.return_value = {
            str(integration.id): {
                "raw_records_count": 42,
                "started_at": "2025-01-01T00:00:00",
                "status": "completed",
                "assets_created_count": 30,
                "last_sync_at": "2025-01-01T00:05:00",
                "last_run_at": "2025-01-01T00:00:00",
            }
        }

        resp = client.get("/api/integrations", headers=auth_headers("admin"))
        body = resp.get_json()
        assert body["integrations"][0]["latest_batch_records"] == 42
        assert body["integrations"][0]["latest_batch_status"] == "completed"
        assert body["integrations"][0]["latest_batch_assets"] == 30
        assert body["integrations"][0]["last_sync_at"] == "2025-01-01T00:05:00"
        assert body["integrations"][0]["last_run_at"] == "2025-01-01T00:00:00"

    def test_list_last_run_at_null_when_no_completed_ingestion(
        self, client, auth_headers, blueprint_app,
    ):
        integration = _make_integration()
        blueprint_app.integration_service.list_integrations.return_value = [integration]
        blueprint_app.integration_service.batch_repository.get_latest_batches_for_all_integrations.return_value = {
            str(integration.id): {
                "raw_records_count": 0,
                "started_at": "2025-01-01T00:00:00",
                "status": "running",
                "assets_created_count": 0,
                "last_sync_at": None,
                "last_run_at": None,
            }
        }

        resp = client.get("/api/integrations", headers=auth_headers("admin"))
        body = resp.get_json()
        assert body["integrations"][0]["last_run_at"] is None

    def test_list_last_run_at_null_when_no_batches(
        self, client, auth_headers, blueprint_app,
    ):
        integration = _make_integration()
        blueprint_app.integration_service.list_integrations.return_value = [integration]
        blueprint_app.integration_service.batch_repository.get_latest_batches_for_all_integrations.return_value = {}

        resp = client.get("/api/integrations", headers=auth_headers("admin"))
        body = resp.get_json()
        assert body["integrations"][0]["last_run_at"] is None

    def test_list_last_sync_at_null_when_no_completed_pipeline(self, client, auth_headers, blueprint_app):
        """When the batch has not been processed by the pipeline yet, last_sync_at is null."""
        integration = _make_integration()
        blueprint_app.integration_service.list_integrations.return_value = [integration]
        blueprint_app.integration_service.batch_repository.get_latest_batches_for_all_integrations.return_value = {
            str(integration.id): {
                "raw_records_count": 42,
                "started_at": "2025-01-01T00:00:00",
                "status": "running",
                "assets_created_count": 0,
                "last_sync_at": None,
            }
        }

        resp = client.get("/api/integrations", headers=auth_headers("admin"))
        body = resp.get_json()
        assert body["integrations"][0]["last_sync_at"] is None

    def test_list_last_sync_at_null_when_no_batches(self, client, auth_headers, blueprint_app):
        """Integrations without any batches have null last_sync_at."""
        integration = _make_integration()
        blueprint_app.integration_service.list_integrations.return_value = [integration]
        blueprint_app.integration_service.batch_repository.get_latest_batches_for_all_integrations.return_value = {}

        resp = client.get("/api/integrations", headers=auth_headers("admin"))
        body = resp.get_json()
        assert body["integrations"][0]["last_sync_at"] is None


# =============================================================================
# Create integration (POST /api/integrations)
# =============================================================================

class TestCreateIntegration:
    """POST /api/integrations"""

    @patch("src.api.blueprints.integrations.integrations.validate_field_mapping")
    @patch("src.api.blueprints.integrations.integrations.AuditLogger")
    @patch("src.api.blueprints.integrations.integrations.get_current_user_context")
    def test_create_success(self, mock_ctx, mock_audit, mock_validate, client, auth_headers, blueprint_app):
        mock_ctx.return_value = ("test-user-id", "testuser")
        new_integration = _make_integration()
        blueprint_app.integration_service.create_integration.return_value = new_integration

        resp = client.post("/api/integrations", json={
            "name": "New Jamf",
            "integration_type": "jamf",
            "config": {"url": "https://jamf.example.com"},
        }, headers=auth_headers("admin"))

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["success"] is True
        assert "integration" in body

    @patch("src.api.blueprints.integrations.integrations.validate_field_mapping")
    @patch("src.api.blueprints.integrations.integrations.AuditLogger")
    @patch("src.api.blueprints.integrations.integrations.get_current_user_context")
    def test_create_forwards_preserve_fields(self, mock_ctx, mock_audit, mock_validate, client, auth_headers, blueprint_app):
        """#809: DSS classification is persisted via a distinct preserve_fields
        array. create() passes explicit kwargs (not **data), so it must forward
        preserve_fields or the value is silently dropped."""
        mock_ctx.return_value = ("test-user-id", "testuser")
        mock_validate.return_value = (True, [])
        blueprint_app.integration_service.create_integration.return_value = _make_integration()

        resp = client.post("/api/integrations", json={
            "name": "DSS Source",
            "integration_type": "custom_http",
            "config": {"url": "https://x.example.com"},
            "field_mapping": {"hostname": "device_name", "os": "os"},
            "preserve_fields": ["os"],
        }, headers=auth_headers("admin"))

        assert resp.status_code == 201
        _, kwargs = blueprint_app.integration_service.create_integration.call_args
        assert kwargs["preserve_fields"] == ["os"]

    def test_create_missing_required_fields(self, client, auth_headers):
        resp = client.post("/api/integrations", json={
            "name": "Only name",
        }, headers=auth_headers("admin"))

        assert resp.status_code == 400

    @patch("src.api.blueprints.integrations.integrations.validate_field_mapping")
    def test_create_invalid_field_mapping(self, mock_validate, client, auth_headers):
        mock_validate.return_value = (False, ["Invalid field: xyz"])

        resp = client.post("/api/integrations", json={
            "name": "Bad Mapping",
            "integration_type": "jamf",
            "config": {"url": "https://jamf.example.com"},
            "field_mapping": {"invalid": "mapping"},
        }, headers=auth_headers("admin"))

        assert resp.status_code == 400

    @patch("src.api.blueprints.integrations.integrations.validate_field_mapping")
    @patch("src.api.blueprints.integrations.integrations.AuditLogger")
    @patch("src.api.blueprints.integrations.integrations.get_current_user_context")
    def test_create_duplicate_name_returns_409(self, mock_ctx, mock_audit, mock_validate, client, auth_headers, blueprint_app):
        mock_ctx.return_value = ("test-user-id", "testuser")
        blueprint_app.integration_service.create_integration.side_effect = Exception(
            "unique constraint violation: already exists"
        )

        resp = client.post("/api/integrations", json={
            "name": "Duplicate",
            "integration_type": "jamf",
            "config": {"url": "https://jamf.example.com"},
        }, headers=auth_headers("admin"))

        assert resp.status_code == 409
        data = resp.get_json()
        assert "Duplicate" in data["message"]
        assert "already exists" in data["message"]


# =============================================================================
# Get integration (GET /api/integrations/<id>)
# =============================================================================

class TestGetIntegration:
    """GET /api/integrations/<id>"""

    def test_get_success(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_latest_sync_and_run_for_integration.return_value = {
            "last_sync_at": None, "last_run_at": None,
        }

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["integration"]["name"] == "Test Jamf"

    def test_get_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404

    def test_get_includes_canonical_last_sync_and_last_run(
        self, client, auth_headers, blueprint_app,
    ):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_latest_sync_and_run_for_integration.return_value = {
            "last_sync_at": "2026-04-28T04:30:54",
            "last_run_at": "2026-04-28T01:00:00",
        }

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["integration"]["last_sync_at"] == "2026-04-28T04:30:54"
        assert body["integration"]["last_run_at"] == "2026-04-28T01:00:00"

    def test_get_last_sync_and_last_run_null_when_no_batches(
        self, client, auth_headers, blueprint_app,
    ):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_latest_sync_and_run_for_integration.return_value = {
            "last_sync_at": None, "last_run_at": None,
        }

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["integration"]["last_sync_at"] is None
        assert body["integration"]["last_run_at"] is None

    def test_get_calls_targeted_helper_not_bulk(
        self, client, auth_headers, blueprint_app,
    ):
        # The detail endpoint must NOT call the bulk
        # get_latest_batches_for_all_integrations helper, which scans
        # every integration's batches just to read one row.
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_latest_sync_and_run_for_integration.return_value = {
            "last_sync_at": None, "last_run_at": None,
        }

        client.get(
            f"/api/integrations/{INTEGRATION_ID}",
            headers=auth_headers("admin"),
        )

        bulk = (
            blueprint_app.integration_service.batch_repository
            .get_latest_batches_for_all_integrations
        )
        bulk.assert_not_called()
        targeted = (
            blueprint_app.integration_service.batch_repository
            .get_latest_sync_and_run_for_integration
        )
        targeted.assert_called_once_with(integration.id)


# =============================================================================
# Update integration (PUT /api/integrations/<id>)
# =============================================================================

class TestUpdateIntegration:
    """PUT /api/integrations/<id>"""

    @patch("src.api.blueprints.integrations.integrations.AuditLogger")
    @patch("src.api.blueprints.integrations.integrations.get_current_user_context")
    def test_update_success(self, mock_ctx, mock_audit, client, auth_headers, blueprint_app):
        mock_ctx.return_value = ("test-user-id", "testuser")
        old_integration = _make_integration(name="Old Name")
        updated_integration = _make_integration(name="New Name")
        blueprint_app.integration_service.get_integration.return_value = old_integration
        blueprint_app.integration_service.update_integration.return_value = updated_integration

        resp = client.put(
            f"/api/integrations/{INTEGRATION_ID}",
            json={"name": "New Name"},
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True

    @patch("src.api.blueprints.integrations.integrations.AuditLogger")
    @patch("src.api.blueprints.integrations.integrations.get_current_user_context")
    def test_update_forwards_preserve_fields(self, mock_ctx, mock_audit, client, auth_headers, blueprint_app):
        """#809: editing must forward preserve_fields in the update payload so
        the DSS classification is re-persisted (the repository's JSONB whitelist
        then wraps it as Json)."""
        mock_ctx.return_value = ("test-user-id", "testuser")
        blueprint_app.integration_service.get_integration.return_value = _make_integration()
        blueprint_app.integration_service.update_integration.return_value = _make_integration()

        resp = client.put(
            f"/api/integrations/{INTEGRATION_ID}",
            json={"name": "Edited", "preserve_fields": ["os", "user"]},
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        # update_integration(integration_id, data) — data must carry preserve_fields
        args, _ = blueprint_app.integration_service.update_integration.call_args
        assert args[1]["preserve_fields"] == ["os", "user"]

    @patch("src.api.blueprints.integrations.integrations.AuditLogger")
    @patch("src.api.blueprints.integrations.integrations.get_current_user_context")
    def test_update_audits_preserve_fields_change(self, mock_ctx, mock_audit, client, auth_headers, blueprint_app):
        """#809: toggling DSS on a framework-named field leaves field_mapping
        byte-identical ({field:field} either way) and flips only preserve_fields,
        so the audit trail must record the preserve_fields change or the edit
        leaves no trace at all."""
        mock_ctx.return_value = ("test-user-id", "testuser")
        old = _make_integration()
        old.preserve_fields = []
        blueprint_app.integration_service.get_integration.return_value = old
        blueprint_app.integration_service.update_integration.return_value = _make_integration()

        resp = client.put(
            f"/api/integrations/{INTEGRATION_ID}",
            json={"preserve_fields": ["os"]},
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        mock_audit.log_config_change.assert_called_once()
        _, kwargs = mock_audit.log_config_change.call_args
        assert kwargs["changes"]["preserve_fields"] == {"old": [], "new": ["os"]}

    def test_update_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.put(
            f"/api/integrations/{INTEGRATION_ID}",
            json={"name": "X"},
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404


# =============================================================================
# Delete integration (DELETE /api/integrations/<id>)
# =============================================================================

class TestDeleteIntegration:
    """DELETE /api/integrations/<id>"""

    @patch("src.api.blueprints.integrations.integrations.AuditLogger")
    @patch("src.api.blueprints.integrations.integrations.get_current_user_context")
    def test_delete_success(self, mock_ctx, mock_audit, client, auth_headers, blueprint_app):
        mock_ctx.return_value = ("test-user-id", "testuser")
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.delete_integration.return_value = True

        resp = client.delete(
            f"/api/integrations/{INTEGRATION_ID}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True

    def test_delete_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.delete(
            f"/api/integrations/{INTEGRATION_ID}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404


# =============================================================================
# Test connection (GET /api/integrations/<id>/test)
# =============================================================================

class TestTestConnection:
    """GET /api/integrations/<id>/test"""

    def test_connection_success(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.test_connection.return_value = {
            "success": True, "message": "Connected"
        }

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/test",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_connection_failure(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.test_connection.return_value = {
            "success": False, "error": "Connection refused"
        }

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/test",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_connection_post_forwards_candidate_config(self, client, auth_headers, blueprint_app):
        """POST /test passes the wizard's candidate config through to the
        service as `candidate_config=` — fix for the edit-mode bug where the
        field-mapping step kept seeing source fields from the saved query."""
        blueprint_app.integration_service.test_connection.return_value = {
            "success": True, "message": "Connected"
        }

        candidate = {"powerquery": "dataSource.name='Sophos XGS'", "limit": 5000}
        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/test",
            json={"config": candidate},
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        blueprint_app.integration_service.test_connection.assert_called_once()
        _, kwargs = blueprint_app.integration_service.test_connection.call_args
        assert kwargs.get("candidate_config") == candidate

    def test_connection_get_passes_no_candidate(self, client, auth_headers, blueprint_app):
        """GET /test (legacy behaviour) must not synthesize a candidate — the
        service uses `candidate_config=None` as the signal that this is a
        saved-config test and the result should be persisted on the row."""
        blueprint_app.integration_service.test_connection.return_value = {
            "success": True, "message": "Connected"
        }

        client.get(
            f"/api/integrations/{INTEGRATION_ID}/test",
            headers=auth_headers("admin"),
        )

        _, kwargs = blueprint_app.integration_service.test_connection.call_args
        assert kwargs.get("candidate_config") is None


# =============================================================================
# Sample data (POST /api/integrations/<id>/sample)
# =============================================================================

class TestSampleData:
    """POST /api/integrations/<id>/sample"""

    def test_sample_success(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_sample_data.return_value = {
            "success": True, "records": [{"hostname": "srv01"}]
        }

        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/sample",
            json={"limit": 5},
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_sample_forwards_candidate_config(self, client, auth_headers, blueprint_app):
        """POST /sample with body `{config: ...}` reaches the service as
        candidate_config — pairs with the matching test on /test."""
        blueprint_app.integration_service.get_sample_data.return_value = {
            "success": True, "records": []
        }

        candidate = {"powerquery": "new query"}
        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/sample",
            json={"limit": 10, "config": candidate},
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        _, kwargs = blueprint_app.integration_service.get_sample_data.call_args
        assert kwargs.get("limit") == 10
        assert kwargs.get("candidate_config") == candidate


# =============================================================================
# Preview fetch (POST /api/integrations/preview/fetch)
# =============================================================================

class TestPreviewFetch:
    """POST /api/integrations/preview/fetch — the wizard's data-filter
    preview endpoint. Currently unreachable from the UI (data-filter preview
    is client-side), but the same class of bug as /test and /sample. These
    tests pin the merge + connector-type behaviour so re-wiring it later
    can't bring the bug back."""

    def test_candidate_config_skips_db_cache_and_reaches_connector(
        self, client, auth_headers, blueprint_app
    ):
        from unittest.mock import patch as _patch

        integration = _make_integration(
            integration_type="sophos",
            config={"powerquery": "OLD QUERY", "client_secret": "real-secret"},
        )
        integration.source_method = "sentinelone_siem"
        integration.effective_connector_type = "sentinelone_siem"
        blueprint_app.integration_service.repository.get_by_id.return_value = (
            integration
        )

        connector = MagicMock()
        connector.get_sample_data.return_value = [{"hostname": "srv-a"}]
        blueprint_app.integration_service.build_connector.return_value = connector

        # Candidate omits client_secret on purpose: the wizard's edit-mode
        # config view doesn't decrypt secrets, so users normally leave that
        # field blank and the merge must reach back to the saved value.
        candidate = {"powerquery": "NEW QUERY"}

        with _patch(
            "src.api.blueprints.integrations.preview._load_from_db"
        ) as load_from_db:
            resp = client.post(
                "/api/integrations/preview/fetch",
                json={
                    "integration_id": INTEGRATION_ID,
                    "session_id": "00000000-0000-0000-0000-000000000001",
                    "config": candidate,
                },
                headers=auth_headers("admin"),
            )

            assert resp.status_code == 200
            # Candidate present → MUST NOT consult the DB cache (rows there
            # were collected with the OLD query and would mislead the
            # preview).
            load_from_db.assert_not_called()

            # The connector is built via the service helper so it inherits
            # the merge + effective_connector_type logic — not constructed
            # directly inside the blueprint.
            blueprint_app.integration_service.build_connector.assert_called_once_with(
                integration, candidate
            )

    def test_no_candidate_uses_db_cache(
        self, client, auth_headers, blueprint_app
    ):
        from unittest.mock import patch as _patch

        integration = _make_integration(
            integration_type="sophos",
            config={"powerquery": "SAVED", "api_token": "real-token"},
        )
        integration.source_method = "sentinelone_siem"
        integration.effective_connector_type = "sentinelone_siem"
        blueprint_app.integration_service.repository.get_by_id.return_value = (
            integration
        )

        with _patch(
            "src.api.blueprints.integrations.preview._load_from_db",
            return_value=[{"hostname": "cached"}],
        ) as load_from_db:
            resp = client.post(
                "/api/integrations/preview/fetch",
                json={
                    "integration_id": INTEGRATION_ID,
                    "session_id": "00000000-0000-0000-0000-000000000002",
                },
                headers=auth_headers("admin"),
            )

            assert resp.status_code == 200
            # No candidate → fast path: DB cache wins, no connector needed.
            load_from_db.assert_called_once()
            blueprint_app.integration_service.build_connector.assert_not_called()


# =============================================================================
# Field schema (GET /api/integrations/<id>/schema)
# =============================================================================

class TestFieldSchema:
    """GET /api/integrations/<id>/schema"""

    def test_schema_success(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_field_schema.return_value = {
            "success": True, "fields": ["hostname", "ip_address"]
        }

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/schema",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200


# =============================================================================
# Run integration (POST /api/integrations/<id>/run)
# =============================================================================

class TestRunIntegration:
    """POST /api/integrations/<id>/run"""

    def test_run_success(self, client, auth_headers, blueprint_app):
        # Pre-flight check returns an enabled integration
        integration = _make_integration()
        integration.is_enabled.return_value = True
        blueprint_app.integration_service.get_integration.return_value = integration

        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/run",
            headers=auth_headers("admin"),
        )

        # Async endpoint returns 202 Accepted immediately
        assert resp.status_code == 202
        assert resp.get_json()["success"] is True

    def test_run_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/run",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "NOT_FOUND"

    def test_run_disabled(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        integration.is_enabled.return_value = False
        blueprint_app.integration_service.get_integration.return_value = integration

        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/run",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "DISABLED"


# =============================================================================
# Enable / Disable (PATCH /api/integrations/<id>/enable|disable)
# =============================================================================

class TestEnableDisable:
    """PATCH /api/integrations/<id>/enable and /disable"""

    @patch("src.api.blueprints.integrations.integrations.AuditLogger")
    @patch("src.api.blueprints.integrations.integrations.get_current_user_context")
    def test_enable_success(self, mock_ctx, mock_audit, client, auth_headers, blueprint_app):
        mock_ctx.return_value = ("test-user-id", "testuser")
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.enable_integration.return_value = True

        resp = client.patch(
            f"/api/integrations/{INTEGRATION_ID}/enable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    @patch("src.api.blueprints.integrations.integrations.AuditLogger")
    @patch("src.api.blueprints.integrations.integrations.get_current_user_context")
    def test_disable_success(self, mock_ctx, mock_audit, client, auth_headers, blueprint_app):
        mock_ctx.return_value = ("test-user-id", "testuser")
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.disable_integration.return_value = True

        resp = client.patch(
            f"/api/integrations/{INTEGRATION_ID}/disable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_enable_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.patch(
            f"/api/integrations/{INTEGRATION_ID}/enable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404

    def test_disable_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.patch(
            f"/api/integrations/{INTEGRATION_ID}/disable",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404


# =============================================================================
# Stats (GET /api/integrations/stats)
# =============================================================================

class TestIntegrationStats:
    """GET /api/integrations/stats"""

    def test_stats_success(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration_stats.return_value = {
            "total": 5, "enabled": 3, "disabled": 2
        }

        resp = client.get("/api/integrations/stats", headers=auth_headers("admin"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["stats"]["total"] == 5


# =============================================================================
# Logo map (GET /api/integrations/logo-map)
# =============================================================================

class TestLogoMap:
    """GET /api/integrations/logo-map -- any authenticated role."""

    def test_logo_map_returns_mapping(self, client, auth_headers, blueprint_app):
        integration = _make_integration(name="Jamf Pro", logo="jamf.png")
        blueprint_app.integration_service.list_integrations.return_value = [integration]

        resp = client.get(
            "/api/integrations/logo-map",
            headers=auth_headers("user"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "Jamf Pro" in body["logoMap"]
        assert body["logoMap"]["Jamf Pro"] == "/logos/jamf.png"

    def test_logo_map_empty(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.list_integrations.return_value = []

        resp = client.get(
            "/api/integrations/logo-map",
            headers=auth_headers("user"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["logoMap"] == {}

    def test_logo_map_handles_uploaded_logo_paths(
        self, client, auth_headers, blueprint_app,
    ):
        """``integration.logo`` may be a bare filename (bundled) or a full path
        (uploaded). The map must emit the right URL for both shapes — bundled
        gets ``/logos/`` prefixed, uploaded passes through unchanged."""
        bundled = _make_integration(name="Jamf Pro", logo="jamf.png")
        uploaded = _make_integration(
            name="Custom Vendor",
            logo="/api/integrations/logos/file/" + ("a" * 64) + ".png",
        )
        blueprint_app.integration_service.list_integrations.return_value = [
            bundled, uploaded,
        ]

        resp = client.get(
            "/api/integrations/logo-map",
            headers=auth_headers("user"),
        )

        assert resp.status_code == 200
        logo_map = resp.get_json()["logoMap"]
        assert logo_map["Jamf Pro"] == "/logos/jamf.png"
        # No double-prefix — uploaded value passes through as-is.
        assert logo_map["Custom Vendor"] == (
            "/api/integrations/logos/file/" + ("a" * 64) + ".png"
        )


# =============================================================================
# Logo file serving (GET /api/integrations/logos/file/<name>) — public route
# =============================================================================

class TestLogoFileServe:
    """No auth required so <img src=…> works; security is in the filename regex."""

    def test_serves_existing_uploaded_file(self, client, blueprint_app, tmp_path):
        blueprint_app.config['UPLOADED_LOGOS_DIR'] = str(tmp_path)
        sha = "b" * 64
        body = b"\x89PNG\r\n\x1a\n\x00fake"
        (tmp_path / f"{sha}.png").write_bytes(body)

        resp = client.get(f"/api/integrations/logos/file/{sha}.png")
        assert resp.status_code == 200
        # Bytes round-trip exactly
        assert resp.data == body

    def test_serves_uploaded_svg(self, client, blueprint_app, tmp_path):
        blueprint_app.config['UPLOADED_LOGOS_DIR'] = str(tmp_path)
        sha = "c" * 64
        (tmp_path / f"{sha}.svg").write_bytes(b"<svg/>")

        resp = client.get(f"/api/integrations/logos/file/{sha}.svg")
        assert resp.status_code == 200

    def test_rejects_non_hash_filename(self, client, blueprint_app, tmp_path):
        """Anything not matching the content-hash regex is a hard 404.

        Stops bundled-logo names, plain attempts, and anything weird from
        ever reaching ``send_from_directory``.
        """
        blueprint_app.config['UPLOADED_LOGOS_DIR'] = str(tmp_path)
        # Even if the file exists on disk, a non-hash name is rejected.
        (tmp_path / "jamf.png").write_bytes(b"x")
        resp = client.get("/api/integrations/logos/file/jamf.png")
        assert resp.status_code == 404

    def test_rejects_path_traversal(self, client, blueprint_app, tmp_path):
        """Multi-layer guard against `..` segments and absolute paths.

        The key invariant: NEVER return 200. Flask may canonicalize a
        double-slash URL into a 308 redirect; that's also fine because it
        doesn't expose any file content.
        """
        blueprint_app.config['UPLOADED_LOGOS_DIR'] = str(tmp_path)
        for traversal in (
            "../etc/passwd",
            "..%2Fetc%2Fpasswd",
            "/etc/passwd",
        ):
            resp = client.get(f"/api/integrations/logos/file/{traversal}")
            assert resp.status_code != 200, (
                f"Traversal {traversal!r} must not be served — got 200"
            )
            assert resp.status_code < 500, (
                f"Traversal {traversal!r} should be a clean reject, "
                f"not 5xx — got {resp.status_code}"
            )

    def test_404_when_directory_missing(self, client, blueprint_app, tmp_path):
        """Misconfigured upload dir => 404, never 500 from a stale FS path."""
        nonexistent = tmp_path / "does-not-exist"
        blueprint_app.config['UPLOADED_LOGOS_DIR'] = str(nonexistent)
        sha = "d" * 64
        resp = client.get(f"/api/integrations/logos/file/{sha}.png")
        assert resp.status_code == 404

    def test_disallowed_extension(self, client, blueprint_app, tmp_path):
        """Only .png / .svg are valid; .jpg etc. are rejected at the boundary."""
        blueprint_app.config['UPLOADED_LOGOS_DIR'] = str(tmp_path)
        sha = "e" * 64
        (tmp_path / f"{sha}.jpg").write_bytes(b"x")
        resp = client.get(f"/api/integrations/logos/file/{sha}.jpg")
        assert resp.status_code == 404
