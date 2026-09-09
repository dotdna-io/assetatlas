# SPDX-License-Identifier: AGPL-3.0-or-later
"""Service layer for API key lifecycle management."""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

from src.modules.api_keys.repository import ApiKeyRepository

logger = logging.getLogger(__name__)

# Key format: aa_ prefix + 40 hex chars = 43 chars total
KEY_PREFIX = 'aa_'
KEY_RANDOM_BYTES = 20  # 20 bytes = 40 hex chars

# Minimum seconds between last_used_at stamps per key. Drives both the
# record_usage() gate below and the repository's UPDATE predicate, so the
# hot path costs zero SQL while the stamp stays accurate to ~this long.
LAST_USED_STAMP_INTERVAL_S = 60


class ApiKeyService:
    """Manages API key creation, validation, and lifecycle."""

    def __init__(self, repository: ApiKeyRepository):
        self.repo = repository

    def create_key(
        self,
        user_id: str,
        name: str,
        expires_at: datetime,
    ) -> Dict[str, Any]:
        """
        Generate a new API key. Returns the full key (shown once) plus metadata.

        The plaintext key is NOT stored — only its SHA-256 hash.
        """
        raw = secrets.token_hex(KEY_RANDOM_BYTES)
        plaintext_key = f"{KEY_PREFIX}{raw}"
        key_hash = hashlib.sha256(plaintext_key.encode()).hexdigest()
        key_prefix = plaintext_key[:8]

        row = self.repo.create(
            user_id=user_id,
            name=name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            expires_at=expires_at,
        )

        logger.info("API key created: id=%s user=%s name=%s", row['id'], user_id, name)

        return {
            'id': str(row['id']),
            'name': row['name'],
            'key': plaintext_key,
            'key_prefix': key_prefix,
            'expires_at': row['expires_at'],
            'created_at': row['created_at'],
        }

    def validate_key(self, plaintext_key: str) -> Optional[Dict[str, Any]]:
        """
        Validate an API key. Returns key+user metadata if valid, None otherwise.

        Checks: key exists, is_active, not expired, owning user is active.
        """
        key_hash = hashlib.sha256(plaintext_key.encode()).hexdigest()
        row = self.repo.get_by_hash(key_hash)

        if row is None:
            return None

        if not row.get('is_active'):
            logger.debug("API key %s is revoked", row['id'])
            return None

        now = datetime.now(timezone.utc)
        expires = row.get('expires_at')
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires and expires <= now:
            logger.debug("API key %s is expired", row['id'])
            return None

        if not row.get('user_is_active'):
            logger.debug("API key %s owner is inactive", row['id'])
            return None

        return row

    def record_usage(
        self,
        key_id: str,
        ip_address: str,
        last_used_at: Optional[datetime] = None,
    ) -> None:
        """Stamp last_used_at and last_used_ip after a successful request.

        Throttled to at most one write per key per
        ``LAST_USED_STAMP_INTERVAL_S``: ``last_used_at`` is the value the
        caller's ``validate_key()`` lookup already returned, and when it is
        fresher than the interval this returns without touching the
        repository (zero SQL on the hot path). ``None`` (never stamped)
        always stamps; a naive datetime is read as UTC, matching
        ``validate_key``. The repository repeats the interval as a WHERE
        predicate so concurrent workers that all read a stale stamp don't
        all write. The stamp is therefore accurate to ~60 s, and
        ``last_used_ip`` is the address of a request within that window.
        """
        if last_used_at is not None:
            if last_used_at.tzinfo is None:
                last_used_at = last_used_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - last_used_at
            if age < timedelta(seconds=LAST_USED_STAMP_INTERVAL_S):
                return
        self.repo.update_last_used(
            key_id, ip_address, stale_after_s=LAST_USED_STAMP_INTERVAL_S
        )

    def list_keys(self) -> List[Dict[str, Any]]:
        """List all API keys."""
        return self.repo.list_all()

    def get_key(self, key_id: str) -> Optional[Dict[str, Any]]:
        """Get a single API key by ID."""
        return self.repo.get_by_id(key_id)

    def update_key(
        self,
        key_id: str,
        name: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update mutable fields on an API key."""
        return self.repo.update(key_id, name=name, is_active=is_active)

    def delete_key(self, key_id: str) -> bool:
        """Permanently delete a key."""
        return self.repo.delete(key_id)
