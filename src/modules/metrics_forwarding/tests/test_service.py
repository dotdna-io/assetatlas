# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.modules.integrations.shared.encryption import decrypt, is_encrypted
from src.modules.metrics_forwarding.repository import MetricsForwardingRepository
from src.modules.metrics_forwarding.service import MetricsForwardingService

POST = "src.modules.metrics_forwarding.service.requests.post"


class FakeDb:
    """In-memory stand-in for db_adapter that faithfully simulates the single-row
    UPSERT + SELECT the repository issues, so service tests exercise the REAL encryption
    + masking path end to end (not a hand-mocked repo)."""

    def __init__(self):
        self.row = None

    def execute_query(self, query, params=None, fetch=True, commit=True):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO metrics_forwarding_config"):
            self.row = {
                "id": params["id"],
                "endpoint_url": params["endpoint_url"],
                "auth_type": params["auth_type"],
                "auth_secret": params["auth_secret"],
                "username": params["username"],
                "external_labels": params[
                    "external_labels"
                ],  # JSON string, as inserted
                "enabled": params["enabled"],
                "updated_at": datetime(2026, 7, 11, tzinfo=timezone.utc),
                "updated_by": params["updated_by"],
            }
            return [dict(self.row)]
        if q.startswith("SELECT"):
            return [dict(self.row)] if self.row else []
        return []


def _service(tmp_path, reload_url="http://metrics-forwarder:9090/-/reload"):
    repo = MetricsForwardingRepository(FakeDb())
    return MetricsForwardingService(
        repo, config_dir=str(tmp_path), reload_url=reload_url
    )


def test_get_config_defaults_when_empty(tmp_path):
    assert _service(tmp_path).get_config() == {
        "endpoint_url": None,
        "auth_type": "none",
        "username": None,
        "external_labels": {},
        "enabled": False,
        "has_secret": False,
        "updated_at": None,
        "updated_by": None,
    }


@patch(POST)
def test_set_enabled_encrypts_masks_writes_and_reloads(mock_post, tmp_path):
    mock_post.return_value = MagicMock(status_code=200)
    svc = _service(tmp_path)
    out = svc.set_config(
        endpoint_url="https://mimir.example/api/v1/push",
        auth_type="bearer",
        auth_secret="s3cr3t-token",
        external_labels={"env": "prod"},
        enabled=True,
        updated_by="admin",
    )
    # Masked on return — never the plaintext or ciphertext.
    assert out["has_secret"] is True
    assert "auth_secret" not in out
    assert "s3cr3t-token" not in str(out)
    assert out["enabled"] is True and out["endpoint_url"].endswith("/push")
    assert out["updated_at"] == "2026-07-11T00:00:00+00:00"  # datetime -> isoformat
    # Encrypted at rest — the stored secret is ENC:-prefixed and decrypts back.
    stored = svc.repo.get_config(decrypt_secret=False)["auth_secret"]
    assert is_encrypted(stored) and decrypt(stored) == "s3cr3t-token"
    # Rendered to the shared file with the plaintext token + endpoint + label.
    written = (tmp_path / "agent.yml").read_text()
    assert "remote_write:" in written
    assert "  - url: 'https://mimir.example/api/v1/push'" in written
    assert "bearer_token: 's3cr3t-token'" in written
    assert "    'env': 'prod'" in written
    # Reload attempted against the configured agent URL and succeeded.
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "http://metrics-forwarder:9090/-/reload"
    assert out["apply"]["reload"]["ok"] is True


@patch(POST, side_effect=requests.ConnectionError("no route to host"))
def test_set_enabled_graceful_when_agent_unreachable(mock_post, tmp_path):
    svc = _service(tmp_path)
    out = svc.set_config(
        endpoint_url="https://x/push",
        auth_type="none",
        enabled=True,
        updated_by="admin",
    )
    # No raise: config persisted + file written; reload flagged not-ok with a clear note.
    assert out["enabled"] is True
    assert (tmp_path / "agent.yml").exists()
    assert out["apply"]["config_written"] is True
    assert out["apply"]["reload"]["ok"] is False
    assert "next agent start" in out["apply"]["reload"]["detail"]


def test_set_disabled_removes_config_and_skips_reload(tmp_path):
    svc = _service(tmp_path)
    (tmp_path / "agent.yml").write_text("stale: config\n")
    with patch(POST) as mock_post:
        out = svc.set_config(
            endpoint_url="https://x/push",
            auth_type="none",
            enabled=False,
            updated_by="admin",
        )
        mock_post.assert_not_called()  # disabling never reloads a running agent
    assert out["enabled"] is False
    assert not (
        tmp_path / "agent.yml"
    ).exists()  # reverts the agent to env/off on restart
    assert out["apply"]["reload"]["attempted"] is False


def test_written_config_is_not_world_readable(tmp_path):
    import os
    import stat

    svc = _service(tmp_path)
    with patch(POST, return_value=MagicMock(status_code=200)):
        svc.set_config(
            endpoint_url="https://x/push",
            auth_type="bearer",
            auth_secret="tok",
            enabled=True,
            updated_by="a",
        )
    mode = stat.S_IMODE(os.stat(tmp_path / "agent.yml").st_mode)
    # The file holds the plaintext remote_write secret: owner rw + group r only,
    # never world-readable/writable (the agent reads it via its group_add group).
    assert mode == 0o640
    assert mode & 0o007 == 0


