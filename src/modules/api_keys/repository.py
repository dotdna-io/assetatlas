# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repository for api_keys table operations."""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from src.database.postgres_adapter import PostgreSQLAdapter

logger = logging.getLogger(__name__)


class ApiKeyRepository:
    """Data access layer for API keys."""

    def __init__(self, db_adapter: PostgreSQLAdapter):
        self.db = db_adapter

    def create(
        self,
        user_id: str,
        name: str,
        key_prefix: str,
        key_hash: str,
        expires_at: datetime,
    ) -> Dict[str, Any]:
        """Insert a new API key and return the created row."""
        sql = """
            INSERT INTO api_keys (user_id, name, key_prefix, key_hash, expires_at)
            VALUES (%(user_id)s, %(name)s, %(key_prefix)s, %(key_hash)s, %(expires_at)s)
            RETURNING id, user_id, name, key_prefix,
                      expires_at, is_active, last_used_at, last_used_ip, created_at
        """
        rows = self.db.execute_query(sql, {
            'user_id': user_id,
            'name': name,
            'key_prefix': key_prefix,
            'key_hash': key_hash,
            'expires_at': expires_at,
        })
        return rows[0]

    def get_by_hash(self, key_hash: str) -> Optional[Dict[str, Any]]:
        """Look up an API key by its SHA-256 hash. Joins user for validation."""
        sql = """
            SELECT
                ak.id, ak.user_id, ak.name, ak.key_prefix, ak.key_hash,
                ak.expires_at, ak.is_active, ak.last_used_at, ak.last_used_ip,
                ak.created_at,
                u.username, u.role, u.is_active AS user_is_active
            FROM api_keys ak
            JOIN users u ON u.id = ak.user_id
            WHERE ak.key_hash = %(key_hash)s
        """
        rows = self.db.execute_query(sql, {'key_hash': key_hash})
        return rows[0] if rows else None

    def get_by_id(self, key_id: str) -> Optional[Dict[str, Any]]:
        """Get a single API key by its UUID."""
        sql = """
            SELECT
                ak.id, ak.user_id, ak.name, ak.key_prefix,
                ak.expires_at, ak.is_active, ak.last_used_at, ak.last_used_ip,
                ak.created_at,
                u.username AS created_by
            FROM api_keys ak
            JOIN users u ON u.id = ak.user_id
            WHERE ak.id = %(key_id)s::uuid
        """
        rows = self.db.execute_query(sql, {'key_id': key_id})
        return rows[0] if rows else None

    def list_all(self) -> List[Dict[str, Any]]:
        """List all API keys with creator username."""
        sql = """
            SELECT
                ak.id, ak.user_id, ak.name, ak.key_prefix,
                ak.expires_at, ak.is_active, ak.last_used_at, ak.last_used_ip,
                ak.created_at,
                u.username AS created_by
            FROM api_keys ak
            JOIN users u ON u.id = ak.user_id
            ORDER BY ak.created_at DESC
        """
        return self.db.execute_query(sql)

    def update(
        self,
        key_id: str,
        name: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update mutable fields on an API key. Returns updated row or None."""
        sets = []
        params: Dict[str, Any] = {'key_id': key_id}

        if name is not None:
            sets.append('name = %(name)s')
            params['name'] = name
        if is_active is not None:
            sets.append('is_active = %(is_active)s')
            params['is_active'] = is_active

        if not sets:
            return self.get_by_id(key_id)

        sql = f"""
            UPDATE api_keys SET {', '.join(sets)}
            WHERE id = %(key_id)s::uuid
            RETURNING id, user_id, name, key_prefix,
                      expires_at, is_active, last_used_at, last_used_ip, created_at
        """
        rows = self.db.execute_query(sql, params)
        return rows[0] if rows else None

    def delete(self, key_id: str) -> bool:
        """Delete an API key. Returns True if a row was removed."""
        sql = """
            DELETE FROM api_keys WHERE id = %(key_id)s::uuid
            RETURNING id
        """
        rows = self.db.execute_query(sql, {'key_id': key_id})
        return len(rows) > 0

    def update_last_used(self, key_id: str, ip_address: str) -> None:
        """Stamp last_used_at and last_used_ip (called on every API request)."""
        sql = """
            UPDATE api_keys
            SET last_used_at = NOW(), last_used_ip = %(ip)s::inet
            WHERE id = %(key_id)s::uuid
        """
        self.db.execute_query(sql, {
            'key_id': key_id,
            'ip': ip_address,
        }, fetch=False)
