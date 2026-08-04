# SPDX-License-Identifier: AGPL-3.0-or-later
"""ChatRepository — sessions, messages, tool calls, feedback persistence.

Uses the named-placeholder idiom (%(name)s) consistent with execute_query().
Modelled on src/modules/posture_rules/repositories/posture_repository.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.database.postgres_adapter import PostgreSQLAdapter

from .models import MessageRole, ToolCallStatus


@dataclass
class SessionRow:
    id: str
    user_id: str
    title: str
    model: str
    provider: str
    created_at: datetime
    updated_at: datetime


@dataclass
class MessageRow:
    id: str
    session_id: str
    seq: int
    role: MessageRole
    content: Optional[str]
    tool_calls: Optional[List[Dict[str, Any]]]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    error: Optional[str]
    created_at: datetime


@dataclass
class ToolCallRow:
    id: str
    message_id: str
    tool_name: str
    arguments: Dict[str, Any]
    result: Optional[str]
    status: ToolCallStatus
    duration_ms: Optional[int]
    error: Optional[str]
    created_at: datetime
    # Optional structured payload produced by the tool (e.g. a donut
    # envelope from aggregate_assets). Surfaced to the frontend for
    # inline rendering; None when the tool produced text only.
    artifact: Optional[Dict[str, Any]] = None


@dataclass
class SessionList:
    sessions: List[SessionRow]
    total: int


@dataclass
class FeedbackRow:
    rating: int
    comment: Optional[str]
    created_at: datetime


class ChatRepository:
    """CRUD for chat sessions, messages, tool calls, and feedback."""

    def __init__(self, db: PostgreSQLAdapter):
        self.db = db

    # ---------- sessions ----------
    def create_session(
        self,
        *,
        user_id: str,
        title: str,
        model: str,
        provider: str,
    ) -> SessionRow:
        sql = """
            INSERT INTO chat_sessions (user_id, title, model, provider)
            VALUES (%(user_id)s::uuid, %(title)s, %(model)s, %(provider)s)
            RETURNING id, user_id, title, model, provider, created_at, updated_at
        """
        rows = self.db.execute_query(sql, {
            "user_id": str(user_id),
            "title": title,
            "model": model,
            "provider": provider,
        })
        return _session_from_row(rows[0])

    def load_session(self, session_id: str, *, user_id: str) -> Optional[SessionRow]:
        sql = """
            SELECT id, user_id, title, model, provider, created_at, updated_at
            FROM chat_sessions
            WHERE id = %(sid)s::uuid AND user_id = %(uid)s::uuid
              AND deleted_at IS NULL
        """
        rows = self.db.execute_query(sql, {
            "sid": str(session_id),
            "uid": str(user_id),
        })
        return _session_from_row(rows[0]) if rows else None

    def list_sessions(
        self,
        *,
        user_id: str,
        page: int = 1,
        per_page: int = 20,
    ) -> SessionList:
        offset = (page - 1) * per_page
        rows = self.db.execute_query(
            """
            SELECT id, user_id, title, model, provider, created_at, updated_at,
                   COUNT(*) OVER () AS _total
            FROM chat_sessions
            WHERE user_id = %(uid)s::uuid AND deleted_at IS NULL
            ORDER BY updated_at DESC
            LIMIT %(lim)s OFFSET %(off)s
            """,
            {"uid": str(user_id), "lim": per_page, "off": offset},
        ) or []
        total = int(rows[0]["_total"]) if rows else 0
        return SessionList(
            sessions=[_session_from_row(r) for r in rows],
            total=total,
        )

    def rename_session(
        self,
        session_id: str,
        *,
        user_id: str,
        title: str,
    ) -> Optional[SessionRow]:
        sql = """
            UPDATE chat_sessions
            SET title = %(t)s
            WHERE id = %(sid)s::uuid AND user_id = %(uid)s::uuid
              AND deleted_at IS NULL
            RETURNING id, user_id, title, model, provider, created_at, updated_at
        """
        rows = self.db.execute_query(sql, {
            "sid": str(session_id),
            "uid": str(user_id),
            "t": title,
        })
        return _session_from_row(rows[0]) if rows else None

    def delete_session(self, session_id: str, *, user_id: str) -> bool:
        sql = """
            UPDATE chat_sessions
            SET deleted_at = NOW()
            WHERE id = %(sid)s::uuid AND user_id = %(uid)s::uuid
              AND deleted_at IS NULL
            RETURNING id
        """
        rows = self.db.execute_query(sql, {
            "sid": str(session_id),
            "uid": str(user_id),
        })
        return bool(rows)

    # ---------- messages ----------
    def append_message(
        self,
        session_id: str,
        *,
        role: MessageRole,
        content: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        error: Optional[str] = None,
    ) -> MessageRow:
        # One CTE round-trip: INSERT the message + bump session.updated_at
        # so it sorts first in list_sessions.
        sql = """
            WITH ins AS (
                INSERT INTO chat_messages (session_id, seq, role, content, tool_calls,
                                           input_tokens, output_tokens, error)
                VALUES (
                    %(sid)s::uuid,
                    COALESCE(
                        (SELECT MAX(seq) FROM chat_messages
                         WHERE session_id = %(sid)s::uuid),
                        0
                    ) + 1,
                    %(role)s, %(content)s, %(tc)s::jsonb,
                    %(in_t)s, %(out_t)s, %(err)s
                )
                RETURNING id, session_id, seq, role, content, tool_calls,
                          input_tokens, output_tokens, error, created_at
            ), bump AS (
                UPDATE chat_sessions SET updated_at = NOW()
                WHERE id = %(sid)s::uuid
            )
            SELECT * FROM ins
        """
        rows = self.db.execute_query(sql, {
            "sid": str(session_id),
            "role": role,
            "content": content,
            "tc": json.dumps(tool_calls) if tool_calls is not None else None,
            "in_t": input_tokens,
            "out_t": output_tokens,
            "err": error,
        })
        return _message_from_row(rows[0])

    def load_messages(self, session_id: str) -> List[MessageRow]:
        rows = self.db.execute_query(
            """
            SELECT id, session_id, seq, role, content, tool_calls,
                   input_tokens, output_tokens, error, created_at
            FROM chat_messages
            WHERE session_id = %(sid)s::uuid
            ORDER BY seq ASC
            """,
            {"sid": str(session_id)},
        ) or []
        return [_message_from_row(r) for r in rows]

    def load_tool_calls_by_message(
        self, session_id: str,
    ) -> Dict[str, List[ToolCallRow]]:
        """Return all tool-call rows for a session, grouped by message_id, in
        insertion order. Used when rebuilding conversation history to pair
        persisted assistant `tool_use` blocks with their `tool_result` bodies
        (Anthropic rejects the request otherwise)."""
        rows = self.db.execute_query(
            """
            SELECT tc.id, tc.message_id, tc.tool_name, tc.arguments, tc.result,
                   tc.status, tc.duration_ms, tc.error, tc.created_at,
                   tc.artifact
            FROM chat_tool_calls tc
            JOIN chat_messages m ON m.id = tc.message_id
            WHERE m.session_id = %(sid)s::uuid
            ORDER BY tc.message_id, tc.created_at ASC, tc.id ASC
            """,
            {"sid": str(session_id)},
        ) or []
        grouped: Dict[str, List[ToolCallRow]] = {}
        for r in rows:
            tc = _tool_call_from_row(r)
            grouped.setdefault(tc.message_id, []).append(tc)
        return grouped

    # ---------- tool calls ----------
    def record_tool_call(
        self,
        *,
        message_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        result: Optional[str],
        status: ToolCallStatus,
        duration_ms: Optional[int] = None,
        error: Optional[str] = None,
        artifact: Optional[Dict[str, Any]] = None,
    ) -> ToolCallRow:
        result_bytes = len(result.encode("utf-8")) if result else 0
        sql = """
            INSERT INTO chat_tool_calls
                (message_id, tool_name, arguments, result, result_bytes,
                 duration_ms, status, error, artifact)
            VALUES (%(mid)s::uuid, %(n)s, %(a)s::jsonb, %(r)s, %(rb)s,
                    %(dur)s, %(s)s, %(err)s, %(art)s::jsonb)
            RETURNING id, message_id, tool_name, arguments, result, status,
                      duration_ms, error, created_at, artifact
        """
        rows = self.db.execute_query(sql, {
            "mid": str(message_id),
            "n": tool_name,
            "a": json.dumps(arguments),
            "r": result,
            "rb": result_bytes,
            "dur": duration_ms,
            "s": status,
            "err": error,
            "art": json.dumps(artifact) if artifact is not None else None,
        })
        return _tool_call_from_row(rows[0])

    # ---------- feedback ----------
    def upsert_feedback(
        self,
        *,
        message_id: str,
        user_id: str,
        rating: int,
        comment: Optional[str] = None,
    ) -> None:
        sql = """
            INSERT INTO chat_feedback (message_id, user_id, rating, comment)
            VALUES (%(mid)s::uuid, %(uid)s::uuid, %(r)s, %(c)s)
            ON CONFLICT (message_id, user_id)
            DO UPDATE SET rating = EXCLUDED.rating,
                          comment = EXCLUDED.comment,
                          created_at = NOW()
        """
        self.db.execute_query(sql, {
            "mid": str(message_id),
            "uid": str(user_id),
            "r": rating,
            "c": comment,
        }, fetch=False)

    def list_feedback_for_message(self, message_id: str) -> List[FeedbackRow]:
        rows = self.db.execute_query(
            """
            SELECT rating, comment, created_at FROM chat_feedback
            WHERE message_id = %(mid)s::uuid
            """,
            {"mid": str(message_id)},
        ) or []
        return [_feedback_from_row(r) for r in rows]

    # ---------- admin aggregates ----------
    def usage_summary(self, *, days: int) -> Dict[str, Any]:
        """Aggregate chat activity over the last ``days`` days.

        Returns totals, token counts, top users, per-provider token splits,
        per-tool counts + error rates + latency, and recent thumbs-down
        feedback. All queries scope on ``created_at >= NOW() - INTERVAL``.
        """
        params = {"days": int(days)}

        # Single scan of chat_messages+chat_sessions for all five totals —
        # saves one round-trip and one chat_messages scan vs separate queries.
        totals_row = self.db.execute_query(
            """
            SELECT
                COUNT(*)                                          AS messages,
                COUNT(DISTINCT m.session_id)                      AS sessions,
                COUNT(DISTINCT s.user_id)                         AS active_users,
                COALESCE(SUM(m.input_tokens), 0)::bigint          AS tokens_input,
                COALESCE(SUM(m.output_tokens), 0)::bigint         AS tokens_output
            FROM chat_messages m
            JOIN chat_sessions s ON s.id = m.session_id
            WHERE m.created_at >= NOW() - (%(days)s || ' days')::interval
            """,
            params,
        ) or [{}]
        totals = dict(totals_row[0] or {})

        top_users = self.db.execute_query(
            """
            SELECT s.user_id, u.username, COUNT(*) AS messages
            FROM chat_messages m
            JOIN chat_sessions s ON s.id = m.session_id
            LEFT JOIN users u ON u.id = s.user_id
            WHERE m.created_at >= NOW() - (%(days)s || ' days')::interval
            GROUP BY s.user_id, u.username
            ORDER BY messages DESC
            LIMIT 10
            """,
            params,
        ) or []

        by_provider = self.db.execute_query(
            """
            SELECT s.provider, s.model,
                   COALESCE(SUM(m.input_tokens), 0)::bigint  AS input_tokens,
                   COALESCE(SUM(m.output_tokens), 0)::bigint AS output_tokens,
                   COUNT(*)                                  AS messages
            FROM chat_messages m
            JOIN chat_sessions s ON s.id = m.session_id
            WHERE m.created_at >= NOW() - (%(days)s || ' days')::interval
            GROUP BY s.provider, s.model
            ORDER BY input_tokens DESC
            """,
            params,
        ) or []

        tool_calls = self.db.execute_query(
            """
            SELECT tool_name,
                   COUNT(*)                                                 AS count,
                   COUNT(*) FILTER (WHERE status = 'error')                  AS error_count,
                   COUNT(*) FILTER (WHERE status = 'timeout')                AS timeout_count,
                   COUNT(*) FILTER (WHERE status = 'denied')                 AS denied_count,
                   ROUND(AVG(duration_ms))::int                              AS avg_duration_ms
            FROM chat_tool_calls
            WHERE created_at >= NOW() - (%(days)s || ' days')::interval
            GROUP BY tool_name
            ORDER BY count DESC
            LIMIT 25
            """,
            params,
        ) or []

        recent_downvotes = self.db.execute_query(
            """
            SELECT f.message_id, m.session_id, f.user_id,
                   u.username, f.comment, f.created_at
            FROM chat_feedback f
            JOIN chat_messages m ON m.id = f.message_id
            LEFT JOIN users u ON u.id = f.user_id
            WHERE f.rating = -1
              AND f.created_at >= NOW() - (%(days)s || ' days')::interval
            ORDER BY f.created_at DESC
            LIMIT 20
            """,
            params,
        ) or []

        return {
            "window_days": int(days),
            "totals": {
                "messages": int(totals.get("messages") or 0),
                "sessions": int(totals.get("sessions") or 0),
                "tokens_input": int(totals.get("tokens_input") or 0),
                "tokens_output": int(totals.get("tokens_output") or 0),
                "active_users": int(totals.get("active_users") or 0),
            },
            "top_users": [{
                "user_id": str(r["user_id"]),
                "username": r.get("username"),
                "messages": int(r["messages"]),
            } for r in top_users],
            "by_provider": [{
                "provider": r["provider"],
                "model": r["model"],
                "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "messages": int(r["messages"]),
            } for r in by_provider],
            "tool_calls": [{
                "tool_name": r["tool_name"],
                "count": int(r["count"]),
                "error_count": int(r["error_count"]),
                "timeout_count": int(r["timeout_count"]),
                "denied_count": int(r["denied_count"]),
                "avg_duration_ms": int(r["avg_duration_ms"] or 0),
            } for r in tool_calls],
            "recent_downvotes": [{
                "message_id": str(r["message_id"]),
                "session_id": str(r["session_id"]),
                "user_id": str(r["user_id"]),
                "username": r.get("username"),
                "comment": r.get("comment"),
                "created_at": r["created_at"].isoformat(),
            } for r in recent_downvotes],
        }

    def audit_messages(
        self,
        *,
        days: int,
        page: int,
        per_page: int,
        user_id: Optional[str] = None,
        role: Optional[MessageRole] = None,
    ) -> Dict[str, Any]:
        """Paginated chat_messages audit view with optional user/role filters."""
        where = ["m.created_at >= NOW() - (%(days)s || ' days')::interval"]
        params: Dict[str, Any] = {
            "days": int(days),
            "limit": int(per_page),
            "offset": int((page - 1) * per_page),
        }
        if user_id:
            where.append("s.user_id = %(uid)s::uuid")
            params["uid"] = str(user_id)
        if role:
            where.append("m.role = %(role)s")
            params["role"] = role
        where_sql = " AND ".join(where)

        total_row = self.db.execute_query(
            f"""
            SELECT COUNT(*) AS n
            FROM chat_messages m
            JOIN chat_sessions s ON s.id = m.session_id
            WHERE {where_sql}
            """,
            params,
        ) or [{}]
        total = int((total_row[0] or {}).get("n") or 0)

        rows = self.db.execute_query(
            f"""
            SELECT m.id AS message_id, m.session_id, s.user_id, u.username,
                   m.role, m.content, m.input_tokens, m.output_tokens,
                   m.error, m.created_at
            FROM chat_messages m
            JOIN chat_sessions s ON s.id = m.session_id
            LEFT JOIN users u ON u.id = s.user_id
            WHERE {where_sql}
            ORDER BY m.created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        ) or []

        items = [{
            "message_id": str(r["message_id"]),
            "session_id": str(r["session_id"]),
            "user_id": str(r["user_id"]),
            "username": r.get("username"),
            "role": r["role"],
            # Preview only — full content lives in the session view. 240
            # chars keeps the audit table scannable without blowing out rows.
            "content_preview": (r.get("content") or "")[:240],
            "input_tokens": r.get("input_tokens"),
            "output_tokens": r.get("output_tokens"),
            "error": r.get("error"),
            "created_at": r["created_at"].isoformat(),
        } for r in rows]

        return {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
        }