def test_write_only_secret_kept_when_blank(tmp_path):
    svc = _service(tmp_path)
    with patch(POST, return_value=MagicMock(status_code=200)):
        svc.set_config(
            endpoint_url="https://x/push",
            auth_type="bearer",
            auth_secret="first-token",
            enabled=True,
            updated_by="a",
        )
        # Re-save with a blank secret -> keep the stored one (write-only round-trip).
        svc.set_config(
            endpoint_url="https://x/push",
            auth_type="bearer",
            auth_secret="",
            enabled=True,
            updated_by="a",
        )
    assert svc.repo.get_config(decrypt_secret=True)["auth_secret"] == "first-token"


def test_switching_to_none_clears_secret(tmp_path):
    svc = _service(tmp_path)
    with patch(POST, return_value=MagicMock(status_code=200)):
        svc.set_config(
            endpoint_url="https://x/push",
            auth_type="bearer",
            auth_secret="tok",
            enabled=True,
            updated_by="a",
        )
        out = svc.set_config(
            endpoint_url="https://x/push",
            auth_type="none",
            enabled=True,
            updated_by="a",
        )
    assert out["has_secret"] is False
    assert svc.repo.get_config(decrypt_secret=True)["auth_secret"] is None


def test_set_config_rejects_unknown_auth_type(tmp_path):
    with pytest.raises(ValueError):
        _service(tmp_path).set_config(
            endpoint_url="https://x", auth_type="mtls", enabled=False
        )


def test_enable_requires_endpoint(tmp_path):
    with pytest.raises(ValueError):
        _service(tmp_path).set_config(endpoint_url="", auth_type="none", enabled=True)


def test_enable_basic_requires_username_and_password(tmp_path):
    with pytest.raises(ValueError):
        _service(tmp_path).set_config(
            endpoint_url="https://x/push",
            auth_type="basic",
            username="",
            auth_secret="",
            enabled=True,
        )


def test_disabled_config_allows_partial_draft(tmp_path):
    # A disabled config may be a partial draft — no completeness enforcement.
    out = _service(tmp_path).set_config(
        endpoint_url="", auth_type="bearer", auth_secret="", enabled=False
    )
    assert out["enabled"] is False


# ---- render (static) ------------------------------------------------------
def test_render_bearer_shape():
    out = MetricsForwardingService.render_agent_config(
        endpoint_url="https://x/push", auth_type="bearer", secret="tok"
    )
    assert "  - job_name: assetatlas-api" in out
    assert "      - targets: ['api:5001']" in out
    assert "      - targets: ['scheduler:9091']" in out
    assert "  - url: 'https://x/push'" in out
    assert "    bearer_token: 'tok'" in out


def test_render_basic_and_quote_escaping():
    out = MetricsForwardingService.render_agent_config(
        endpoint_url="https://x/push",
        auth_type="basic",
        username="u",
        secret="p'wd",
        external_labels={"team": "a'b"},
    )
    assert "    basic_auth:" in out
    assert "      username: 'u'" in out
    assert "      password: 'p''wd'" in out  # embedded single quote doubled
    assert "    'team': 'a''b'" in out


def test_render_none_auth_has_no_auth_block():
    out = MetricsForwardingService.render_agent_config(
        endpoint_url="https://x/push", auth_type="none"
    )
    assert "bearer_token" not in out and "basic_auth" not in out
    assert "remote_write:" in out  # agent mode still needs a remote_write target


def test_render_default_installation_label():
    out = MetricsForwardingService.render_agent_config(endpoint_url="https://x/push")
    assert "installation: 'assetatlas'" in out
    assert out.count("installation:") == 1


def test_render_user_installation_label_overrides_without_duplicate():
    # A user label named 'installation' must OVERRIDE the default and be emitted once —
    # a duplicate map key would make Prometheus reject the whole config on reload.
    out = MetricsForwardingService.render_agent_config(
        endpoint_url="https://x/push", external_labels={"installation": "hs-prod"}
    )
    assert out.count("installation:") == 1
    assert "installation: 'hs-prod'" in out
    assert "installation: 'assetatlas'" not in out


def test_invalid_prometheus_label_name_rejected(tmp_path):
    # A hyphenated name is invalid in Prometheus; reject it at save time (clear 400)
    # rather than rendering a config the agent will refuse to load.
    with pytest.raises(ValueError):
        _service(tmp_path).set_config(
            endpoint_url="https://x/push",
            auth_type="none",
            enabled=False,
            external_labels={"data-center": "dc1"},
        )


# ---- test_connection ------------------------------------------------------
@patch(POST)
def test_test_connection_reachable_on_any_http_response(mock_post, tmp_path):
    mock_post.return_value = MagicMock(
        status_code=405
    )  # remote_write rejects a bare POST
    svc = _service(tmp_path)
    with patch.object(
        svc.repo,
        "get_config",
        return_value={"endpoint_url": "https://x/push", "auth_type": "none"},
    ):
        res = svc.test_connection()
    assert res["reachable"] is True and res["status_code"] == 405


@patch(POST, side_effect=requests.Timeout("slow"))
def test_test_connection_unreachable_on_timeout(mock_post, tmp_path):
    svc = _service(tmp_path)
    with patch.object(
        svc.repo,
        "get_config",
        return_value={"endpoint_url": "https://x/push", "auth_type": "none"},
    ):
        res = svc.test_connection()
    assert res["reachable"] is False


def test_test_connection_no_endpoint(tmp_path):
    res = _service(tmp_path).test_connection()
    assert res["reachable"] is False and "no endpoint" in res["detail"]


@patch(POST)
def test_test_connection_sends_bearer_header(mock_post, tmp_path):
    mock_post.return_value = MagicMock(status_code=200)
    svc = _service(tmp_path)
    with patch.object(
        svc.repo,
        "get_config",
        return_value={
            "endpoint_url": "https://x/push",
            "auth_type": "bearer",
            "auth_secret": "plain-tok",
        },
    ):
        svc.test_connection()
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer plain-tok"
