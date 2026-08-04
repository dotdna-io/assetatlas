# SPDX-License-Identifier: AGPL-3.0-or-later
"""Data access for the single-row metrics_forwarding_config table (#922 P2b).

Raw SQL via db_adapter (%(name)s placeholders). The remote_write secret
(auth_secret: bearer token or basic-auth password) is Fernet-encrypted at rest,
mirroring AlertDeliveryRepository / IntegrationRepository. Reads do NOT decrypt
unless explicitly asked; the service masks the secret before it reaches the API.
"""

import json
import logging
from typing import Any, Dict, Optional

from src.database.postgres_adapter import _clean_nan
from src.modules.integrations.shared.encryption import decrypt, encrypt, is_encrypted

logger = logging.getLogger(__name__)

# Single-row table: the config always lives at id = 1 (enforced by a CHECK
# constraint + PK in migration 130).
_ROW_ID = 1


class MetricsForwardingRepository:
    def __init__(self, db_adapter):
        self.db = db_adapter

    def get_config(self, decrypt_secret: bool = False) -> Optional[Dict[str, Any]]:
        rows = self.db.execute_query(
            "SELECT id, endpoint_url, auth_type, auth_secret, username, "
            "external_labels, enabled, updated_at, updated_by "
            "FROM metrics_forwarding_config WHERE id = %(id)s",
            {"id": _ROW_ID},
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["external_labels"] = self._coerce_json(row.get("external_labels"))
        if decrypt_secret:
            secret = row.get("auth_secret")
            if isinstance(secret, str) and is_encrypted(secret):
                try:
                    row["auth_secret"] = decrypt(secret)
                except ValueError:
                    logger.warning("could not decrypt metrics_forwarding auth_secret")
        return row

    def save_config(
        self,
        *,
        endpoint_url: Optional[str],
        auth_type: str,
        auth_secret: Optional[str],
        username: Optional[str],
        external_labels: Dict[str, Any],
        enabled: bool,
        updated_by: Optional[str]
    ) -> Dict[str, Any]:
        # auth_secret may arrive as new plaintext, an already-encrypted value to keep,
        # or None. encrypt() is a no-op on already-encrypted / empty values, so it is
        # safe to call unconditionally — the secret is never stored in plaintext.
        stored_secret = encrypt(auth_secret) if auth_secret else None
        rows = self.db.execute_query(
            """
            INSERT INTO metrics_forwarding_config
                (id, endpoint_url, auth_type, auth_secret, username,
                 external_labels, enabled, updated_at, updated_by)
            VALUES
                (%(id)s, %(endpoint_url)s, %(auth_type)s, %(auth_secret)s, %(username)s,
                 %(external_labels)s::jsonb, %(enabled)s, now(), %(updated_by)s)
            ON CONFLICT (id) DO UPDATE SET
                endpoint_url = EXCLUDED.endpoint_url,
                auth_type = EXCLUDED.auth_type,
                auth_secret = EXCLUDED.auth_secret,
                username = EXCLUDED.username,
                external_labels = EXCLUDED.external_labels,
                enabled = EXCLUDED.enabled,
                updated_at = now(),
                updated_by = EXCLUDED.updated_by
            RETURNING id, endpoint_url, auth_type, auth_secret, username,
                      external_labels, enabled, updated_at, updated_by
            """,
            {
                "id": _ROW_ID,
                "endpoint_url": endpoint_url,
                "auth_type": auth_type,
                "auth_secret": stored_secret,
                "username": username,
                "external_labels": json.dumps(_clean_nan(external_labels or {})),
                "enabled": enabled,
                "updated_by": updated_by,
            },
        )
        row = dict(rows[0])
        row["external_labels"] = self._coerce_json(row.get("external_labels"))
        return row

    @staticmethod
    def _coerce_json(value):
        """psycopg2 may hand back jsonb as a str depending on adapter config."""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return {}
        return value or {}
