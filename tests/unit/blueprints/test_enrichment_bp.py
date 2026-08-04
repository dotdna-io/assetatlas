# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the enrichment blueprint (blueprint harness).

Ported from ``tests/unit/test_enrichment/test_enrichment_routes.py`` (#1292):
the old file sat outside the blueprint harness, so the JWT revocation lookup
failed closed on the mocked connection_manager and every authenticated
request 401'd. Here the autouse ``_stub_jwt_session_lookup`` /
``_prime_user_cache`` fixtures make authed requests work, and
``authed_headers(role)`` primes the user cache per role.

Endpoints tested:
- GET    /api/enrichment/datasets                      — List datasets (auth)
- GET    /api/enrichment/datasets/<id>                 — Get dataset (auth)
- DELETE /api/enrichment/datasets/<id>                 — Delete dataset (admin)
- PUT    /api/enrichment/datasets/<id>/status           — Update status (admin)
- POST   /api/enrichment/upload                        — Upload CSV (admin)
- POST   /api/enrichment/datasets/<id>/preview         — Preview matches (admin)
- POST   /api/enrichment/datasets/<id>/apply           — Apply dataset (admin)
"""

import io
from unittest.mock import patch


# =============================================================================
# Auth requirements
# =============================================================================

class TestEnrichmentAuth:
    """All enrichment endpoints require JWT authentication."""

    def test_list_requires_auth(self, client):
        resp = client.get("/api/enrichment/datasets")
        assert resp.status_code == 401

    def test_get_requires_auth(self, client):
        resp = client.get("/api/enrichment/datasets/ds-1")
        assert resp.status_code == 401

    def test_delete_requires_auth(self, client):
        resp = client.delete("/api/enrichment/datasets/ds-1")
        assert resp.status_code == 401

    def test_update_status_requires_auth(self, client):
        resp = client.put(
            "/api/enrichment/datasets/ds-1/status",
            json={"status": "active"},
        )
        assert resp.status_code == 401

    def test_upload_requires_auth(self, client):
        resp = client.post("/api/enrichment/upload")
        assert resp.status_code == 401

    def test_preview_requires_auth(self, client):
        resp = client.post("/api/enrichment/datasets/ds-1/preview")
        assert resp.status_code == 401

    def test_apply_requires_auth(self, client):
        resp = client.post("/api/enrichment/datasets/ds-1/apply")
        assert resp.status_code == 401


# =============================================================================
# RBAC — admin-only endpoints reject regular users
# =============================================================================

class TestEnrichmentRBAC:
    """Admin-only endpoints return 403 for regular users."""

    def test_delete_forbidden_for_user(self, client, authed_headers):
        resp = client.delete(
            "/api/enrichment/datasets/ds-1",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403

    def test_update_status_forbidden_for_user(self, client, authed_headers):
        resp = client.put(
            "/api/enrichment/datasets/ds-1/status",
            json={"status": "active"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403

    def test_upload_forbidden_for_user(self, client, authed_headers):
        resp = client.post(
            "/api/enrichment/upload",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403

    def test_preview_forbidden_for_user(self, client, authed_headers):
        resp = client.post(
            "/api/enrichment/datasets/ds-1/preview",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403

    def test_apply_forbidden_for_user(self, client, authed_headers):
        resp = client.post(
            "/api/enrichment/datasets/ds-1/apply",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403


# =============================================================================
# GET /api/enrichment/datasets
# =============================================================================

class TestListDatasets:
    """GET /api/enrichment/datasets -- list enrichment datasets."""

    def test_list_returns_datasets(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.list_datasets.return_value = [
            {"id": "ds-1", "name": "Test"},
        ]
        resp = client.get("/api/enrichment/datasets", headers=authed_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert len(body["data"]["datasets"]) == 1

    def test_list_empty(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.list_datasets.return_value = []
        resp = client.get("/api/enrichment/datasets", headers=authed_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["datasets"] == []

    def test_list_accessible_by_regular_user(self, client, authed_headers, blueprint_app):
        """Regular users can list datasets (no admin required)."""
        blueprint_app.enrichment_service.list_datasets.return_value = []
        resp = client.get("/api/enrichment/datasets", headers=authed_headers("user"))
        assert resp.status_code == 200


# =============================================================================
# GET /api/enrichment/datasets/<dataset_id>
# =============================================================================

class TestGetDataset:
    """GET /api/enrichment/datasets/<id> -- get dataset details."""

    def test_get_returns_dataset(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.get_dataset.return_value = {
            "id": "ds-1",
            "name": "Test",
            "sample_rows": [],
        }
        resp = client.get("/api/enrichment/datasets/ds-1", headers=authed_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["dataset"]["id"] == "ds-1"

    def test_get_not_found_returns_404(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.get_dataset.return_value = None
        resp = client.get("/api/enrichment/datasets/ds-999", headers=authed_headers())
        assert resp.status_code == 404

    def test_get_accessible_by_regular_user(self, client, authed_headers, blueprint_app):
        """Regular users can get dataset details (no admin required)."""
        blueprint_app.enrichment_service.get_dataset.return_value = {
            "id": "ds-1",
            "name": "Test",
        }
        resp = client.get("/api/enrichment/datasets/ds-1", headers=authed_headers("user"))
        assert resp.status_code == 200


# =============================================================================
# DELETE /api/enrichment/datasets/<dataset_id>
# =============================================================================

class TestDeleteDataset:
    """DELETE /api/enrichment/datasets/<id> -- delete a dataset (admin)."""

    def test_delete_success(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.delete_dataset.return_value = True
        resp = client.delete(
            "/api/enrichment/datasets/ds-1",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["message"] == "Dataset deleted"

    def test_delete_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.delete_dataset.return_value = False
        resp = client.delete(
            "/api/enrichment/datasets/ds-999",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_delete_calls_service(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.delete_dataset.return_value = True
        client.delete(
            "/api/enrichment/datasets/ds-1",
            headers=authed_headers("admin"),
        )
        blueprint_app.enrichment_service.delete_dataset.assert_called_once_with("ds-1")


# =============================================================================
# PUT /api/enrichment/datasets/<dataset_id>/status
# =============================================================================

class TestUpdateDatasetStatus:
    """PUT /api/enrichment/datasets/<id>/status -- update status (admin)."""

    def test_update_status_active(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.update_status.return_value = True
        resp = client.put(
            "/api/enrichment/datasets/ds-1/status",
            json={"status": "active"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "active" in body["data"]["message"]

    def test_update_status_inactive(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.update_status.return_value = True
        resp = client.put(
            "/api/enrichment/datasets/ds-1/status",
            json={"status": "inactive"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "inactive" in body["data"]["message"]

    def test_update_status_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.update_status.return_value = False
        resp = client.put(
            "/api/enrichment/datasets/ds-999/status",
            json={"status": "active"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_update_status_invalid_value(self, client, authed_headers):
        resp = client.put(
            "/api/enrichment/datasets/ds-1/status",
            json={"status": "deleted"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_update_status_missing_body(self, client, authed_headers):
        resp = client.put(
            "/api/enrichment/datasets/ds-1/status",
            headers=authed_headers("admin"),
            content_type="application/json",
            data="null",
        )
        assert resp.status_code == 400

    def test_update_status_calls_service(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.update_status.return_value = True
        client.put(
            "/api/enrichment/datasets/ds-1/status",
            json={"status": "inactive"},
            headers=authed_headers("admin"),
        )
        blueprint_app.enrichment_service.update_status.assert_called_once_with(
            "ds-1", "inactive"
        )


# =============================================================================
# POST /api/enrichment/upload
# =============================================================================

class TestUploadDataset:
    """POST /api/enrichment/upload -- upload a CSV dataset (admin)."""

    def _make_upload_data(self, **overrides):
        """Build multipart form data for a CSV upload."""
        defaults = {
            "file": (io.BytesIO(b"subnet,location\n10.0.0.0/24,Building A\n"), "test.csv"),
            "name": "Test Dataset",
            "match_field": "ip",
            "match_type": "cidr",
            "target_field": "subnet",
            "match_col": "subnet",
            "enrich_col": "location",
        }
        defaults.update(overrides)
        return defaults

    def test_upload_success(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.upload_csv.return_value = {
            "dataset_id": "ds-new",
            "row_count": 1,
            "preview": [{"subnet": "10.0.0.0/24", "location": "Building A"}],
            "columns": ["subnet", "location"],
        }
        resp = client.post(
            "/api/enrichment/upload",
            headers=authed_headers("admin"),
            data=self._make_upload_data(),
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["dataset_id"] == "ds-new"

    def test_upload_requires_file(self, client, authed_headers):
        resp = client.post(
            "/api/enrichment/upload",
            headers=authed_headers("admin"),
            data={"name": "Test"},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert "file" in body["message"].lower()

    def test_upload_requires_name(self, client, authed_headers):
        data = self._make_upload_data()
        del data["name"]
        resp = client.post(
            "/api/enrichment/upload",
            headers=authed_headers("admin"),
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_upload_requires_match_field(self, client, authed_headers):
        data = self._make_upload_data()
        del data["match_field"]
        resp = client.post(
            "/api/enrichment/upload",
            headers=authed_headers("admin"),
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_upload_requires_target_field(self, client, authed_headers):
        data = self._make_upload_data()
        del data["target_field"]
        resp = client.post(
            "/api/enrichment/upload",
            headers=authed_headers("admin"),
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_upload_requires_match_col(self, client, authed_headers):
        data = self._make_upload_data()
        del data["match_col"]
        resp = client.post(
            "/api/enrichment/upload",
            headers=authed_headers("admin"),
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_upload_requires_enrich_col(self, client, authed_headers):
        data = self._make_upload_data()
        del data["enrich_col"]
        resp = client.post(
            "/api/enrichment/upload",
            headers=authed_headers("admin"),
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_upload_service_error_returns_400(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.upload_csv.return_value = {
            "error": "CSV is empty"
        }
        resp = client.post(
            "/api/enrichment/upload",
            headers=authed_headers("admin"),
            data=self._make_upload_data(),
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert "CSV is empty" in body["message"]

    def test_upload_invalid_match_field_returns_400(self, client, authed_headers):
        resp = client.post(
            "/api/enrichment/upload",
            headers=authed_headers("admin"),
            data=self._make_upload_data(match_field="nonexistent"),
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_upload_calls_service_with_correct_args(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.upload_csv.return_value = {
            "dataset_id": "ds-new",
            "row_count": 1,
            "preview": [],
            "columns": ["subnet", "location"],
        }
        client.post(
            "/api/enrichment/upload",
            headers=authed_headers("admin"),
            data=self._make_upload_data(),
            content_type="multipart/form-data",
        )
        call_kwargs = blueprint_app.enrichment_service.upload_csv.call_args[1]
        assert call_kwargs["name"] == "Test Dataset"
        assert call_kwargs["match_field"] == "ip"
        assert call_kwargs["match_type"] == "cidr"
        assert call_kwargs["target_field"] == "subnet"
        assert call_kwargs["match_col"] == "subnet"
        assert call_kwargs["enrich_col"] == "location"
        assert call_kwargs["uploaded_by"] == "testuser"


# =============================================================================
# POST /api/enrichment/datasets/<dataset_id>/preview
# =============================================================================

class TestPreviewDataset:
    """POST /api/enrichment/datasets/<id>/preview -- preview matches (admin)."""

    def test_preview_success(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.preview_dataset.return_value = {
            "dataset_id": "ds-1",
            "dataset_name": "Test",
            "matched_count": 5,
            "total_rows": 10,
            "sample_matches": [],
        }
        resp = client.post(
            "/api/enrichment/datasets/ds-1/preview",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["matched_count"] == 5

    def test_preview_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.preview_dataset.return_value = None
        resp = client.post(
            "/api/enrichment/datasets/ds-999/preview",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404


# =============================================================================
# POST /api/enrichment/datasets/<dataset_id>/apply
# =============================================================================

class TestApplyDataset:
    """POST /api/enrichment/datasets/<id>/apply -- apply dataset (admin)."""

    def test_apply_success(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.apply_dataset.return_value = {
            "dataset_id": "ds-1",
            "dataset_name": "Test",
            "matched_count": 42,
        }
        resp = client.post(
            "/api/enrichment/datasets/ds-1/apply",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["matched_count"] == 42

    def test_apply_not_found(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.apply_dataset.return_value = None
        resp = client.post(
            "/api/enrichment/datasets/ds-999/apply",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 404

    def test_apply_resolution_error_returns_400(self, client, authed_headers, blueprint_app):
        # A dataset whose match_field no longer resolves returns a clear 400 with
        # the reason (NotFoundError is reserved for a missing dataset → None).
        blueprint_app.enrichment_service.apply_dataset.return_value = {
            "error": "match_field 'group:Gone:building' is not a known asset field"
        }
        resp = client.post(
            "/api/enrichment/datasets/ds-bad/apply",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 400

    def test_apply_calls_service(self, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.apply_dataset.return_value = {
            "dataset_id": "ds-1",
            "dataset_name": "Test",
            "matched_count": 0,
        }
        client.post(
            "/api/enrichment/datasets/ds-1/apply",
            headers=authed_headers("admin"),
        )
        blueprint_app.enrichment_service.apply_dataset.assert_called_once_with("ds-1")


# =============================================================================
# Audit emission (#1290, #1292) — the four mutating endpoints log with the vocab
# =============================================================================

class TestEnrichmentAudit:
    """Each mutating endpoint emits exactly one AuditLogger event carrying the
    shared vocabulary and the real JWT user context. Preview stays silent by
    design (compute-only, nothing is mutated)."""

    @patch("src.api.blueprints.enrichment.enrichment_routes.AuditLogger")
    def test_delete_dataset(self, mock_audit, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.delete_dataset.return_value = True
        resp = client.delete(
            "/api/enrichment/datasets/ds-1",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        mock_audit.log_data_operation.assert_called_once()
        _, kwargs = mock_audit.log_data_operation.call_args
        assert kwargs["resource"] == "enrichment_dataset"
        assert kwargs["resource_id"] == "ds-1"
        assert kwargs["action"] == "delete"
        assert (kwargs["user_id"], kwargs["username"]) == ("test-user-id", "testuser")

    @patch("src.api.blueprints.enrichment.enrichment_routes.AuditLogger")
    def test_update_dataset_status(self, mock_audit, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.update_status.return_value = True
        resp = client.put(
            "/api/enrichment/datasets/ds-1/status",
            json={"status": "active"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        mock_audit.log_data_operation.assert_called_once()
        _, kwargs = mock_audit.log_data_operation.call_args
        assert kwargs["resource"] == "enrichment_dataset"
        assert kwargs["resource_id"] == "ds-1"
        assert kwargs["action"] == "update"
        assert kwargs["details"] == {"status": "active"}
        assert (kwargs["user_id"], kwargs["username"]) == ("test-user-id", "testuser")

    @patch("src.api.blueprints.enrichment.enrichment_routes.AuditLogger")
    def test_apply_dataset(self, mock_audit, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.apply_dataset.return_value = {
            "dataset_id": "ds-1",
            "dataset_name": "Test",
            "matched_count": 42,
        }
        resp = client.post(
            "/api/enrichment/datasets/ds-1/apply",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        mock_audit.log_data_operation.assert_called_once()
        _, kwargs = mock_audit.log_data_operation.call_args
        assert kwargs["resource"] == "enrichment_dataset"
        assert kwargs["resource_id"] == "ds-1"
        assert kwargs["action"] == "apply"
        assert kwargs["details"] == {"matched_count": 42}
        assert (kwargs["user_id"], kwargs["username"]) == ("test-user-id", "testuser")

    @patch("src.api.blueprints.enrichment.enrichment_routes.AuditLogger")
    def test_upload_dataset(self, mock_audit, client, authed_headers, blueprint_app):
        blueprint_app.enrichment_service.upload_csv.return_value = {
            "dataset_id": "ds-new",
            "row_count": 1,
            "preview": [],
            "columns": ["subnet", "location"],
        }
        resp = client.post(
            "/api/enrichment/upload",
            headers=authed_headers("admin"),
            data={
                "file": (io.BytesIO(b"subnet,location\n10.0.0.0/24,Building A\n"), "test.csv"),
                "name": "Test Dataset",
                "match_field": "ip",
                "match_type": "cidr",
                "target_field": "subnet",
                "match_col": "subnet",
                "enrich_col": "location",
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        mock_audit.log_data_operation.assert_called_once()
        _, kwargs = mock_audit.log_data_operation.call_args
        assert kwargs["resource"] == "enrichment_dataset"
        assert kwargs["resource_id"] == "ds-new"
        assert kwargs["action"] == "create"
        assert kwargs["details"] == {"name": "Test Dataset", "row_count": 1}
        assert (kwargs["user_id"], kwargs["username"]) == ("test-user-id", "testuser")
