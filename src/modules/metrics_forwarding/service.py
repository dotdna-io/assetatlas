# SPDX-License-Identifier: AGPL-3.0-or-later
"""MetricsForwardingService (#922 P2b): persist the remote_write forwarding target,
render it into the metrics-forwarder agent's shared config file, and hot-reload the
agent. The remote_write secret is encrypted at rest and never returned in plaintext
(masked to a ``has_secret`` boolean on read).

The shared config file's PRESENCE is what tells the agent's entrypoint to use the
API-managed config instead of the Phase-2a env render: enabling writes it, disabling
removes it (reverting the agent to its env render / off on its next start). Prometheus
agent mode requires at least one remote_write, so "disabled" is represented by removing
the API config rather than rendering a remote_write-less one; the definitive on/off for
the agent PROCESS is the opt-in ``forwarding`` compose profile.
"""

import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

import requests

from src.modules.integrations.shared.encryption import decrypt, is_encrypted

logger = logging.getLogger(__name__)

AUTH_TYPES = ("none", "bearer", "basic")
# Prometheus label-name grammar: a letter/underscore then letters/digits/underscores.
# Rendering an invalid name into external_labels makes Prometheus reject the whole
# config on reload, so reject it up front (clear 400) instead.
_LABEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
CONFIG_FILENAME = "agent.yml"
DEFAULT_CONFIG_DIR = "/app/data/metrics-forwarder"
DEFAULT_RELOAD_URL = "http://metrics-forwarder:9090/-/reload"
# The UI never receives the secret, but if a client echoes a masked placeholder back
# on save, treat it (like a blank field) as "keep the stored secret".
MASKED_PLACEHOLDER = "********"


def _squote(value: Any) -> str:
    """Render a value as a YAML single-quoted scalar (embedded quotes doubled) so URLs /
    tokens / passwords / label values with special characters can't break the rendered
    config. Mirrors the metrics-forwarder entrypoint's shell ``yq()`` helper (Phase 2a).
    """
    return "'" + str(value).replace("'", "''") + "'"


