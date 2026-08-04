# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the audit-trail blueprint (GET /api/audit-trail, admin-only).

Issue #634: routes now LEFT JOIN ``users`` so the User column shows the live
username instead of the stale ``audit_trail.username`` snapshot (which held
a ``user_<uuid>`` placeholder).

Uses a minimal Flask app per ``test_account_bp.py`` rather than the shared
``blueprint_app`` fixture (which currently 401s on ``@jwt_required()`` routes
via the mocked SessionRepository in the token-blocklist loader).
"""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token

from src.api.blueprints.audit_trail import audit_trail_bp


def _make_user(role: str = "admin") -> dict:
    return {
        "id": "test-user-id",
        "username": "alice",
        "email": "alice@test.local",
        "role": role,
        "is_active": True,
    }


@pytest.fixture
def app():
    """Minimal Flask app exposing /api/audit-trail with mocked db_adapter.

    user_lookup_loader is parameterised on each test via ``app._test_user``
    so the same fixture covers admin/user/inactive cases without rebuilding
    the app.
    """
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "test-secret"
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(seconds=900)

    jwt = JWTManager(app)
    app._test_user = _make_user("admin")  # type: ignore[attr-defined]

    @jwt.user_identity_loader
    def _id(user):
        return str(user["id"]) if isinstance(user, dict) else str(user)

    @jwt.user_lookup_loader
    def _lookup(_h, _data):
        return app._test_user  # type: ignore[attr-defined]

    app.db_adapter = MagicMock(name="db_adapter")
    app.register_blueprint(audit_trail_bp)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_header(app):
    user = _make_user("admin")
    with app.app_context():
        token = create_access_token(identity=user)
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# Auth requirement
# =============================================================================

class TestAuditTrailAuth:
    """The audit trail is admin-only."""

    def test_list_requires_auth(self, client):
        resp = client.get("/api/audit-trail")
        assert resp.status_code == 401

    def test_list_forbids_non_admin(self, app, client, auth_header):
        app._test_user = _make_user("user")  # type: ignore[attr-defined]
        resp = client.get("/api/audit-trail", headers=auth_header)
        assert resp.status_code == 403


# =============================================================================
# Username resolution (issue #634)
# =============================================================================

class TestUsernameResolution:
    """Issue #634 — the User column must show the live username from the
    users table, not the ``user_<uuid>`` placeholder stored on the row."""

    def test_response_contains_live_username(self, app, client, auth_header):
        """The data query returns ``users.username``; the API surfaces it
        verbatim so the UI no longer falls back to ``user_id``."""
        # First call -> COUNT(*), second call -> data page
        app.db_adapter.execute_query.side_effect = [
            [{"total": 1}],
            [{
                "id": 42,
                "timestamp": "2026-05-10T09:00:00Z",
                "user_id": "00000000-0000-0000-0000-0000000000aa",
                "username": "admin",  # would be NULL or "user_<uuid>" before the fix
                "method": "POST",
                "path": "/api/assets",
                "status_code": 200,
                "ip_address": "127.0.0.1",
                "duration_ms": 12,
                "request_id": "req-1",
                "request_body": None,
                "response_summary": None,
            }],
        ]

        resp = client.get("/api/audit-trail", headers=auth_header)

        assert resp.status_code == 200
        body = resp.get_json()
        # The route is decorated with ``@response_wrapper``, which wraps
        # the dict in ``{"success": True, "data": ...}``.
        entries = body["data"]["entries"]
        assert len(entries) == 1
        assert entries[0]["username"] == "admin"

    def test_data_query_left_joins_users_and_selects_users_username(
        self, app, client, auth_header,
    ):
        """The SQL must read ``u.username`` via ``LEFT JOIN users u``. Without
        the join the column would be ``audit_trail.username`` — the bug we
        are fixing."""
        app.db_adapter.execute_query.side_effect = [
            [{"total": 0}],
            [],
        ]

        client.get("/api/audit-trail", headers=auth_header)

        # Two calls: count and data. The data query is the second one.
        assert app.db_adapter.execute_query.call_count == 2
        data_sql = app.db_adapter.execute_query.call_args_list[1][0][0]
        normalized = " ".join(data_sql.split()).lower()
        assert "left join users u on u.id = a.user_id" in normalized
        assert "u.username" in normalized

    def test_count_query_also_joins_users(self, app, client, auth_header):
        """The COUNT must mirror the JOIN so that filtering by username sees
        the same row set as the data page (otherwise pagination diverges)."""
        app.db_adapter.execute_query.side_effect = [
            [{"total": 0}],
            [],
        ]

        client.get(
            "/api/audit-trail?username=admin",
            headers=auth_header,
        )

        count_sql = app.db_adapter.execute_query.call_args_list[0][0][0]
        normalized = " ".join(count_sql.split()).lower()
        assert "left join users u on u.id = a.user_id" in normalized

    def test_username_filter_targets_users_table(
        self, app, client, auth_header,
    ):
        """Filtering by username must match against the live ``users.username``,
        not the stale ``audit_trail.username`` snapshot."""
        app.db_adapter.execute_query.side_effect = [
            [{"total": 0}],
            [],
        ]

        client.get(
            "/api/audit-trail?username=admin",
            headers=auth_header,
        )

        for call in app.db_adapter.execute_query.call_args_list:
            sql = " ".join(call[0][0].split()).lower()
            args = call[0][1]
            if "username_filter" in args:
                assert "u.username ilike" in sql, (
                    "username filter must target users.username, "
                    "not audit_trail.username"
                )
                assert args["username_filter"] == "%admin%"

    def test_anonymous_request_yields_null_username(self, app, client, auth_header):
        """Audit rows written for unauthenticated requests have user_id NULL
        — the LEFT JOIN doesn't match, so u.username comes back NULL and
        the UI falls back to ``entry.user_id ?? '-'``."""
        app.db_adapter.execute_query.side_effect = [
            [{"total": 1}],
            [{
                "id": 7,
                "timestamp": "2026-05-10T09:00:00Z",
                "user_id": None,
                "username": None,
                "method": "POST",
                "path": "/api/auth/login",
                "status_code": 401,
                "ip_address": "127.0.0.1",
                "duration_ms": 4,
                "request_id": "req-x",
                "request_body": None,
                "response_summary": None,
            }],
        ]

        resp = client.get("/api/audit-trail", headers=auth_header)

        assert resp.status_code == 200
        entries = resp.get_json()["data"]["entries"]
        assert entries[0]["user_id"] is None
        assert entries[0]["username"] is None

    def test_deleted_user_yields_null_username(self, app, client, auth_header):
        """When user_id is set but the users row has been deleted, the
        LEFT JOIN keeps the audit row but u.username is NULL — better
        than the previous "user_<uuid>" placeholder, and the UI's
        ``entry.username ?? entry.user_id`` fallback shows the UUID."""
        app.db_adapter.execute_query.side_effect = [
            [{"total": 1}],
            [{
                "id": 9,
                "timestamp": "2026-05-10T09:00:00Z",
                "user_id": "00000000-0000-0000-0000-0000000000ff",
                "username": None,  # LEFT JOIN miss for a deleted user
                "method": "GET",
                "path": "/api/assets",
                "status_code": 200,
                "ip_address": "127.0.0.1",
                "duration_ms": 8,
                "request_id": "req-y",
                "request_body": None,
                "response_summary": None,
            }],
        ]

        resp = client.get("/api/audit-trail", headers=auth_header)

        assert resp.status_code == 200
        entries = resp.get_json()["data"]["entries"]
        assert entries[0]["username"] is None
        assert entries[0]["user_id"] == "00000000-0000-0000-0000-0000000000ff"


# =============================================================================
# Filter forwarding
# =============================================================================

class TestFilterForwarding:
    """Sanity check that other filters survive the column qualification —
    every WHERE term gained an ``a.`` prefix as part of the JOIN refactor."""

    def test_method_filter_uppercased(self, app, client, auth_header):
        app.db_adapter.execute_query.side_effect = [
            [{"total": 0}],
            [],
        ]

        client.get("/api/audit-trail?method=post", headers=auth_header)

        params = app.db_adapter.execute_query.call_args_list[1][0][1]
        assert params["method"] == "POST"

    def test_status_code_and_path_filters_applied(
        self, app, client, auth_header,
    ):
        app.db_adapter.execute_query.side_effect = [
            [{"total": 0}],
            [],
        ]

        client.get(
            "/api/audit-trail?status_code=500&path=/api/assets",
            headers=auth_header,
        )

        params = app.db_adapter.execute_query.call_args_list[1][0][1]
        assert params["status_code"] == 500
        assert params["path_filter"] == "%/api/assets%"

    def test_date_range_filters_use_qualified_columns(
        self, app, client, auth_header,
    ):
        """start_date / end_date must address ``a.timestamp`` — without
        the alias the SQL throws "ambiguous column" once the JOIN is in
        place, and a future refactor that drops the alias would silently
        break date filtering."""
        app.db_adapter.execute_query.side_effect = [
            [{"total": 0}],
            [],
        ]

        client.get(
            "/api/audit-trail?start_date=2026-05-01&end_date=2026-05-10",
            headers=auth_header,
        )

        data_sql = " ".join(
            app.db_adapter.execute_query.call_args_list[1][0][0].split()
        ).lower()
        params = app.db_adapter.execute_query.call_args_list[1][0][1]

        assert "a.timestamp >= %(start_date)s" in data_sql
        assert "a.timestamp <= %(end_date)s" in data_sql
        assert params["start_date"] == "2026-05-01"
        assert params["end_date"] == "2026-05-10"


# =============================================================================
# Semantic annotation columns (issue #1150, migration 144)
# =============================================================================

class TestSemanticColumns:
    """The endpoint must surface the five semantic columns the middleware
    merges into each row (#1150) and filter on them. A MagicMock DB never
    evaluates SQL, so assert the generated SQL AND the forwarded params, not
    just a canned payload."""

    def test_data_query_selects_semantic_columns(self, app, client, auth_header):
        app.db_adapter.execute_query.side_effect = [
            [{"total": 0}],
            [],
        ]

        client.get("/api/audit-trail", headers=auth_header)

        data_sql = " ".join(
            app.db_adapter.execute_query.call_args_list[1][0][0].split()
        ).lower()
        for col in ("a.category", "a.resource_type", "a.resource_id",
                    "a.action", "a.details"):
            assert col in data_sql

    def test_response_carries_semantic_fields(self, app, client, auth_header):
        """NULL semantic fields are the normal HTTP-only-row case and must
        pass through untouched (the UI renders them as an em-dash)."""
        app.db_adapter.execute_query.side_effect = [
            [{"total": 2}],
            [
                {
                    "id": 1,
                    "timestamp": "2026-05-10T09:00:00Z",
                    "user_id": None,
                    "username": None,
                    "method": "PUT",
                    "path": "/api/integrations/s1",
                    "status_code": 200,
                    "ip_address": "127.0.0.1",
                    "duration_ms": 9,
                    "request_id": "req-a",
                    "request_body": None,
                    "response_summary": None,
                    "category": "admin",
                    "resource_type": "integration",
                    "resource_id": "s1",
                    "action": "update",
                    "details": {"field": "name"},
                },
                {
                    "id": 2,
                    "timestamp": "2026-05-10T09:01:00Z",
                    "user_id": None,
                    "username": None,
                    "method": "GET",
                    "path": "/api/assets",
                    "status_code": 200,
                    "ip_address": "127.0.0.1",
                    "duration_ms": 3,
                    "request_id": "req-b",
                    "request_body": None,
                    "response_summary": None,
                    "category": None,
                    "resource_type": None,
                    "resource_id": None,
                    "action": None,
                    "details": None,
                },
            ],
        ]

        resp = client.get("/api/audit-trail", headers=auth_header)

        assert resp.status_code == 200
        entries = resp.get_json()["data"]["entries"]
        assert entries[0]["category"] == "admin"
        assert entries[0]["resource_type"] == "integration"
        assert entries[0]["resource_id"] == "s1"
        assert entries[0]["action"] == "update"
        assert entries[0]["details"] == {"field": "name"}
        # HTTP-only row: all five stay NULL
        for field in ("category", "resource_type", "resource_id",
                      "action", "details"):
            assert entries[1][field] is None

    @pytest.mark.parametrize("query,condition,param,value", [
        ("category=admin", "a.category = %(category)s", "category", "admin"),
        ("resource_type=integration",
         "a.resource_type = %(resource_type)s", "resource_type", "integration"),
        ("resource_id=abc-123",
         "a.resource_id = %(resource_id)s", "resource_id", "abc-123"),
        ("action=delete", "a.action = %(action)s", "action", "delete"),
    ])
    def test_semantic_filter_exact_match(
        self, app, client, auth_header, query, condition, param, value,
    ):
        app.db_adapter.execute_query.side_effect = [
            [{"total": 0}],
            [],
        ]

        client.get(f"/api/audit-trail?{query}", headers=auth_header)

        # Both count and data queries carry the condition (they share
        # where_clause); the data query is the second call.
        for call in app.db_adapter.execute_query.call_args_list:
            sql = " ".join(call[0][0].split()).lower()
            assert condition.lower() in sql
            assert call[0][1][param] == value

    def test_semantic_filters_combine_with_existing(
        self, app, client, auth_header,
    ):
        app.db_adapter.execute_query.side_effect = [
            [{"total": 0}],
            [],
        ]

        client.get(
            "/api/audit-trail?method=post&category=admin&action=create",
            headers=auth_header,
        )

        data_sql = " ".join(
            app.db_adapter.execute_query.call_args_list[1][0][0].split()
        ).lower()
        params = app.db_adapter.execute_query.call_args_list[1][0][1]
        assert "a.method = %(method)s" in data_sql
        assert "a.category = %(category)s" in data_sql
        assert "a.action = %(action)s" in data_sql
        assert params["method"] == "POST"
        assert params["category"] == "admin"
        assert params["action"] == "create"

    def test_no_semantic_params_leaves_where_untouched(
        self, app, client, auth_header,
    ):
        """Without the new params the WHERE stays bare TRUE — the filters are
        strictly opt-in and unfiltered pagination is unchanged."""
        app.db_adapter.execute_query.side_effect = [
            [{"total": 0}],
            [],
        ]

        client.get("/api/audit-trail", headers=auth_header)

        data_sql = " ".join(
            app.db_adapter.execute_query.call_args_list[1][0][0].split()
        ).lower()
        assert "where true" in data_sql
        params = app.db_adapter.execute_query.call_args_list[1][0][1]
        for param in ("category", "resource_type", "resource_id", "action"):
            assert param not in params
