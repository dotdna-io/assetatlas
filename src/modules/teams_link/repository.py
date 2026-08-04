# SPDX-License-Identifier: AGPL-3.0-or-later
"""Direct-SQL repository for Teams bridge tables.

Follows the AssetAtlas convention of raw SQL via db_adapter.execute_query()
with %(name)s named placeholders. Returns Pydantic models — never raw dicts —
to give callers a typed surface.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

import psycopg2

from src.modules.teams_link.models import (
    TeamsAccountLink, TeamsConversationSession,
)

logger = logging.getLogger(__name__)


class TeamsLinkRepository:
    def __init__(self, db_adapter):
        self.db = db_adapter

    # ------------------------- teams_account_links -------------------------

    def upsert_link(
        self,
        *,
        aad_object_id: str,
        tenant_id: str,
        user_id: UUID,
        linked_via_ip: Optional[str] = None,
    ) -> None:
        """Insert or replace the link row for (aad_object_id, tenant_id).
        Re-linking from the same Teams identity overwrites the prior link.
        """
        self.db.execute_query(
            """
            INSERT INTO teams_account_links
                (aad_object_id, tenant_id, user_id, linked_via_ip)
            VALUES
                (%(aad_object_id)s, %(tenant_id)s, %(user_id)s, %(linked_via_ip)s)
            ON CONFLICT (aad_object_id, tenant_id) DO UPDATE
                SET user_id = EXCLUDED.user_id,
                    linked_at = now(),
                    linked_via_ip = EXCLUDED.linked_via_ip
            """,
            {
                "aad_object_id": aad_object_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "linked_via_ip": linked_via_ip,
            },
            fetch=False,
        )

    def find_link(
        self, aad_object_id: str, tenant_id: str,
    ) -> Optional[TeamsAccountLink]:
        sql = """
        SELECT id, aad_object_id, tenant_id, user_id,
               linked_at, linked_via_ip
        FROM teams_account_links
        WHERE aad_object_id = %(aad_object_id)s
          AND tenant_id     = %(tenant_id)s
        """
        rows = self.db.execute_query(sql, {
            "aad_object_id": aad_object_id, "tenant_id": tenant_id,
        })
        if not rows:
            return None
        return TeamsAccountLink.model_validate(rows[0])

    # -------------------- teams_conversation_sessions ---------------------

    def find_conversation_session(
        self, conversation_id: str,
    ) -> Optional[TeamsConversationSession]:
        sql = """
        SELECT id, conversation_id, user_id, session_id,
               created_at, last_active_at
        FROM teams_conversation_sessions
        WHERE conversation_id = %(conversation_id)s
        """
        rows = self.db.execute_query(sql, {"conversation_id": conversation_id})
        if not rows:
            return None
        return TeamsConversationSession.model_validate(rows[0])

    def create_conversation_session(
        self, *, conversation_id: str, user_id: UUID, session_id: UUID,
    ) -> TeamsConversationSession:
        sql = """
        INSERT INTO teams_conversation_sessions
            (conversation_id, user_id, session_id)
        VALUES
            (%(conversation_id)s, %(user_id)s, %(session_id)s)
        ON CONFLICT (conversation_id) DO UPDATE
            SET session_id = EXCLUDED.session_id,
                last_active_at = now()
        RETURNING id, conversation_id, user_id, session_id,
                  created_at, last_active_at
        """
        rows = self.db.execute_query(sql, {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "session_id": session_id,
        })
        return TeamsConversationSession.model_validate(rows[0])

    def touch_conversation_session(self, conversation_id: str) -> None:
        self.db.execute_query(
            "UPDATE teams_conversation_sessions "
            "SET last_active_at = now() "
            "WHERE conversation_id = %(conversation_id)s",
            {"conversation_id": conversation_id},
            fetch=False,
        )

    def delete_conversation_session(self, conversation_id: str) -> None:
        self.db.execute_query(
            "DELETE FROM teams_conversation_sessions "
            "WHERE conversation_id = %(conversation_id)s",
            {"conversation_id": conversation_id},
            fetch=False,
        )


class UsedJtiStore:
    """Tracks consumed JTIs in `teams_link_used_jtis` so token replay is
    impossible across process restarts.

    `mark_used` returns False ONLY on a unique-violation race-loss; any
    other database error propagates so a real outage isn't silently
    misclassified as "token already used".
    """

    def __init__(self, db_adapter):
        self.db = db_adapter

    def is_used(self, jti: str) -> bool:
        rows = self.db.execute_query(
            "SELECT 1 FROM teams_link_used_jtis "
            "WHERE jti = %(jti)s LIMIT 1",
            {"jti": jti},
        )
        return bool(rows)

    def mark_used(self, jti: str) -> bool:
        """Returns True if we won the race, False if another caller
        already marked this JTI used. Other DB errors propagate."""
        try:
            self.db.execute_query(
                "INSERT INTO teams_link_used_jtis (jti) "
                "VALUES (%(jti)s)",
                {"jti": jti},
                fetch=False,
            )
            return True
        except psycopg2.errors.UniqueViolation:
            logger.info("JTI race-loss: %s", jti)
            return False
        except psycopg2.IntegrityError as exc:
            # Some db_adapter configurations may surface unique violations
            # as the parent IntegrityError class without the subclass.
            if "teams_link_used_jtis_pkey" in str(exc):
                logger.info("JTI race-loss: %s", jti)
                return False
            raise