class MetricsForwardingService:
    def __init__(
        self,
        repository,
        config_dir: Optional[str] = None,
        reload_url: Optional[str] = None,
        http_timeout: int = 5,
    ):
        self.repo = repository
        self.config_dir = config_dir or os.getenv(
            "METRICS_FORWARDER_CONFIG_DIR", DEFAULT_CONFIG_DIR
        )
        self.reload_url = reload_url or os.getenv(
            "METRICS_FORWARDER_RELOAD_URL", DEFAULT_RELOAD_URL
        )
        self.http_timeout = http_timeout

    @property
    def config_path(self) -> str:
        return os.path.join(self.config_dir, CONFIG_FILENAME)

    # ---- read (secret masked) ------------------------------------------
    def get_config(self) -> Dict[str, Any]:
        row = self.repo.get_config(decrypt_secret=False) or {}
        return self._public_view(row)

    @staticmethod
    def _public_view(row: Dict[str, Any]) -> Dict[str, Any]:
        updated_at = row.get("updated_at")
        return {
            "endpoint_url": row.get("endpoint_url"),
            "auth_type": row.get("auth_type") or "none",
            "username": row.get("username"),
            "external_labels": row.get("external_labels") or {},
            "enabled": bool(row.get("enabled", False)),
            # The secret is never returned — neither plaintext nor ciphertext. The UI
            # shows a "configured" state and only sends a new value to change it.
            "has_secret": bool(row.get("auth_secret")),
            "updated_at": (
                updated_at.isoformat()
                if hasattr(updated_at, "isoformat")
                else updated_at
            ),
            "updated_by": row.get("updated_by"),
        }

    # ---- write ----------------------------------------------------------
    def set_config(
        self,
        *,
        endpoint_url: Optional[str],
        auth_type: str,
        auth_secret: Optional[str] = None,
        username: Optional[str] = None,
        external_labels: Optional[Dict[str, Any]] = None,
        enabled: bool = False,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        auth_type = (auth_type or "none").lower()
        if auth_type not in AUTH_TYPES:
            raise ValueError(
                f"Invalid auth_type '{auth_type}' (expected one of {', '.join(AUTH_TYPES)})"
            )
        endpoint_url = (endpoint_url or "").strip() or None
        username = (username or "").strip() or None
        labels = self._validate_labels(external_labels or {})

        existing = self.repo.get_config(decrypt_secret=False) or {}
        resolved_secret = self._resolve_secret(auth_type, auth_secret, existing)

        # Completeness is enforced only when enabling — a disabled config may be a
        # partially-filled draft the admin returns to later.
        if enabled:
            if not endpoint_url:
                raise ValueError("endpoint_url is required when forwarding is enabled")
            if auth_type == "bearer" and not resolved_secret:
                raise ValueError("A bearer token is required for bearer auth")
            if auth_type == "basic" and (not username or not resolved_secret):
                raise ValueError("A username and password are required for basic auth")

        if auth_type != "basic":
            username = None  # username is only meaningful for basic auth

        saved = self.repo.save_config(
            endpoint_url=endpoint_url,
            auth_type=auth_type,
            auth_secret=resolved_secret,
            username=username,
            external_labels=labels,
            enabled=bool(enabled),
            updated_by=updated_by,
        )

        apply_status = self._apply(saved)
        result = self._public_view(saved)
        result["apply"] = apply_status
        return result

    @staticmethod
    def _validate_labels(labels: Dict[str, Any]) -> Dict[str, str]:
        if not isinstance(labels, dict):
            raise ValueError(
                "external_labels must be an object of string keys to values"
            )
        out: Dict[str, str] = {}
        for key, value in labels.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("external_labels keys must be non-empty strings")
            if not _LABEL_NAME_RE.match(key):
                raise ValueError(
                    f"external_labels key '{key}' is not a valid Prometheus label name "
                    "(letters, digits, underscore; not starting with a digit)"
                )
            # bool is a subclass of int — reject it explicitly (a YAML 'true' label value
            # is almost certainly a mistake) but accept str / int / float scalars.
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError(f"external_labels['{key}'] must be a string or number")
            out[key] = str(value)
        return out

    def _resolve_secret(
        self, auth_type: str, incoming: Optional[str], existing: Dict[str, Any]
    ) -> Optional[str]:
        """Write-only round-trip: a blank / masked / already-encrypted incoming secret
        means "keep the stored one"; auth_type='none' clears it."""
        if auth_type == "none":
            return None
        if not incoming or incoming == MASKED_PLACEHOLDER or is_encrypted(incoming):
            return existing.get("auth_secret")  # already-encrypted (or None)
        return incoming  # new plaintext; repo encrypts on save

    # ---- apply to the agent (best-effort) ------------------------------
    def _apply(self, saved: Dict[str, Any]) -> Dict[str, Any]:
        if saved.get("enabled"):
            write = self._write_config_file(self._render(saved))
            reload_res = (
                self._reload_agent()
                if write.get("ok")
                else {
                    "attempted": False,
                    "detail": "config file not written; skipped reload",
                }
            )
            return {
                "enabled": True,
                "config_written": write.get("ok", False),
                "config_path": self.config_path,
                "write_error": write.get("error"),
                "reload": reload_res,
            }
        # Disabled: remove the API-managed config so the agent reverts to its env render
        # (or stays down) on next start. A running agent keeps its in-memory config until
        # the `forwarding` profile is disabled or the agent restarts.
        removed = self._remove_config_file()
        return {
            "enabled": False,
            "config_removed": removed.get("ok", False),
            "config_path": self.config_path,
            "remove_error": removed.get("error"),
            "reload": {
                "attempted": False,
                "detail": "forwarding disabled; the agent stops forwarding when "
                "the 'forwarding' profile is disabled or on its next restart",
            },
        }

    def _render(self, saved: Dict[str, Any]) -> str:
        # Decrypt the secret only here, at the moment it must be written into the agent's
        # config (Prometheus needs the plaintext to authenticate). It is never returned.
        secret = saved.get("auth_secret")
        if isinstance(secret, str) and is_encrypted(secret):
            try:
                secret = decrypt(secret)
            except ValueError:
                logger.warning(
                    "metrics forwarding: could not decrypt secret for render"
                )
                secret = None
        return self.render_agent_config(
            endpoint_url=saved.get("endpoint_url") or "",
            auth_type=saved.get("auth_type") or "none",
            secret=secret,
            username=saved.get("username"),
            external_labels=saved.get("external_labels") or {},
        )

    @staticmethod
    def render_agent_config(
        *,
        endpoint_url: str,
        auth_type: str = "none",
        secret: Optional[str] = None,
        username: Optional[str] = None,
        external_labels: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Render the Prometheus-agent config (scrape api:5001 + scheduler:9091,
        remote_write with bearer/basic auth + external labels). Same shape as the
        Phase-2a env-rendered entrypoint so both paths produce an equivalent config."""
        labels = external_labels or {}
        lines: List[str] = [
            "global:",
            "  scrape_interval: 15s",
            "  external_labels:",
            # `installation` defaults to assetatlas but a user label of the same name
            # overrides it — emit each key exactly once so the rendered YAML never has a
            # duplicate `installation:` key (Prometheus rejects duplicate map keys).
            f"    installation: {_squote(labels.get('installation', 'assetatlas'))}",
        ]
        for key, value in labels.items():
            if key == "installation":
                continue
            lines.append(f"    {_squote(key)}: {_squote(value)}")
        lines += [
            "scrape_configs:",
            "  - job_name: assetatlas-api",
            "    metrics_path: /metrics",
            "    static_configs:",
            "      - targets: ['api:5001']",
            "  - job_name: assetatlas-scheduler",
            "    metrics_path: /metrics",
            "    static_configs:",
            "      - targets: ['scheduler:9091']",
            "remote_write:",
            f"  - url: {_squote(endpoint_url)}",
        ]
        if auth_type == "bearer" and secret:
            lines.append(f"    bearer_token: {_squote(secret)}")
        elif auth_type == "basic" and (username or secret):
            lines.append("    basic_auth:")
            lines.append(f"      username: {_squote(username or '')}")
            lines.append(f"      password: {_squote(secret or '')}")
        return "\n".join(lines) + "\n"

    def _write_config_file(self, text: str) -> Dict[str, Any]:
        try:
            os.makedirs(self.config_dir, exist_ok=True)
            # A UNIQUE temp per writer: concurrent saves (multi-worker/thread gunicorn)
            # must not O_TRUNC-clobber each other's in-progress write. mkstemp creates it
            # owner-only (0600 — never briefly world-readable during the write); we
            # atomically swap it in (so a concurrent agent reload never reads a
            # half-written file), then relax to 0640: owner rw + group r. The
            # metrics-forwarder agent runs as a different uid but is added to this
            # (uid-1000 assetatlas) group via the compose `group_add`, so it reads the
            # config over the shared volume via GROUP — "other" (host users / other
            # containers) still cannot.
            fd, tmp = tempfile.mkstemp(
                dir=self.config_dir, prefix=".agent-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                os.replace(tmp, self.config_path)
                os.chmod(self.config_path, 0o640)
            except OSError:
                # Don't leak the temp file if the write/swap failed.
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return {"ok": True}
        except OSError as e:
            logger.warning(
                "metrics forwarding: could not write agent config to %s: %s",
                self.config_path,
                e,
            )
            return {"ok": False, "error": str(e)}

    def _remove_config_file(self) -> Dict[str, Any]:
        try:
            os.remove(self.config_path)
            return {"ok": True}
        except FileNotFoundError:
            return {"ok": True}  # already absent — that is the desired end state
        except OSError as e:
            logger.warning(
                "metrics forwarding: could not remove agent config %s: %s",
                self.config_path,
                e,
            )
            return {"ok": False, "error": str(e)}

    def _reload_agent(self) -> Dict[str, Any]:
        try:
            resp = requests.post(self.reload_url, timeout=self.http_timeout)
            ok = 200 <= resp.status_code < 300
            return {
                "attempted": True,
                "ok": ok,
                "status_code": resp.status_code,
                "detail": (
                    "agent reloaded"
                    if ok
                    else f"agent returned HTTP {resp.status_code}"
                ),
            }
        except requests.RequestException as e:
            # Agent unreachable (profile off / not started yet). Not an error: the config
            # is persisted + written and applies when the agent next starts.
            return {
                "attempted": True,
                "ok": False,
                "error": str(e),
                "detail": "agent unreachable; config saved and applies on next agent start",
            }

    # ---- reachability test (best-effort) -------------------------------
    def test_connection(self) -> Dict[str, Any]:
        """Best-effort reachability check of the configured endpoint: a plain HTTP request
        with the configured auth header. NOT a remote_write protocol write — a
        remote_write endpoint typically rejects a bare request (400/404/405), so ANY HTTP
        response (even 4xx) counts as 'reachable'; only a connect/timeout failure is not.
        """
        cfg = self.repo.get_config(decrypt_secret=True) or {}
        url = cfg.get("endpoint_url")
        if not url:
            return {"reachable": False, "detail": "no endpoint_url configured"}
        headers: Dict[str, str] = {}
        auth = None
        auth_type = cfg.get("auth_type") or "none"
        secret = cfg.get("auth_secret")
        if auth_type == "bearer" and secret:
            headers["Authorization"] = f"Bearer {secret}"
        elif auth_type == "basic" and (cfg.get("username") or secret):
            auth = (cfg.get("username") or "", secret or "")
        try:
            resp = requests.post(
                url, headers=headers, auth=auth, data=b"", timeout=self.http_timeout
            )
            return {
                "reachable": True,
                "status_code": resp.status_code,
                "detail": f"endpoint responded with HTTP {resp.status_code}",
            }
        except requests.RequestException as e:
            return {
                "reachable": False,
                "error": str(e),
                "detail": "could not reach the endpoint",
            }
