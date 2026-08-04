# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Shared test fixtures for AssetAtlas.

Provides:
- blueprint_app: Session-scoped Flask app with mocked services (TestConfig)
- client: Function-scoped test client with mock resets
- auth_headers: JWT token generator for protected endpoints
- db_connection: Real DB connection for integration tests (requires DATABASE_URL)
- db_transaction: SAVEPOINT/ROLLBACK wrapper per test
"""

import os

from cryptography.fernet import Fernet

import pytest
from unittest.mock import MagicMock, patch

# Set DATABASE_URL before any factory/config import so BaseConfig.DATABASE_URL resolves
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5434/assetatlas_test")

# Test-only Fernet key for the encryption module. The runtime requires
# ASSETATLAS_ENCRYPTION_KEY in every environment (no fallback) — generate a fresh, throwaway
# key here so the harness can encrypt/decrypt
# without setting it in the developer's shell.
os.environ.setdefault(
    "ASSETATLAS_ENCRYPTION_KEY",
    Fernet.generate_key().decode(),  # fresh per-run; never committed
)


# ---------------------------------------------------------------------------
# Service attribute names that get mocked on the Flask app
# ---------------------------------------------------------------------------
_SERVICE_ATTRS = [
    "rule_mgmt_service",
    "preview_service",
    "execution_service",
    "asset_repository",
    "asset_service",
    "asset_history_service",
    "category_override_repo",
    "custom_subcategory_repo",
    "asset_label_override_repo",
    "asset_label_override_service",
    "reclassify_service",
    "tag_service",
    "merge_service",
    "dashboard_repository",
    "dashboard_service",
    "reports_repository",
    "reports_service",
    "system_preferences_repository",
    "system_preferences_service",
    "integration_service",
    "asset_generation_trigger_service",
    "scheduler_service",
    "integration_group_repository",
    "integration_group_service",
    "settings_manager",
    "pipeline_service",
    "pipeline_phase_service",
    "log_cleanup_service",
    "alerts_repository",
    "alerts_service",
    "alert_delivery_service",
    "metrics_forwarding_service",
    "anomaly_service",
    "prometheus_webhook_service",
    "enrichment_repository",
    "enrichment_service",
    "reference_data_service",
    "suggestion_service",
    "field_registry_service",
    "description_generator",
    "db_adapter",
    "connection_manager",
    "api_key_repository",
    "api_key_service",
    "monitoring_service",
    "posture_repository",
    "posture_category_read_service",
    "posture_sub_rule_mgmt_service",
    "posture_evaluation_service",
    "posture_preview_service",
    "field_resolver",
    "chat_service",
    "chat_providers_service",
    "chat_rate_limiter",
    "email_service",
    "teams_link_service",
    "saved_filters_service",
    "merge_denylist_service",
    "unified_fields_service",
    "embedding_integration_service",
    "asset_split_service",
]


def _wire_get_cursor(cm):
    """Configure ``cm.get_cursor(...)`` as a working context manager yielding
    a ``(cursor, conn)`` 2-tuple.

    Repositories open cursors via
    ``with conn_manager.get_cursor(...) as (cursor, conn):`` — notably
    ``SessionRepository.get_session_by_access_jti``, which the JWT
    ``token_in_blocklist_loader`` calls on every authed request.  A bare
    MagicMock raises on that unpack, the loader fails closed (token treated
    as revoked), and every authed request 401s (#1298).  With this wiring the
    lookup instead returns an empty dict, which the loader still treats as
    revoked via its normal "session not found" path — authed flows
    additionally need the session-lookup stub (see
    tests/unit/blueprints/conftest.py).

    Must be re-applied after any bulk ``reset_mock(return_value=True)``: the
    reset cascades into child mocks and discards this wiring.
    """
    cm.get_cursor.return_value.__enter__.return_value = (
        MagicMock(name="cursor"),
        MagicMock(name="conn"),
    )
    cm.get_cursor.return_value.__exit__.return_value = False


def _fake_init_database(app, should_log=True):
    """Stand-in for factory._init_database that installs mocks instead of
    connecting to a real database.  This lets _init_jwt (which needs
    app.connection_manager) and the before_request/teardown_request hooks
    work without a live PostgreSQL instance.
    """
    mock_cm = MagicMock(name="connection_manager")
    mock_cm.begin_scope = MagicMock()
    mock_cm.end_scope = MagicMock()
    # _init_jwt captures this mock in its SessionRepository, so the
    # blocklist loader's get_cursor unpack must work on it (#1298).
    _wire_get_cursor(mock_cm)

    app.connection_manager = mock_cm
    app.db_adapter = MagicMock(name="db_adapter")

    @app.before_request
    def init_db_scope():
        mock_cm.begin_scope()

    @app.teardown_request
    def cleanup_db_scope(exception=None):
        mock_cm.end_scope(error=exception)


@pytest.fixture(scope="session")
def blueprint_app():
    """Session-scoped Flask test app with all services replaced by MagicMocks.

    Uses TestConfig (TESTING=True, simple cache, scheduler disabled).
    Database initialisation and service creation are patched out so no real
    DB or Redis connection is needed.
    """
    # Replace _init_database with our fake that installs mocks.
    # Replace _init_services to skip real service construction.
    with patch("src.api.factory._init_database", side_effect=_fake_init_database), \
         patch("src.api.factory._init_services"):

        from src.api.factory import create_app

        app = create_app("test")

        # Ensure JWT_SECRET_KEY is present for token creation
        app.config.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

        # Inject mocked services (overwrite whatever _fake_init_database set)
        for attr in _SERVICE_ATTRS:
            setattr(app, attr, MagicMock(name=attr))

        # Re-set connection_manager convenience methods after the bulk override
        app.connection_manager.begin_scope = MagicMock()
        app.connection_manager.end_scope = MagicMock()
        # The bulk override replaced app.connection_manager wholesale, so the
        # get_cursor wiring from _fake_init_database does not carry over —
        # re-apply it here (and again per test in client(), whose bulk
        # reset_mock(return_value=True) wipes it).
        _wire_get_cursor(app.connection_manager)

        # Chat blueprint is registered unconditionally by the factory
        # since the chat-config chicken-and-egg fix; no manual registration
        # needed here. Tests that need chat_service to be None override
        # app.chat_service directly (see TestChatServiceGuard).

        # settings_manager.get() is called by request_id middleware during
        # every request.  Make it return the caller-supplied default (3rd arg)
        # so middleware code like `value.upper()` doesn't fail on a MagicMock.
        def _settings_get(_category, _key, default=None):
            return default

        app.settings_manager.get = _settings_get

        yield app


@pytest.fixture(scope="function")
def client(blueprint_app):
    """Function-scoped test client. Resets all service mocks between tests."""
    for attr in _SERVICE_ATTRS:
        mock = getattr(blueprint_app, attr, None)
        if isinstance(mock, MagicMock):
            mock.reset_mock(side_effect=True, return_value=True)

    # reset_mock(return_value=True) cascades into child mocks and wipes the
    # get_cursor context-manager wiring — re-apply it per test (#1298).
    _wire_get_cursor(blueprint_app.connection_manager)

    # Restore settings_manager.get to the default function — tests like
    # test_settings_bp.py replace it with custom callables, and reset_mock
    # cannot undo that because it's a plain attribute assignment.
    def _settings_get(_category, _key, default=None):
        return default

    blueprint_app.settings_manager.get = _settings_get

    with blueprint_app.test_client() as test_client:
        yield test_client


@pytest.fixture(scope="function")
def auth_headers(blueprint_app):
    """Return a callable that produces JWT Authorization headers.

    Usage::

        headers = auth_headers(role="admin")
        response = client.get("/api/assets", headers=headers)
    """
    from flask_jwt_extended import create_access_token

    def _make_headers(role="admin", username="testuser", user_id="test-user-id"):
        with blueprint_app.app_context():
            token = create_access_token(
                identity=user_id,
                additional_claims={
                    "role": role,
                    "username": username,
                },
            )
        return {"Authorization": f"Bearer {token}"}

    return _make_headers


# ---------------------------------------------------------------------------
# Integration test fixtures (require a live database)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def db_connection():
    """Session-scoped raw psycopg2 connection for integration tests.

    Skips the test if DATABASE_URL is not set or the database is unreachable.
    """
    import psycopg2

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set -- skipping integration test")

    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = False
    except psycopg2.OperationalError as exc:
        pytest.skip(f"Cannot connect to database: {exc}")

    yield conn

    conn.close()


@pytest.fixture(scope="function")
def db_transaction(db_connection):
    """Function-scoped SAVEPOINT wrapper.

    Wraps each test in a savepoint and rolls back after, so tests never
    persist data.
    """
    cur = db_connection.cursor()
    cur.execute("SAVEPOINT test_savepoint")

    yield db_connection

    cur.execute("ROLLBACK TO SAVEPOINT test_savepoint")
    cur.close()