# ---------- helpers ----------
def _session_from_row(r: Dict[str, Any]) -> SessionRow:
    return SessionRow(
        id=str(r["id"]),
        user_id=str(r["user_id"]),
        title=r["title"],
        model=r["model"],
        provider=r["provider"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _message_from_row(r: Dict[str, Any]) -> MessageRow:
    return MessageRow(
        id=str(r["id"]),
        session_id=str(r["session_id"]),
        seq=r["seq"],
        role=r["role"],
        content=r.get("content"),
        tool_calls=r.get("tool_calls"),
        input_tokens=r.get("input_tokens"),
        output_tokens=r.get("output_tokens"),
        error=r.get("error"),
        created_at=r["created_at"],
    )


def _tool_call_from_row(r: Dict[str, Any]) -> ToolCallRow:
    return ToolCallRow(
        id=str(r["id"]),
        message_id=str(r["message_id"]),
        tool_name=r["tool_name"],
        arguments=r["arguments"],
        result=r.get("result"),
        status=r["status"],
        duration_ms=r.get("duration_ms"),
        error=r.get("error"),
        created_at=r["created_at"],
        # Older rows (pre-migration 077) won't have the column in the
        # SELECT list; guard with .get so tests / partial reads don't
        # trip a KeyError.
        artifact=r.get("artifact"),
    )


def _feedback_from_row(r: Dict[str, Any]) -> FeedbackRow:
    return FeedbackRow(
        rating=r["rating"],
        comment=r.get("comment"),
        created_at=r["created_at"],
    )
