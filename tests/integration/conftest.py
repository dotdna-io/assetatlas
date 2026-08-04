# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Shared fixtures for integration tests.

Provides a session-scoped Flask app backed by the live database,
and an admin token obtained via the login endpoint (so the JWT
blocklist check against the sessions table passes).
"""

import os

import pytest


# ---------------------------------------------------------------------------
# Live app (session-scoped to avoid Prometheus metric collision)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def live_app():
    """Real Flask app connected to the live database.

    Uses TestConfig (scheduler disabled, simple cache, TAG_RULES_AUTO_EXECUTE=False).
    SESSION-scoped so Prometheus metrics are registered only once per test run.

    DATABASE_URL is loaded from .env at repo root if not already pointing at
    the live server — conftest.py at tests/ sets a localhost default with
    setdefault(), so we override it here before create_app().
    """
    from dotenv import dotenv_values

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    env_file = os.path.join(repo_root, ".env")
    if os.path.exists(env_file):
        dot_env = dotenv_values(env_file)
        db_url = dot_env.get("DATABASE_URL")
        if db_url:
            os.environ["DATABASE_URL"] = db_url

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url or "localhost" in db_url:
        pytest.skip(
            "Live DATABASE_URL not available — skipping posture integration tests. "
            f"DATABASE_URL={db_url!r}"
        )

    # Bootstrap test-only secrets the integration suite expects but the
    # live .env may not carry. setdefault() preserves any operator-set
    # value in CI.
    os.environ.setdefault(
        "TEAMS_LINK_SECRET_KEY",
        "test-teams-link-secret-32-chars-long-padding",
    )

    from src.api.factory import create_app

    app = create_app("test")
    return app


# ---------------------------------------------------------------------------
# Admin token obtained via the real login endpoint (passes blocklist check)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def live_admin_headers(live_app):
    """JWT Authorization header backed by a real DB session.

    Logs in as the 'admin' user so the JWT blocklist/session check in
    check_if_token_revoked() finds an active session row.

    ADMIN_PASSWORD env var overrides the default ('admin123').
    """
    password = os.environ.get("ADMIN_PASSWORD", "admin123")

    with live_app.test_client() as c:
        resp = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": password},
        )
        assert resp.status_code == 200, (
            f"Login failed (status {resp.status_code}): {resp.get_data(as_text=True)}"
        )
        body = resp.get_json()
        # Login returns {access_token, refresh_token, ...} directly (no 'data' wrapper)
        token = body.get("access_token") or body["data"]["access_token"]

    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Live test client / db_adapter (session-scoped, shared across integration tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def live_client(live_app):
    """Test client backed by the live Flask app. Stateless, safe to share."""
    with live_app.test_client() as c:
        yield c


@pytest.fixture(scope="session")
def live_db(live_app):
    """Access the db_adapter wired into the live app."""
    return live_app.db_adapter


# ---------------------------------------------------------------------------
# Grace-period pipeline fixtures (function-scoped; skip without DATABASE_URL)
# ---------------------------------------------------------------------------

@pytest.fixture
def grace_db():
    """Function-scoped db_adapter for grace-period tests.

    Skips cleanly when DATABASE_URL is absent.  Refuses to run against the
    shared dev DB (192.0.2.10) because the pipeline truncates assets.
    localhost / any other disposable DB is allowed.
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        pytest.skip("grace integration test needs a disposable DB via DATABASE_URL")
    if "192.0.2.10" in db_url:
        pytest.skip(
            "refusing to run the destructive grace pipeline against the shared dev DB"
        )
    from src.database.postgres_adapter import PostgreSQLAdapter
    return PostgreSQLAdapter()


# grace_orchestrator / sample_input_with_host were removed: the grace integration
# tests now drive the absence path directly (seed -> stage -> persist -> track)
# rather than the full pipeline, so only grace_db is needed.
