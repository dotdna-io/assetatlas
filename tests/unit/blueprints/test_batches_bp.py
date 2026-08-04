# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the batches blueprint (admin-only).

All endpoints live under /api/integrations/ and require admin role.

Endpoints tested:
- GET    /api/integrations/<id>/batches                          — list batches
- GET    /api/integrations/<id>/batches/<batch_id>               — get batch details
- GET    /api/integrations/<id>/batches/<batch_id>/records       — get raw records
- DELETE /api/integrations/<id>/batches/<batch_id>               — delete batch
- GET    /api/integrations/batches/stats                         — global batch stats
- GET    /api/integrations/<id>/stats/batches                    — per-integration stats
- POST   /api/integrations/<id>/batches/<batch_id>/map           — trigger mapping
- POST   /api/integrations/<id>/batches/<batch_id>/remap         — remap batch
- GET    /api/integrations/<id>/mapping/stats                    — mapping stats
- GET    /api/integrations/<id>/batches/<batch_id>/mapped-records — get mapped records
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INTEGRATION_ID = "11111111-1111-1111-1111-111111111111"
BATCH_ID = "22222222-2222-2222-2222-222222222222"
OTHER_INTEGRATION_ID = "33333333-3333-3333-3333-333333333333"


def _make_integration(**overrides):
    integration = MagicMock()
    integration.id = overrides.get("id", INTEGRATION_ID)
    integration.name = overrides.get("name", "Test Jamf")
    return integration


def _make_batch(**overrides):
    """Return a batch dict as the repository would."""
    started = datetime(2025, 1, 1, 10, 0, 0)
    completed = started + timedelta(seconds=30)
    base = {
        "id": overrides.get("id", BATCH_ID),
        "integration_id": overrides.get("integration_id", INTEGRATION_ID),
        "status": overrides.get("status", "completed"),
        "started_at": overrides.get("started_at", started),
        "completed_at": overrides.get("completed_at", completed),
        "raw_records_count": overrides.get("raw_records_count", 100),
        "mapped_records_count": overrides.get("mapped_records_count", 95),
        "assets_created_count": overrides.get("assets_created_count", 90),
        "error_message": overrides.get("error_message", None),
    }
    base.update(overrides)
    return base


# =============================================================================
# Auth / RBAC
# =============================================================================

