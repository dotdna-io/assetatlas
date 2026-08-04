# SPDX-License-Identifier: AGPL-3.0-or-later
"""RBAC tests for integration group routes.

The dashboard homepage renders Data Source Groups for every role, so the
read-only group listing must be visible to plain users — only mutations
stay admin-gated.
"""


class TestGroupListRbac:
    def test_regular_user_can_list_groups(self, client, authed_headers, blueprint_app):
        """GET /groups feeds the homepage, which every role can see."""
        blueprint_app.integration_group_service.list_groups.return_value = []
        resp = client.get(
            "/api/integrations/groups",
            headers=authed_headers("user"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["groups"] == []

    def test_regular_user_cannot_create_group(self, client, authed_headers):
        """Mutations remain admin-only."""
        resp = client.post(
            "/api/integrations/groups",
            json={"name": "Entra", "integration_type": "entra"},
            headers=authed_headers("user"),
        )
        assert resp.status_code == 403
