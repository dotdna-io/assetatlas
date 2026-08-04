# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Authentication audit logging service.

Logs all authentication events for security monitoring and compliance.

Write model (#1150, #1294):

- ``audit_trail`` is the system of record and the sole write path. Every
  event logged here annotates the in-flight request's ``audit_trail`` row
  via ``AuditLogger.log_auth_event`` → ``log_audit_event`` →
  ``annotate_audit`` (the single annotation point since slice 2 of #1150),
  carrying ``category='auth'``, the action outcome, and the acting identity
  in the annotation details — including the attempted username on failed
  logins, which the middleware's own username column (JWT-cache backed)
  cannot see. The same call also writes the audit-file line
  (logs/audit.log).
- The legacy per-event auth audit table is gone: the dual-write ended
  with the stop-writes (#1294 PR-A), and PR-B archived the table by
  rename (zero-copy; final cleanup is a manual per-deployment op) along
  with its retention entry. The reader methods (``get_user_audit_log``,
  ``get_failed_login_attempts``, ``get_recent_events``) went with the
  writer — they had no callers in ``src/``.
- Retention is a single knob now: ``AUDIT_TRAIL_RETENTION_DAYS``
  (audit_trail.timestamp) — see
  ``src/modules/data_retention/data_retention_service.py``; the retired
  table's own knob went with it in PR-B.
- No non-request auth writers exist: the one indirect writer,
  ``UserRepository._emit_provisioning_audit`` (SSO auto-provisioning),
  delegates to ``log_auth`` during a login request, so the same
  request-scoped annotation chain covers it and no direct writer is needed.
- The audit log file (logs/audit.log) format is byte-frozen (compliance);
  identity parity lives in the annotation only, never in the file line.
"""
from typing import Optional, Dict, Any
from uuid import UUID
import logging

# Import the comprehensive audit logger
from src.api.utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)


class AuditService:
    """Handles authentication audit logging."""

    def __init__(self, conn_manager):
        """
        Initialize audit service.

        Args:
            conn_manager: PostgreSQL connection manager instance. Retained
                for signature compatibility — callers everywhere construct
                the service as ``AuditService(conn_manager)`` — but unused
                since the legacy-table stop-writes (#1294 PR-A).
        """
        self.conn_manager = conn_manager

    def log_auth(
        self,
        action: str,
        success: bool,
        user_id: Optional[UUID] = None,
        username: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        failure_reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log authentication event.

        Single write path (#1150, #1294): forwards to
        ``AuditLogger.log_auth_event``, which annotates the in-flight
        request's ``audit_trail`` row — the system of record — with
        ``category='auth'``, the outcome (``success``/``failure``), and the
        acting identity (``user_id``/``username`` when meaningful) in the
        annotation details. The audit file (logs/audit.log) gets its frozen
        line from the same call. Runs inside one try/except: audit logging
        must NEVER break authentication.

        Args:
            action: Authentication action (login, logout, token_refresh, etc.)
            success: Whether the action succeeded
            user_id: User UUID (None for failed attempts on non-existent users)
            username: Username (for tracking failed attempts)
            ip: Client IP address
            user_agent: Client user agent string
            failure_reason: Reason for failure (user_not_found, invalid_password, etc.)
            metadata: Additional context as dictionary (e.g., session_id, failed_attempts)
        """
        try:
            # Application logger for immediate visibility
            if success:
                logger.info(
                    f"Auth event: {action} - user={username or user_id} "
                    f"ip={ip} success={success}"
                )
            else:
                logger.warning(
                    f"Auth event: {action} - user={username or user_id} "
                    f"ip={ip} success={success} reason={failure_reason}"
                )

            # Comprehensive audit file + audit_trail annotation (system of record)
            details = {
                'ip_address': ip,
                'user_agent': user_agent
            }
            if metadata:
                details.update(metadata)
            if failure_reason:
                details['failure_reason'] = failure_reason

            AuditLogger.log_auth_event(
                event=action,
                user_id=str(user_id) if user_id else 'unknown',
                username=username or 'unknown',
                success=success,
                details=details,
                reason=failure_reason if not success else None
            )

        except Exception as e:
            # Don't let audit logging failures break authentication flow
            logger.error(f"Error logging auth event: {e}")
