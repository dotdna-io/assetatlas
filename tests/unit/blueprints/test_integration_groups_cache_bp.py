# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for integration group cache invalidation."""

from unittest.mock import patch


class TestGroupCacheInvalidation:
    """Group mutations must invalidate the group mapping cache."""

    @patch("src.api.blueprints.integrations.integration_groups.invalidate_group_mapping_cache")
    def test_create_group_invalidates_cache(self, mock_invalidate, client, authed_headers, blueprint_app):
        blueprint_app.integration_group_service.create_group.return_value = {
            "id": "g1", "name": "Entra", "integration_type": "entra"
        }
        resp = client.post(
            "/api/integrations/groups",
            json={"name": "Entra", "integration_type": "entra"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 201
        mock_invalidate.assert_called_once()

    @patch("src.api.blueprints.integrations.integration_groups.invalidate_group_mapping_cache")
    def test_update_group_invalidates_cache(self, mock_invalidate, client, authed_headers, blueprint_app):
        blueprint_app.integration_group_service.update_group.return_value = {
            "id": "g1", "name": "Entra Updated"
        }
        resp = client.put(
            "/api/integrations/groups/g1",
            json={"name": "Entra Updated"},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        mock_invalidate.assert_called_once()

    @patch("src.api.blueprints.integrations.integration_groups.invalidate_group_mapping_cache")
    def test_delete_group_invalidates_cache(self, mock_invalidate, client, authed_headers, blueprint_app):
        resp = client.delete(
            "/api/integrations/groups/g1",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        mock_invalidate.assert_called_once()

    @patch("src.api.blueprints.integrations.integration_groups.invalidate_group_mapping_cache")
    def test_add_members_invalidates_cache(self, mock_invalidate, client, authed_headers, blueprint_app):
        resp = client.post(
            "/api/integrations/groups/g1/members",
            json={"integration_ids": ["i1", "i2"]},
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        mock_invalidate.assert_called_once()

    @patch("src.api.blueprints.integrations.integration_groups.invalidate_group_mapping_cache")
    def test_remove_member_invalidates_cache(self, mock_invalidate, client, authed_headers, blueprint_app):
        resp = client.delete(
            "/api/integrations/groups/g1/members/i1",
            headers=authed_headers("admin"),
        )
        assert resp.status_code == 200
        mock_invalidate.assert_called_once()
