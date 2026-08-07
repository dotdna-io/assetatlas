# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the health blueprint (no auth required).

Endpoints tested:
- GET /api/health       — comprehensive health check (200 or 503)
- GET /api/health/live  — liveness probe (always 200)
- GET /api/health/ready — readiness probe (200 or 503)
- GET /api/health/logs  — log file health (200 or 503)
"""

import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.api.blueprints.health import get_application_info
from src.api.utils.caching import cache
from tests.tz_helpers import WEST_OF_UTC_TZ, local_timezone


class TestHealthLiveness:
    """GET /api/health/live -- no auth, no DB, always 200."""

    def test_liveness_returns_200(self, client):
        resp = client.get("/api/health/live")
        assert resp.status_code == 200

    def test_liveness_body_contains_status_alive(self, client):
        resp = client.get("/api/health/live")
        data = resp.get_json()
        assert data["status"] == "alive"

    def test_liveness_includes_version(self, client):
        resp = client.get("/api/health/live")
        data = resp.get_json()
        assert "version" in data["application"]

    def test_liveness_includes_uptime(self, client):
        resp = client.get("/api/health/live")
        data = resp.get_json()
        assert "uptime_seconds" in data["application"]
        assert isinstance(data["application"]["uptime_seconds"], (int, float))

    def test_liveness_includes_timestamp(self, client):
        resp = client.get("/api/health/live")
        data = resp.get_json()
        assert "timestamp" in data
        assert data["timestamp"].endswith("Z")

    def test_liveness_includes_app_name(self, client):
        resp = client.get("/api/health/live")
        data = resp.get_json()
        assert "name" in data["application"]


class TestHealthCheck:
    """GET /api/health -- comprehensive check, needs db_adapter mock."""

    @pytest.fixture(autouse=True)
    def _chromadb_stats(self, client, blueprint_app):
        """Model the ChromaDB check that 0f71656f added to the /health payload.

        check_chromadb() reads embedding_integration_service
        .chromadb_store_if_initialized.get_collection_stats() (the read-only
        accessor — the health probe must not trigger the lazy build, #1390);
        a bare MagicMock there yields MagicMock counts, which jsonify cannot
        serialize — the whole response 500s regardless of what the database
        check did. Real stats dicts are ints, so give the mock the real shape.

        Depends on `client` so the return value is set AFTER that fixture's
        per-test reset_mock(return_value=True) cascade, which would
        otherwise wipe it back to a bare MagicMock.
        """
        store = blueprint_app.embedding_integration_service \
            .chromadb_store_if_initialized
        store.get_collection_stats.return_value = {
            "fields_count": 49,
            "values_count": 120,
        }

    @staticmethod
    def _setup_db_healthy(blueprint_app):
        """Configure mocks so database health check passes."""
        blueprint_app.db_adapter.execute_query.side_effect = None
        blueprint_app.db_adapter.execute_query.return_value = [{"test": 1}]
        # get_pool_stats returns a plain dict (not a MagicMock) for JSON serialization
        blueprint_app.connection_manager.get_pool_stats.return_value = {
            "min_connections": 2,
            "max_connections": 10,
            "total_connections_estimate": 3,
        }

    def test_healthy_when_db_succeeds(self, client, blueprint_app):
        self._setup_db_healthy(blueprint_app)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "healthy"

    def test_response_has_checks_section(self, client, blueprint_app):
        self._setup_db_healthy(blueprint_app)
        resp = client.get("/api/health")
        data = resp.get_json()
        assert "checks" in data
        assert "database" in data["checks"]
        assert "cache" in data["checks"]
        assert "scheduler" in data["checks"]

    def test_response_has_application_section(self, client, blueprint_app):
        self._setup_db_healthy(blueprint_app)
        resp = client.get("/api/health")
        data = resp.get_json()
        assert "application" in data
        assert "name" in data["application"]
        assert "version" in data["application"]
        assert "uptime_seconds" in data["application"]

    def test_db_healthy_includes_pool_stats(self, client, blueprint_app):
        self._setup_db_healthy(blueprint_app)
        resp = client.get("/api/health")
        data = resp.get_json()
        db_check = data["checks"]["database"]
        assert db_check["status"] == "healthy"
        assert "pool" in db_check
        assert "usage_percent" in db_check["pool"]

    def test_unhealthy_when_db_raises(self, client, blueprint_app):
        blueprint_app.db_adapter.execute_query.side_effect = Exception("connection refused")
        resp = client.get("/api/health")
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["database"]["status"] == "unhealthy"

    def test_db_check_includes_error_message(self, client, blueprint_app):
        blueprint_app.db_adapter.execute_query.side_effect = Exception("timeout")
        resp = client.get("/api/health")
        data = resp.get_json()
        assert "timeout" in data["checks"]["database"]["message"]


class TestApplicationFeatures:
    """application.features flags surfaced by get_application_info().

    The frontend reads application.features.{chat,monitoring} (served under
    /api/health) to decide whether to surface widgets whose backend is opt-in.
    `monitoring` reflects the EFFECTIVE dashboards toggle — the runtime
    `monitoring.dashboards` setting (auto|enabled|disabled) resolved against the
    MONITORING_ENABLED env — not mere service attachment (MonitoringService is
    always attached; the proxy gates itself per request). The UI hides the
    dashboards when this is False so it doesn't fire requests the proxy 404s.

    These call get_application_info() directly rather than GET /api/health: the
    flag lives entirely in that helper, and the full health response also
    serializes the (mocked) cache/scheduler checks, which isn't what's under test.
    """

    def test_features_includes_monitoring_and_chat(self, blueprint_app):
        with blueprint_app.app_context():
            info = get_application_info()
        assert "monitoring" in info["features"]
        assert "chat" in info["features"]

    # -- features.chat: a DB fact, not this worker's object -----------------
    #
    # The api runs four gunicorn workers and each builds its own chat_service,
    # so `app.chat_service is not None` answers "did the worker that served
    # this poll happen to have built one" — not "is chat configured". After a
    # first-time provider save that was true of one worker in four, the widget
    # stayed hidden, and the user could not send the chat request that heals a
    # cold worker. Restart or luck were the only ways out.

    def _providers(self, blueprint_app, monkeypatch, *, configured):
        svc = blueprint_app.chat_providers_service
        monkeypatch.setattr(svc, "is_configured", lambda: configured)
        return svc

    def test_chat_true_on_a_worker_with_no_service_when_db_says_configured(
        self, blueprint_app, monkeypatch,
    ):
        """The regression. This is the cold worker that used to report False."""
        monkeypatch.setitem(blueprint_app.config, "CHAT_ENABLED", True)
        monkeypatch.setattr(blueprint_app, "chat_service", None, raising=False)
        self._providers(blueprint_app, monkeypatch, configured=True)
        with blueprint_app.app_context():
            info = get_application_info()
        assert info["features"]["chat"] is True, (
            "a cold worker must still report chat available when the DB says "
            "a provider is configured, or the widget never appears"
        )

    def test_chat_false_when_no_provider_configured(
        self, blueprint_app, monkeypatch,
    ):
        """Must not flip the other way: an unconfigured install shows nothing."""
        monkeypatch.setitem(blueprint_app.config, "CHAT_ENABLED", True)
        monkeypatch.setattr(blueprint_app, "chat_service", object(), raising=False)
        self._providers(blueprint_app, monkeypatch, configured=False)
        with blueprint_app.app_context():
            info = get_application_info()
        assert info["features"]["chat"] is False

    def test_chat_false_when_disabled_by_config(
        self, blueprint_app, monkeypatch,
    ):
        """CHAT_ENABLED=false is a deployment decision and wins outright."""
        monkeypatch.setitem(blueprint_app.config, "CHAT_ENABLED", False)
        self._providers(blueprint_app, monkeypatch, configured=True)
        with blueprint_app.app_context():
            info = get_application_info()
        assert info["features"]["chat"] is False

    def test_chat_falls_back_to_local_state_when_the_probe_raises(
        self, blueprint_app, monkeypatch,
    ):
        """/api/health must survive the outage it exists to report."""
        monkeypatch.setitem(blueprint_app.config, "CHAT_ENABLED", True)
        monkeypatch.setattr(blueprint_app, "chat_service", object(), raising=False)
        svc = blueprint_app.chat_providers_service

        def _boom():
            raise RuntimeError("database is down")

        monkeypatch.setattr(svc, "is_configured", _boom)
        with blueprint_app.app_context():
            info = get_application_info()
        assert info["features"]["chat"] is True  # this worker does have one

    def test_monitoring_true_when_auto_and_env_enabled(self, blueprint_app):
        # Setting unset -> 'auto' -> follows MONITORING_ENABLED, which TestConfig
        # sets True. Mirrors a monitoring-enabled deployment left on 'auto'.
        with blueprint_app.app_context():
            info = get_application_info()
        assert info["features"]["monitoring"] is True

    def test_monitoring_false_when_setting_disabled(self, blueprint_app, monkeypatch):
        # An explicit 'disabled' override wins even though MONITORING_ENABLED=True
        # and the service is attached.
        monkeypatch.setattr(
            blueprint_app.settings_manager, "get", lambda *a, **k: "disabled"
        )
        with blueprint_app.app_context():
            info = get_application_info()
        assert info["features"]["monitoring"] is False

    def test_monitoring_setting_enabled_overrides_env_off(self, blueprint_app, monkeypatch):
        # An explicit 'enabled' override turns dashboards on even when the env flag
        # is off — proving the runtime setting is the source of truth.
        monkeypatch.setitem(blueprint_app.config, "MONITORING_ENABLED", False)
        monkeypatch.setattr(
            blueprint_app.settings_manager, "get", lambda *a, **k: "enabled"
        )
        with blueprint_app.app_context():
            info = get_application_info()
        assert info["features"]["monitoring"] is True


class TestReadinessProbe:
    """GET /api/health/ready -- checks database connectivity."""

    @staticmethod
    def _setup_db_healthy(blueprint_app):
        blueprint_app.db_adapter.execute_query.side_effect = None
        blueprint_app.db_adapter.execute_query.return_value = [{"test": 1}]
        blueprint_app.connection_manager.get_pool_stats.return_value = {
            "min_connections": 2,
            "max_connections": 10,
            "total_connections_estimate": 3,
        }

    def test_ready_when_db_healthy(self, client, blueprint_app):
        self._setup_db_healthy(blueprint_app)
        resp = client.get("/api/health/ready")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ready"

    def test_not_ready_when_db_fails(self, client, blueprint_app):
        blueprint_app.db_adapter.execute_query.side_effect = Exception("db down")
        resp = client.get("/api/health/ready")
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["status"] == "not_ready"

    def test_ready_response_has_checks(self, client, blueprint_app):
        self._setup_db_healthy(blueprint_app)
        resp = client.get("/api/health/ready")
        data = resp.get_json()
        assert "database" in data["checks"]
        assert "cache" in data["checks"]

    def test_ready_response_has_application_info(self, client, blueprint_app):
        self._setup_db_healthy(blueprint_app)
        resp = client.get("/api/health/ready")
        data = resp.get_json()
        assert "name" in data["application"]
        assert "version" in data["application"]

    def test_ready_response_has_timestamp(self, client, blueprint_app):
        self._setup_db_healthy(blueprint_app)
        resp = client.get("/api/health/ready")
        data = resp.get_json()
        assert "timestamp" in data
        assert data["timestamp"].endswith("Z")


class TestLogFileHealth:
    """GET /api/health/logs -- log file system health check."""

    def test_logs_disabled_returns_200(self, client, blueprint_app):
        """When no LOG_DIRECTORIES configured, returns disabled status."""
        resp = client.get("/api/health/logs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "logs" in data
        assert "timestamp" in data

    def test_logs_response_has_timestamp(self, client, blueprint_app):
        resp = client.get("/api/health/logs")
        data = resp.get_json()
        assert data["timestamp"].endswith("Z")


class TestLogFileLastModified:
    """files.<handler>.last_modified must name the instant the file was written.

    It used to be `datetime.fromtimestamp(st_mtime).isoformat() + 'Z'` — naive
    *local* wall-clock asserted to be UTC (#1162). That is worse than an
    offset-less string: the value looks authoritative, so a consumer has no way
    to notice it is wrong by the host's UTC offset. The containers run UTC, where
    the offset is zero, so the tests below pin the timezone to prove it.
    """

    # A fixed instant so the assertion never depends on when the suite runs.
    MTIME = 1_700_000_000.5  # 2023-11-14T22:13:20.5Z

    @staticmethod
    def _configure(blueprint_app, monkeypatch, tmp_path, mtime):
        """Point the log-file health check at one real file with a known mtime."""
        log_file = tmp_path / "app.log"
        log_file.write_text("log line\n")
        os.utime(log_file, (mtime, mtime))

        # check_log_files() only reads baseFilename/maxBytes/backupCount off the handler.
        handler = SimpleNamespace(
            baseFilename=str(log_file), maxBytes=1024, backupCount=3
        )
        monkeypatch.setitem(blueprint_app.config, "LOG_DIRECTORIES", {"api": tmp_path})
        monkeypatch.setitem(blueprint_app.config, "LOG_FILE_HANDLERS", {"app": handler})
        return log_file

    def test_last_modified_round_trips_to_the_source_mtime(
            self, client, blueprint_app, monkeypatch, tmp_path):
        self._configure(blueprint_app, monkeypatch, tmp_path, self.MTIME)

        with local_timezone(WEST_OF_UTC_TZ):
            resp = client.get("/api/health/logs")

        assert resp.status_code == 200
        emitted = resp.get_json()["logs"]["files"]["app"]["last_modified"]
        parsed = datetime.fromisoformat(emitted.replace("Z", "+00:00"))
        assert parsed.timestamp() == pytest.approx(self.MTIME, abs=0.001), (
            f"{emitted!r} is off by the host's UTC offset — it was serialized "
            f"from local wall-clock and labelled UTC"
        )

    def test_last_modified_keeps_its_z_suffixed_shape(
            self, client, blueprint_app, monkeypatch, tmp_path):
        """The wire format is unchanged by the fix — only the instant it names.

        The 'Z' is now derived from an aware UTC value instead of pasted onto a
        naive one, so no consumer sees a new string shape.
        """
        self._configure(blueprint_app, monkeypatch, tmp_path, self.MTIME)

        with local_timezone(WEST_OF_UTC_TZ):
            resp = client.get("/api/health/logs")

        emitted = resp.get_json()["logs"]["files"]["app"]["last_modified"]
        assert emitted.endswith("Z")
        assert datetime.fromisoformat(emitted.replace("Z", "+00:00")).tzinfo is not None


class TestHealthCheckEmbeddingComponents:
    """Test LLM and ChromaDB health in /api/health response."""

    @staticmethod
    def _setup_db_healthy(blueprint_app):
        """Configure mocks so database health check passes."""
        blueprint_app.db_adapter.execute_query.side_effect = None
        blueprint_app.db_adapter.execute_query.return_value = [{"test": 1}]
        blueprint_app.connection_manager.get_pool_stats.return_value = {
            "min_connections": 2,
            "max_connections": 10,
            "total_connections_estimate": 3,
        }

    def test_health_includes_chromadb(self, client, blueprint_app):
        self._setup_db_healthy(blueprint_app)
        with patch('src.api.blueprints.health.check_chromadb') as mock_chroma:
            mock_chroma.return_value = {
                'status': 'available',
                'fields_count': 49,
                'values_count': 485
            }
            response = client.get('/api/health')
            data = response.get_json()
            assert 'chromadb' in data['checks']


class TestChromadbCheckObservesOnly:
    """check_chromadb() must OBSERVE the embedding stack, never construct it.

    Regression suite for #1390: the first GET /api/health against a cold
    gunicorn worker evaluated ``EmbeddingIntegrationService.chromadb_store``
    — a property whose getter calls ``_ensure_heavy_components()``,
    synchronously building a SynonymGenerator, a ChromaDBStore and two
    EmbeddingService instances (BAAI/bge-base-en-v1.5 via ONNX, under an
    exclusive cross-process ChromaDB file lock) inside the worker serving
    the probe. The installer's post-update version check probed /api/health
    with a 15s budget ~30-40s after ``compose up -d``, got no answer, and
    aborted an otherwise-successful update.

    These tests attach a REAL EmbeddingIntegrationService (built with mocked
    collaborators, so construction itself is cheap and loads no model): a
    regression back to the constructing property then runs against the real
    lazy-build machinery rather than a look-alike fake. The cold-worker test
    additionally stubs ``_ensure_heavy_components`` so a regression is a fast
    assertion failure instead of a real ONNX model load.
    """

    @staticmethod
    def _setup_db_healthy(blueprint_app):
        blueprint_app.db_adapter.execute_query.side_effect = None
        blueprint_app.db_adapter.execute_query.return_value = [{"test": 1}]
        blueprint_app.connection_manager.get_pool_stats.return_value = {
            "min_connections": 2,
            "max_connections": 10,
            "total_connections_estimate": 3,
        }

    @staticmethod
    def _attach_real_service(blueprint_app, monkeypatch):
        from src.modules.asset_generation.services.embedding_integration import (
            EmbeddingIntegrationService,
        )
        service = EmbeddingIntegrationService(
            MagicMock(name="db_adapter"),
            field_registry=MagicMock(name="field_registry"),
            description_generator=MagicMock(name="description_generator"),
        )
        monkeypatch.setattr(blueprint_app, "embedding_integration_service", service)
        return service

    def test_cold_worker_reports_initializing_and_builds_nothing(
            self, client, blueprint_app, monkeypatch):
        """The regression: a probe of an unbuilt worker must not build it."""
        self._setup_db_healthy(blueprint_app)
        service = self._attach_real_service(blueprint_app, monkeypatch)
        with patch.object(service, '_ensure_heavy_components') as ensure:
            resp = client.get("/api/health")

        # ChromaDB is non-critical: 'initializing' must not flip the aggregate
        # (the handler keys the HTTP code off the database check alone).
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "healthy"
        chromadb = data["checks"]["chromadb"]
        assert chromadb["status"] == "initializing"
        assert "message" in chromadb
        ensure.assert_not_called()
        assert service._heavy_loaded is False

    def test_built_store_reports_available_with_unchanged_body(
            self, client, blueprint_app, monkeypatch):
        """Behaviour with the stack built is byte-identical to before #1390."""
        self._setup_db_healthy(blueprint_app)
        service = self._attach_real_service(blueprint_app, monkeypatch)
        service._heavy_loaded = True
        service._chromadb_store = MagicMock(name="chromadb_store")
        service._chromadb_store.get_collection_stats.return_value = {
            "fields_count": 49,
            "values_count": 120,
        }
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.get_json()["checks"]["chromadb"] == {
            "status": "available",
            "fields_count": 49,
            "values_count": 120,
        }

    def test_attempted_but_failed_build_reports_unavailable(
            self, client, blueprint_app, monkeypatch):
        """_heavy_loaded=True with a None store means the build FAILED — a
        fault, not a cold start, so it must not read as 'initializing'."""
        self._setup_db_healthy(blueprint_app)
        service = self._attach_real_service(blueprint_app, monkeypatch)
        service._heavy_loaded = True
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.get_json()["checks"]["chromadb"] == {
            "status": "unavailable",
            "message": "ChromaDB not configured",
        }

    def test_live_store_error_still_degrades_to_unavailable(
            self, client, blueprint_app, monkeypatch):
        """The try/except stays: a stats failure on a built store is a fault."""
        self._setup_db_healthy(blueprint_app)
        service = self._attach_real_service(blueprint_app, monkeypatch)
        service._heavy_loaded = True
        service._chromadb_store = MagicMock(name="chromadb_store")
        service._chromadb_store.get_collection_stats.side_effect = RuntimeError(
            "locked index")
        resp = client.get("/api/health")
        assert resp.status_code == 200
        chromadb = resp.get_json()["checks"]["chromadb"]
        assert chromadb["status"] == "unavailable"
        assert "locked index" in chromadb["message"]

    def test_missing_service_reports_unavailable(
            self, client, blueprint_app, monkeypatch):
        """The factory attaches None when service construction failed."""
        self._setup_db_healthy(blueprint_app)
        monkeypatch.setattr(blueprint_app, "embedding_integration_service", None)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.get_json()["checks"]["chromadb"]["status"] == "unavailable"

    def test_read_only_accessors_never_trigger_the_lazy_build(
            self, blueprint_app, monkeypatch):
        """Pin the invariant on the service itself: asking must not build."""
        from src.modules.asset_generation.services.embedding_integration import (
            EmbeddingIntegrationService,
        )
        service = self._attach_real_service(blueprint_app, monkeypatch)
        with patch.object(
                EmbeddingIntegrationService, '_ensure_heavy_components') as ensure:
            assert service.is_heavy_loaded is False
            assert service.chromadb_store_if_initialized is None
        ensure.assert_not_called()


class TestHealthInFlight:
    """GET /api/health/in-flight -- queries pipeline_runs for active runs."""

    def test_in_flight_returns_false_when_no_active_runs(self, client, blueprint_app):
        blueprint_app.db_adapter.execute_query.side_effect = None
        # The endpoint runs SELECT EXISTS(...); db.execute_query returns a list of dict rows.
        blueprint_app.db_adapter.execute_query.return_value = [{"in_flight": False}]
        resp = client.get("/api/health/in-flight")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == {"in_flight": False}

    def test_in_flight_returns_true_when_run_active(self, client, blueprint_app):
        blueprint_app.db_adapter.execute_query.side_effect = None
        blueprint_app.db_adapter.execute_query.return_value = [{"in_flight": True}]
        resp = client.get("/api/health/in-flight")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == {"in_flight": True}

    def test_in_flight_returns_503_on_db_failure(self, client, blueprint_app):
        # Simulate DB error — endpoint should fail closed (503) so the installer doesn't blindly proceed.
        blueprint_app.db_adapter.execute_query.side_effect = Exception("connection refused")
        resp = client.get("/api/health/in-flight")
        assert resp.status_code == 503
        data = resp.get_json()
        assert data == {"status": "error", "message": "DB unavailable"}


class TestUpdateCheck:
    """GET/POST /api/health/update-check — gated, cached, admin-only force."""

    CACHE_KEY = "update_check:latest"

    @pytest.fixture(autouse=True)
    def _clear_update_cache(self, blueprint_app):
        # Delete ONLY our key — the JWT user cache (jwt_user:*) shares this
        # Cache singleton and must survive, or auth breaks. Never cache.clear().
        with blueprint_app.app_context():
            cache.delete(self.CACHE_KEY)
        yield
        with blueprint_app.app_context():
            cache.delete(self.CACHE_KEY)

    @staticmethod
    def _set_enabled(blueprint_app, enabled):
        """Override the mocked settings_manager.get for the update-check key."""
        def _get(category, key, default=None):
            if (category, key) == ("system", "enable_update_check"):
                return enabled
            return default
        blueprint_app.settings_manager.get = _get

    def test_get_requires_auth(self, client):
        resp = client.get("/api/health/update-check")
        assert resp.status_code == 401

    def test_disabled_returns_enabled_false_and_makes_no_fetch(
            self, client, blueprint_app, auth_headers):
        self._set_enabled(blueprint_app, False)
        with patch("src.api.blueprints.health.fetch_latest_version") as fetch:
            resp = client.get("/api/health/update-check", headers=auth_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enabled"] is False
        assert data["update_available"] is False
        assert data["latest_version"] is None
        fetch.assert_not_called()

    def test_force_does_not_bypass_disabled_gate(
            self, client, blueprint_app, auth_headers):
        # Air-gap master switch: even an admin POST (force) must make NO
        # outbound call while the setting is off. force bypasses only the
        # cache TTL, never the enable gate.
        self._set_enabled(blueprint_app, False)
        with patch("src.api.blueprints.health.fetch_latest_version") as fetch:
            resp = client.post("/api/health/update-check", headers=auth_headers())
        assert resp.status_code == 200
        assert resp.get_json()["enabled"] is False
        fetch.assert_not_called()

    def test_enabled_newer_sets_update_available(
            self, client, blueprint_app, auth_headers, monkeypatch):
        self._set_enabled(blueprint_app, True)
        monkeypatch.setitem(blueprint_app.config, "APP_VERSION", "2026.27.5")
        with patch("src.api.blueprints.health.fetch_latest_version",
                   return_value="2026.28.1"):
            resp = client.get("/api/health/update-check", headers=auth_headers())
        data = resp.get_json()
        assert data["enabled"] is True
        assert data["update_available"] is True
        assert data["latest_version"] == "2026.28.1"
        assert data["current_version"] == "2026.27.5"

    def test_enabled_same_version_no_update(
            self, client, blueprint_app, auth_headers, monkeypatch):
        self._set_enabled(blueprint_app, True)
        monkeypatch.setitem(blueprint_app.config, "APP_VERSION", "2026.28.1")
        with patch("src.api.blueprints.health.fetch_latest_version",
                   return_value="2026.28.1"):
            resp = client.get("/api/health/update-check", headers=auth_headers())
        assert resp.get_json()["update_available"] is False

    def test_fetch_uses_version_in_path_url(
            self, client, blueprint_app, auth_headers, monkeypatch):
        # The installed version rides in the URL path so the distribution
        # census (which sees request paths only) can count the version mix.
        self._set_enabled(blueprint_app, True)
        monkeypatch.setitem(blueprint_app.config, "APP_VERSION", "2026.27.5")
        with patch("src.api.blueprints.health.fetch_latest_version",
                   return_value="2026.27.5") as fetch:
            resp = client.get("/api/health/update-check", headers=auth_headers())
        assert resp.status_code == 200
        fetch.assert_called_once_with(
            "https://get.assetatlas.com/check/2026.27.5")

    def test_fetch_falls_back_to_plain_manifest_for_dev_version(
            self, client, blueprint_app, auth_headers, monkeypatch):
        # '0.0.0-dev' is not a version the Worker 302s — the URL must fall back
        # to plain /latest.json or this install's check would 404 forever.
        self._set_enabled(blueprint_app, True)
        monkeypatch.setitem(blueprint_app.config, "APP_VERSION", "0.0.0-dev")
        with patch("src.api.blueprints.health.fetch_latest_version",
                   return_value=None) as fetch:
            resp = client.get("/api/health/update-check", headers=auth_headers())
        assert resp.status_code == 200
        fetch.assert_called_once_with("https://get.assetatlas.com/latest.json")

    def test_enabled_fetch_failure_is_silent_200(
            self, client, blueprint_app, auth_headers):
        self._set_enabled(blueprint_app, True)
        with patch("src.api.blueprints.health.fetch_latest_version",
                   return_value=None):
            resp = client.get("/api/health/update-check", headers=auth_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["latest_version"] is None
        assert data["update_available"] is False

    def test_cache_shares_one_fetch_across_two_gets(
            self, client, blueprint_app, auth_headers, monkeypatch):
        self._set_enabled(blueprint_app, True)
        monkeypatch.setitem(blueprint_app.config, "APP_VERSION", "2026.27.5")
        with patch("src.api.blueprints.health.fetch_latest_version",
                   return_value="2026.28.1") as fetch:
            client.get("/api/health/update-check", headers=auth_headers())
            client.get("/api/health/update-check", headers=auth_headers())
        assert fetch.call_count == 1

    def test_force_refetches_despite_warm_cache(
            self, client, blueprint_app, auth_headers, monkeypatch):
        self._set_enabled(blueprint_app, True)
        monkeypatch.setitem(blueprint_app.config, "APP_VERSION", "2026.27.5")
        with patch("src.api.blueprints.health.fetch_latest_version",
                   return_value="2026.28.1") as fetch:
            client.get("/api/health/update-check", headers=auth_headers())  # warms cache
            resp = client.post("/api/health/update-check", headers=auth_headers())
        assert resp.status_code == 200
        assert fetch.call_count == 2

    def test_force_forbidden_for_non_admin(
            self, client, blueprint_app, auth_headers, _prime_regular_user):
        # _prime_regular_user makes user_lookup_loader return a 'user' role, so
        # require_admin() rejects with 403 (role is read from the looked-up user).
        self._set_enabled(blueprint_app, True)
        with patch("src.api.blueprints.health.fetch_latest_version") as fetch:
            resp = client.post("/api/health/update-check", headers=auth_headers())
        assert resp.status_code == 403
        fetch.assert_not_called()


class TestApplicationEnvironment:
    """application.environment must name the config ACTUALLY in force.

    /api/health is what an operator hits to answer "is this box configured the
    way I think it is?". The field read `current_app.config.get('FLASK_ENV',
    'unknown')`, but no config class defined FLASK_ENV and config.py used the env
    var only to SELECT a class — so Flask never put the key in app.config and the
    default fired in every deployment, production ones included. A field
    hard-coded to "unknown" does not merely fail to help: it invites the reading
    that the environment is genuinely unset and sends diagnosis down the wrong
    path, which is exactly what happened when this was found (#1074).
    """

    def test_environment_is_not_unknown(self, blueprint_app):
        """The regression itself: whatever config is applied, it has a name."""
        with blueprint_app.app_context():
            assert get_application_info()["environment"] != "unknown"

    def test_reports_test_for_an_explicit_create_app_override(self, blueprint_app):
        """blueprint_app is create_app('test'). This is the case where the env
        var and the applied config disagree — FLASK_ENV is unset (or anything
        else) in the developer's shell while TestConfig is what is running. The
        reported value must follow the config, not the variable."""
        with blueprint_app.app_context():
            assert get_application_info()["environment"] == "test"

    @pytest.mark.parametrize(
        "config_name,expected",
        [("production", "production"), ("development", "development"), ("test", "test")],
    )
    def test_each_config_reports_its_own_name(self, config_name, expected):
        from src.api.config import get_config

        assert get_config(config_name).FLASK_ENV == expected

    def test_unrecognised_name_reports_the_config_it_fell_back_to(self):
        """get_config() silently falls back to the default class for an unknown
        name. Reporting the requested string would then claim an environment that
        is not running — worse than 'unknown', because it looks authoritative.
        A typo'd FLASK_ENV=prodction really is running DevelopmentConfig."""
        from src.api.config import DevelopmentConfig, get_config

        assert get_config("prodction") is DevelopmentConfig
        assert get_config("prodction").FLASK_ENV == "development"

    def test_production_config_lands_in_app_config(self):
        """The deployment case from the report: the container env was correct and
        debug:false proved ProductionConfig was live, yet environment read
        'unknown'. Both must now agree.

        Exercises Flask's `app.config.from_object()` directly rather than
        create_app(): that call is the mechanism under test — it copies only
        UPPERCASE attributes, which is exactly why nothing populated this key
        before. Going through create_app() a second time in one process trips the
        pre-existing prometheus CollectorRegistry collision (#596 H7), which has
        nothing to do with what is being asserted here.
        """
        from flask import Flask

        from src.api.config import ProductionConfig

        app = Flask(__name__)
        app.config.from_object(ProductionConfig)
        assert app.config["FLASK_ENV"] == "production"
        assert app.config["DEBUG"] is False