class TestBatchesAuth:
    """All batch endpoints require JWT + admin role."""

    @pytest.mark.parametrize("method,path", [
        ("GET",    f"/api/integrations/{INTEGRATION_ID}/batches"),
        ("GET",    f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}"),
        ("GET",    f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/records"),
        ("DELETE", f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}"),
        ("GET",    "/api/integrations/batches/stats"),
        ("GET",    f"/api/integrations/{INTEGRATION_ID}/stats/batches"),
        ("POST",   f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/map"),
        ("POST",   f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/remap"),
        ("GET",    f"/api/integrations/{INTEGRATION_ID}/mapping/stats"),
        ("GET",    f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/mapped-records"),
    ])
    def test_no_auth_returns_401(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path", [
        ("GET",    f"/api/integrations/{INTEGRATION_ID}/batches"),
        ("GET",    f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}"),
        ("DELETE", f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}"),
        ("GET",    "/api/integrations/batches/stats"),
        ("GET",    f"/api/integrations/{INTEGRATION_ID}/stats/batches"),
    ])
    def test_non_admin_returns_403(self, client, auth_headers, _prime_regular_user, method, path):
        resp = getattr(client, method.lower())(path, headers=auth_headers("user"))
        assert resp.status_code == 403


# =============================================================================
# List batches (GET /api/integrations/<id>/batches)
# =============================================================================

class TestListBatches:
    """GET /api/integrations/<id>/batches"""

    def test_list_success(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.list_batches.return_value = [
            _make_batch()
        ]
        blueprint_app.integration_service.batch_repository.count_batches.return_value = 1

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/batches",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["total"] == 1
        assert len(body["batches"]) == 1
        # Check that duration is calculated
        assert body["batches"][0]["duration_seconds"] == 30

    def test_list_with_pagination(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.list_batches.return_value = []
        blueprint_app.integration_service.batch_repository.count_batches.return_value = 0

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/batches?limit=10&offset=20",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["limit"] == 10
        assert body["offset"] == 20

    def test_list_limit_capped_at_200(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.list_batches.return_value = []
        blueprint_app.integration_service.batch_repository.count_batches.return_value = 0

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/batches?limit=500",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["limit"] == 200

    def test_list_integration_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/batches",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404

    def test_list_with_status_filter(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.list_batches.return_value = []
        blueprint_app.integration_service.batch_repository.count_batches.return_value = 0

        client.get(
            f"/api/integrations/{INTEGRATION_ID}/batches?status=completed",
            headers=auth_headers("admin"),
        )

        _, kwargs = blueprint_app.integration_service.batch_repository.list_batches.call_args
        # The status filter should be passed through
        # list_batches signature: (integration_id, limit, offset, status)
        args = blueprint_app.integration_service.batch_repository.list_batches.call_args[0]
        assert args[3] == "completed"


# =============================================================================
# Get batch (GET /api/integrations/<id>/batches/<batch_id>)
# =============================================================================

class TestGetBatch:
    """GET /api/integrations/<id>/batches/<batch_id>"""

    def test_get_batch_success(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = _make_batch()

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["batch"]["integration_name"] == "Test Jamf"
        assert body["batch"]["duration_seconds"] == 30

    def test_get_batch_not_found(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = None

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404

    def test_get_batch_wrong_integration(self, client, auth_headers, blueprint_app):
        """Batch belongs to a different integration -- returns 403."""
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = _make_batch(
            integration_id=OTHER_INTEGRATION_ID,
        )

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}",
            headers=auth_headers("admin"),
        )

        # AuthError returns 401 per the error handler
        assert resp.status_code == 401

    def test_get_batch_integration_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404


# =============================================================================
# Get batch records (GET /api/integrations/<id>/batches/<batch_id>/records)
# =============================================================================

class TestGetBatchRecords:
    """GET /api/integrations/<id>/batches/<batch_id>/records"""

    def test_records_success(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = _make_batch()
        blueprint_app.integration_service.raw_data_repository.get_records.return_value = [
            {"id": "r1", "data": {"hostname": "srv01"}}
        ]
        blueprint_app.integration_service.raw_data_repository.count_records.return_value = 1

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/records",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["total"] == 1

    def test_records_limit_capped_at_500(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = _make_batch()
        blueprint_app.integration_service.raw_data_repository.get_records.return_value = []
        blueprint_app.integration_service.raw_data_repository.count_records.return_value = 0

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/records?limit=1000",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["limit"] == 500


# =============================================================================
# Delete batch (DELETE /api/integrations/<id>/batches/<batch_id>)
# =============================================================================

class TestDeleteBatch:
    """DELETE /api/integrations/<id>/batches/<batch_id>"""

    def test_delete_success(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = _make_batch()

        resp = client.delete(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        blueprint_app.integration_service.batch_repository.delete_batch.assert_called_once()

    def test_delete_batch_not_found(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = None

        resp = client.delete(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404

    def test_delete_wrong_integration(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = _make_batch(
            integration_id=OTHER_INTEGRATION_ID,
        )

        resp = client.delete(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}",
            headers=auth_headers("admin"),
        )

        # AuthError
        assert resp.status_code == 401


# =============================================================================
# Global batch stats (GET /api/integrations/batches/stats)
# =============================================================================

class TestGlobalBatchStats:
    """GET /api/integrations/batches/stats"""

    def test_global_stats_success(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.batch_repository.get_global_batch_stats.return_value = {
            "total_batches": 100,
            "total_records": 5000,
        }

        resp = client.get(
            "/api/integrations/batches/stats",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["total_batches"] == 100


# =============================================================================
# Per-integration batch stats (GET /api/integrations/<id>/stats/batches)
# =============================================================================

class TestBatchStats:
    """GET /api/integrations/<id>/stats/batches"""

    def test_batch_stats_success(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch_stats.return_value = {
            "total_batches": 10, "avg_records": 50
        }
        blueprint_app.integration_service.raw_data_repository.get_integration_stats.return_value = {
            "total_raw_records": 500
        }

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/stats/batches",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["stats"]["total_batches"] == 10
        assert body["stats"]["raw_data"]["total_raw_records"] == 500

    def test_batch_stats_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/stats/batches",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404


# =============================================================================
# Trigger mapping (POST /api/integrations/<id>/batches/<batch_id>/map)
# =============================================================================

class TestTriggerMapping:
    """POST /api/integrations/<id>/batches/<batch_id>/map"""

    def test_map_success(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = _make_batch()
        blueprint_app.integration_service.mapping_service.map_batch.return_value = {
            "success": True, "mapped": 95
        }

        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/map",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_map_batch_not_found(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = None

        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/map",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404

    def test_map_wrong_integration(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = _make_batch(
            integration_id=OTHER_INTEGRATION_ID,
        )

        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/map",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 403

    def test_map_failure(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = _make_batch()
        blueprint_app.integration_service.mapping_service.map_batch.return_value = {
            "success": False, "error": "No field mapping configured"
        }

        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/map",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 400


# =============================================================================
# Remap batch (POST /api/integrations/<id>/batches/<batch_id>/remap)
# =============================================================================

class TestRemapBatch:
    """POST /api/integrations/<id>/batches/<batch_id>/remap"""

    def test_remap_success(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = _make_batch()
        blueprint_app.integration_service.mapping_service.remap_batch.return_value = {
            "success": True, "remapped": 95
        }

        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/remap",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_remap_integration_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.post(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/remap",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404


# =============================================================================
# Mapping stats (GET /api/integrations/<id>/mapping/stats)
# =============================================================================

class TestMappingStats:
    """GET /api/integrations/<id>/mapping/stats"""

    def test_mapping_stats_success(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.mapping_service.get_mapping_stats.return_value = {
            "total_mapped": 500, "coverage": {"hostname": 100, "ip": 95}
        }

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/mapping/stats",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["stats"]["total_mapped"] == 500

    def test_mapping_stats_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/mapping/stats",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404


# =============================================================================
# Mapped records (GET /api/integrations/<id>/batches/<batch_id>/mapped-records)
# =============================================================================

class TestMappedRecords:
    """GET /api/integrations/<id>/batches/<batch_id>/mapped-records"""

    def test_mapped_records_success(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = _make_batch()

        with patch(
            "src.modules.integrations.repositories.raw_asset_repository.RawAssetRepository"
        ) as MockRawAssetRepo:
            raw_asset_repo = MockRawAssetRepo.return_value
            raw_asset_repo.get_by_batch.return_value = [
                {"id": "r1", "hostname": "srv01"}
            ]
            raw_asset_repo.count_by_batch.return_value = 1

            resp = client.get(
                f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/mapped-records",
                headers=auth_headers("admin"),
            )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["total"] == 1

    def test_mapped_records_batch_not_found(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_batch.return_value = None

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/batches/{BATCH_ID}/mapped-records",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404


# =============================================================================
# Get raw data (GET /api/integrations/<id>/raw-data)
# =============================================================================

class TestGetRawData:
    """GET /api/integrations/<id>/raw-data"""

    def test_returns_latest_batch_records_with_schema(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_latest_completed_batch.return_value = _make_batch()
        blueprint_app.integration_service.raw_data_repository.get_records.return_value = [
            {"id": 1, "source_record_id": "r1", "data": {"hostname": "srv01", "ip": "10.0.0.1"}, "collected_at": "2025-01-01T10:00:00"},
        ]
        blueprint_app.integration_service.raw_data_repository.count_records.return_value = 1

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/raw-data",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["batch_id"] == BATCH_ID
        assert len(body["records"]) == 1
        assert len(body["field_schema"]) > 0
        paths = {f["path"] for f in body["field_schema"]}
        assert "hostname" in paths
        assert "ip" in paths

    def test_no_batches_returns_empty(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_latest_completed_batch.return_value = None

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/raw-data",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["batch_id"] is None
        assert body["records"] == []
        assert body["field_schema"] == []

    def test_integration_not_found(self, client, auth_headers, blueprint_app):
        blueprint_app.integration_service.get_integration.return_value = None

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/raw-data",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 404

    def test_requires_admin(self, client, auth_headers, _prime_regular_user):
        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/raw-data",
            headers=auth_headers("user"),
        )
        assert resp.status_code == 403

    def test_pagination_params(self, client, auth_headers, blueprint_app):
        integration = _make_integration()
        blueprint_app.integration_service.get_integration.return_value = integration
        blueprint_app.integration_service.batch_repository.get_latest_completed_batch.return_value = _make_batch()
        blueprint_app.integration_service.raw_data_repository.get_records.return_value = []
        blueprint_app.integration_service.raw_data_repository.count_records.return_value = 0

        resp = client.get(
            f"/api/integrations/{INTEGRATION_ID}/raw-data?limit=10&offset=20",
            headers=auth_headers("admin"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["limit"] == 10
        assert body["offset"] == 20
