# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repository for the `user_mfa` table.

Multi-method (#608 follow-up): one row per enrolled MFA method, keyed by `id`.
A user can have N rows; rows with `mfa_enabled = false` are pending enrollments
awaiting verification.

Thin DB access — encryption/decryption of secrets lives in MfaService.
"""
import logging
from typing import Dict, List, Optional
from uuid import UUID

from psycopg2 import extras

logger = logging.getLogger(__name__)

# Module-level constants. Exposed here (not just on the class) so call sites
# can reach the defaults without resolving them through the class object —
# tests routinely patch the class, which would otherwise turn these into
# MagicMock attributes.
DEFAULT_LABEL = 'Authenticator app'
DEFAULT_TYPE = 'totp'


class UserMfaRepository:
    """DB access for the user_mfa table (one row per enrolled method).

    Read-modify-write operations (replace_pending_method,
    delete_method_safely) take a per-user transaction-scoped advisory lock
    so concurrent writes for the same user can't race past invariants like
    "at most one pending row" or "at least one enabled method".
    """

    DEFAULT_LABEL = DEFAULT_LABEL
    DEFAULT_TYPE = DEFAULT_TYPE

    def __init__(self, conn_manager):
        self.conn_manager = conn_manager

    # ----- helpers ----------------------------------------------------------

    @staticmethod
    def _acquire_user_lock(cursor, user_id: UUID) -> None:
        """Take a transaction-scoped advisory lock keyed on (table, user_id).

        Two-arg form gives independent int4 hashes per (namespace, user) pair,
        avoiding the int8 collision risk you'd get from a single hashtext.
        Released automatically at COMMIT/ROLLBACK.
        """
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext('user_mfa'), hashtext(%(uid)s))",
            {'uid': str(user_id)},
        )

    # ----- read paths -------------------------------------------------------

    def list_methods(self, user_id: UUID) -> List[Dict]:
        """Return every row for the user (enabled + pending), oldest-first."""
        query = """
            SELECT id, user_id, type, label, mfa_enabled, mfa_secret,
                   created_at, updated_at
            FROM user_mfa
            WHERE user_id = %(user_id)s
            ORDER BY created_at ASC
        """
        with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, _):
            cursor.execute(query, {'user_id': str(user_id)})
            return [dict(r) for r in cursor.fetchall()]

    def list_enabled_methods(self, user_id: UUID) -> List[Dict]:
        """Return only rows with mfa_enabled=true."""
        return [m for m in self.list_methods(user_id) if m.get('mfa_enabled')]

    def count_enabled_methods(self, user_id: UUID) -> int:
        """Count of enabled methods. Used by the `must keep at least one` guard."""
        query = """
            SELECT COUNT(*) AS n
            FROM user_mfa
            WHERE user_id = %(user_id)s AND mfa_enabled = true
        """
        with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, _):
            cursor.execute(query, {'user_id': str(user_id)})
            row = cursor.fetchone()
            return int(row['n']) if row else 0

    def get_method(self, method_id: UUID) -> Optional[Dict]:
        """Look up a single method by id. Returns None if missing.

        Callers that mutate must use the safely-* helpers below; they take
        the per-user lock and re-check ownership in the same transaction.
        """
        query = """
            SELECT id, user_id, type, label, mfa_enabled, mfa_secret,
                   created_at, updated_at
            FROM user_mfa
            WHERE id = %(method_id)s
        """
        with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, _):
            cursor.execute(query, {'method_id': str(method_id)})
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_pending_method(self, user_id: UUID) -> Optional[Dict]:
        """Return the user's most recent pending (mfa_enabled=false) row.

        Used by the post-login enrollment flow which has no method_id in hand.
        replace_pending_method enforces at-most-one pending row, so "most
        recent" is defensive against legacy rows from before that invariant.
        """
        query = """
            SELECT id, user_id, type, label, mfa_enabled, mfa_secret,
                   created_at, updated_at
            FROM user_mfa
            WHERE user_id = %(user_id)s AND mfa_enabled = false
            ORDER BY created_at DESC
            LIMIT 1
        """
        with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, _):
            cursor.execute(query, {'user_id': str(user_id)})
            row = cursor.fetchone()
            return dict(row) if row else None

    # ----- atomic write paths -----------------------------------------------

    def replace_pending_method(
        self,
        user_id: UUID,
        *,
        encrypted_secret: str,
        label: Optional[str] = None,
        type_: str = DEFAULT_TYPE,
    ) -> UUID:
        """Atomically clear any prior pending row for the user and insert a
        new one. Returns the new method id (UUID).

        Per-user advisory lock prevents two concurrent setup calls from
        leaving two pending rows behind: the second waits for the first to
        commit, then DELETEs the row the first inserted and INSERTs its own.
        Last write wins, deterministic, no orphans.
        """
        clean_label = (label or self.DEFAULT_LABEL).strip() or self.DEFAULT_LABEL
        with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
            self._acquire_user_lock(cursor, user_id)
            cursor.execute(
                """
                DELETE FROM user_mfa
                WHERE user_id = %(user_id)s AND mfa_enabled = false
                """,
                {'user_id': str(user_id)},
            )
            cursor.execute(
                """
                INSERT INTO user_mfa (user_id, mfa_enabled, mfa_secret, label, type)
                VALUES (%(user_id)s, false, %(secret)s, %(label)s, %(type)s)
                RETURNING id
                """,
                {
                    'user_id': str(user_id),
                    'secret': encrypted_secret,
                    'label': clean_label,
                    'type': type_,
                },
            )
            new_id = cursor.fetchone()['id']
            conn.commit()
            logger.info(f"Pending MFA method inserted for user {user_id} (id={new_id})")
            return UUID(str(new_id))

    def enable_method(self, method_id: UUID) -> None:
        """Flip mfa_enabled=true on a single method (verify step)."""
        query = """
            UPDATE user_mfa
            SET mfa_enabled = true,
                updated_at = NOW()
            WHERE id = %(method_id)s
        """
        with self.conn_manager.get_cursor() as (cursor, conn):
            cursor.execute(query, {'method_id': str(method_id)})
            conn.commit()
            logger.info(f"MFA method {method_id} enabled")

    def update_label_safely(
        self, user_id: UUID, method_id: UUID, label: str,
    ) -> bool:
        """Rename a method, but only when it belongs to the supplied user.

        Returns True if a row was updated, False if no row matched (missing
        id OR id belongs to another user). Callers should treat False as 404.
        Empty/whitespace labels fall back to the default.
        """
        clean = (label or '').strip() or self.DEFAULT_LABEL
        query = """
            UPDATE user_mfa
            SET label = %(label)s,
                updated_at = NOW()
            WHERE id = %(method_id)s AND user_id = %(user_id)s
        """
        with self.conn_manager.get_cursor() as (cursor, conn):
            cursor.execute(query, {
                'label': clean,
                'method_id': str(method_id),
                'user_id': str(user_id),
            })
            updated = cursor.rowcount
            conn.commit()
            return bool(updated)

    def delete_method_safely(
        self, user_id: UUID, method_id: UUID,
    ) -> Dict:
        """Atomically delete a method, refusing if it would drop the user
        below 1 enabled method. Pending rows have no floor — they're always
        removable.

        Returns one of:
          {'deleted': True,  'method': {...}}
          {'deleted': False, 'reason': 'method_not_found',     'method': None}
          {'deleted': False, 'reason': 'must_keep_one_mfa_method', 'method': {...}}

        Per-user advisory lock prevents two concurrent deletes from each
        passing the floor check and dropping the user to zero enabled.
        """
        with self.conn_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
            self._acquire_user_lock(cursor, user_id)

            cursor.execute(
                """
                SELECT id, user_id, type, label, mfa_enabled, mfa_secret
                FROM user_mfa
                WHERE id = %(method_id)s AND user_id = %(user_id)s
                """,
                {'method_id': str(method_id), 'user_id': str(user_id)},
            )
            target_row = cursor.fetchone()
            if not target_row:
                conn.rollback()
                return {'deleted': False, 'reason': 'method_not_found', 'method': None}
            target = dict(target_row)

            if target['mfa_enabled']:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS n FROM user_mfa
                    WHERE user_id = %(user_id)s AND mfa_enabled = true
                    """,
                    {'user_id': str(user_id)},
                )
                enabled_count = int(cursor.fetchone()['n'])
                if enabled_count <= 1:
                    conn.rollback()
                    return {
                        'deleted': False,
                        'reason': 'must_keep_one_mfa_method',
                        'method': target,
                    }

            cursor.execute(
                "DELETE FROM user_mfa WHERE id = %(method_id)s",
                {'method_id': str(method_id)},
            )
            conn.commit()
            logger.info(f"MFA method {method_id} deleted (user {user_id})")
            return {'deleted': True, 'method': target}

    def delete_all_methods(self, user_id: UUID) -> int:
        """Delete every row for the user. Used by the admin "Reset MFA"
        action and the self-service /mfa/disable endpoint.

        Returns the number of deleted rows.
        """
        query = "DELETE FROM user_mfa WHERE user_id = %(user_id)s"
        with self.conn_manager.get_cursor() as (cursor, conn):
            cursor.execute(query, {'user_id': str(user_id)})
            deleted = cursor.rowcount
            conn.commit()
            logger.info(f"All MFA methods deleted for user {user_id} (count={deleted})")
            return deleted or 0
