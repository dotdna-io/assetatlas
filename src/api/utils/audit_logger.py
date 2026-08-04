# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Audit Logging Service

Provides comprehensive audit trail for security, compliance, and operational tracking.
Captures who did what, when, where, and why for all critical system events.

Usage:
    from src.api.utils.audit_logger import AuditLogger, AuditEventCategory, AuditSeverity

    # Log authentication event
    AuditLogger.log_auth_event(
        event='login_success',
        user_id=user.id,
        username=user.username,
        details={'ip_address': request.remote_addr}
    )

    # Log configuration change
    AuditLogger.log_config_change(
        resource='integration',
        resource_id=integration.id,
        action='update',
        user_id=current_user.id,
        changes={'schedule': {'old': '0 0 * * *', 'new': '0 */6 * * *'}},
        reason='Changed to run every 6 hours'
    )
"""

import logging
import json
import os
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional
from logging.handlers import RotatingFileHandler
from flask import request, has_request_context, has_app_context

from src.api.utils.audit_context import annotate_audit

# Create separate audit logger (different from application logger)
audit_logger = logging.getLogger('audit')
audit_logger.setLevel(logging.INFO)
audit_logger.propagate = False  # Don't propagate to root logger


class AuditEventCategory(str, Enum):
    """Categories of audit events for classification and filtering."""

    AUTH = 'auth'                   # Authentication events (login, logout, token refresh)
    AUTHZ = 'authorization'         # Authorization events (permission checks, access denied)
    DATA = 'data'                   # Data operations (create, update, delete)
    CONFIG = 'config'               # Configuration changes (settings, integrations)
    ADMIN = 'admin'                 # Administrative actions (user management, system config)
    SECURITY = 'security'           # Security events (failed auth, suspicious activity)
    COMPLIANCE = 'compliance'       # Compliance-related events (data export, retention)


class AuditSeverity(str, Enum):
    """Severity levels for audit events."""

    INFO = 'info'                   # Normal operations (successful login, data read)
    WARNING = 'warning'             # Concerning events (repeated failures, unusual patterns)
    CRITICAL = 'critical'           # Critical events (data deletion, security breaches)


def _plain_str(value):
    """Flatten an audit vocab enum member to its plain string value.

    Call sites pass ``AuditResource``/``AuditAction`` members (``str, Enum``
    mixins from ``audit_vocab``). They compare and serialize like their
    values, but ``str()``/f-strings render them as 'ClassName.MEMBER', which
    would corrupt the frozen audit.log line — so every entry point flattens
    them before any string formatting. Plain strings pass through unchanged.
    """
    return value.value if isinstance(value, Enum) else value


def _init_audit_logger():
    """
    Initialize audit logger with file handler.

    Creates separate audit log file with rotation.
    Only initializes once to avoid duplicate handlers.

    If the configured log directory is not writable (read-only mount,
    permissions issue, full disk), fall back to a NullHandler so audit calls
    don't crash the whole application — the app must still serve requests
    even when audit-log persistence is unavailable. The error is logged at
    WARNING so operators see it.
    """
    # Check if already initialized (has handlers)
    if audit_logger.handlers:
        return

    log_dir = os.getenv('LOG_DIR', 'logs')

    try:
        os.makedirs(log_dir, exist_ok=True)
        audit_log_path = os.path.join(log_dir, 'audit.log')
        # Rotate at 10MB, keep 50 backup files (compliance requirement: 365 days retention)
        handler = RotatingFileHandler(
            audit_log_path,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=50,
            encoding='utf-8'
        )
        formatter = logging.Formatter(
            '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "category": "%(category)s", '
            '"event": "%(event)s", "user_id": "%(user_id)s", "username": "%(username)s", '
            '"resource": "%(resource)s", "action": "%(action)s", "ip_address": "%(ip_address)s", '
            '"request_id": "%(request_id)s", "severity": "%(severity)s", "details": %(details)s, '
            '"reason": "%(reason)s"}',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        audit_logger.addHandler(handler)
    except (OSError, PermissionError) as e:
        # Defensive: a missing/non-writable log dir must not abort startup.
        # Log a one-time warning to the standard logger and install a
        # NullHandler so audit calls become no-ops rather than raising.
        logging.getLogger(__name__).warning(
            "Audit log unavailable (%s). Audit events will be discarded for "
            "this process. Set LOG_DIR to a writable path to restore.",
            e,
        )
        audit_logger.addHandler(logging.NullHandler())


# Initialize audit logger when module is imported
_init_audit_logger()


class AuditLogger:
    """
    Audit logging service for tracking security and compliance events.

    Provides structured audit logging with:
    - Who: user_id, username, IP address
    - What: event type, action, resource
    - When: timestamp (ISO 8601)
    - Where: IP address, request_id for correlation
    - Why: reason/notes field

    All audit events are logged to a separate audit.log file with rotation.
    """

    @staticmethod
    def log_audit_event(
        category: AuditEventCategory,
        event: str,
        user_id: str = 'anonymous',
        username: str = 'anonymous',
        resource: str = None,
        resource_id: str = None,
        action: str = None,
        severity: AuditSeverity = AuditSeverity.INFO,
        details: Dict[str, Any] = None,
        reason: str = None
    ):
        """
        Log a generic audit event.

        Args:
            category: Event category (auth, data, config, etc.)
            event: Event name (login_success, asset_created, etc.)
            user_id: User ID performing the action
            username: Username performing the action
            resource: Resource type affected (asset, integration, user, etc.)
            resource_id: ID of the resource affected
            action: Action performed (create, update, delete, read)
            severity: Event severity (info, warning, critical)
            details: Additional event details (free-form dict)
            reason: Reason for the action (e.g., "Part of scheduled maintenance")

        Side effect (#1150): inside a Flask app context this also annotates
        the in-flight request via annotate_audit(), so the audit trail
        middleware merges the same semantic values into its audit_trail row.
        This is the ONE integration point — every log_* wrapper funnels
        through here, so wrappers and their call sites annotate for free.
        """
        # Flatten vocab enums before ANY string formatting (see _plain_str).
        resource = _plain_str(resource)
        action = _plain_str(action)

        # Annotate the request BEFORE building the "resource:resource_id"
        # display string below, so audit_trail gets the two separately.
        # Skipped outside an app context (non-request events — CLI, scheduler
        # — only reach audit.log; slice 3 of #1150 verified no non-request
        # auth writers exist, so no direct writer is needed).
        if has_app_context():
            # Identity parity (slice 3 of #1150): the acting identity as the
            # logger saw it rides in the annotation DETAILS ONLY — the frozen
            # file line below is untouched. Placeholder identities are
            # skipped, not recorded as fact. This closes the failed-login
            # gap: the middleware's own user_id/username columns come from
            # the JWT cache and stay NULL without a JWT, so the attempted
            # username would otherwise be lost. Those columns stay
            # authoritative — an annotation can never spoof them.
            identity = {
                key: value
                for key, value in {'user_id': user_id, 'username': username}.items()
                if value not in (None, 'anonymous', 'unknown')
            }
            annotation_details = {
                key: value
                for key, value in {
                    'event': event,
                    'reason': reason,
                    **identity,
                    **(details or {}),
                }.items()
                if value is not None
            }
            annotate_audit(
                resource_type=resource,
                action=action,
                resource_id=str(resource_id) if resource_id is not None else None,
                details=annotation_details,
                category=category.value,
            )

        # Get request context if available
        ip_address = 'unknown'
        request_id = 'no-request'

        if has_request_context():
            ip_address = request.remote_addr or 'unknown'
            request_id = getattr(request, 'request_id', 'no-request') if hasattr(request, 'request_id') else 'no-request'

            # Try to get request_id from g object
            try:
                from flask import g
                request_id = getattr(g, 'request_id', request_id)
            except:
                pass

        # Construct resource identifier
        resource_str = resource or 'unknown'
        if resource_id:
            resource_str = f"{resource}:{resource_id}"

        # Serialize details to JSON
        details_json = json.dumps(details or {}, default=str)

        # Determine log level based on severity
        log_level = {
            AuditSeverity.INFO: logging.INFO,
            AuditSeverity.WARNING: logging.WARNING,
            AuditSeverity.CRITICAL: logging.CRITICAL
        }.get(severity, logging.INFO)

        # Log the audit event
        audit_logger.log(
            log_level,
            f"{category.value}.{event}",
            extra={
                'category': category.value,
                'event': event,
                'user_id': user_id,
                'username': username,
                'resource': resource_str,
                'action': action or 'unknown',
                'ip_address': ip_address,
                'request_id': request_id,
                'severity': severity.value,
                'details': details_json,
                'reason': reason or ''
            }
        )

    @staticmethod
    def log_auth_event(
        event: str,
        user_id: str = 'anonymous',
        username: str = 'anonymous',
        success: bool = True,
        details: Dict[str, Any] = None,
        reason: str = None
    ):
        """
        Log an authentication event (login, logout, token refresh, etc.).

        Args:
            event: Event name (login, logout, token_refresh, etc.)
            user_id: User ID
            username: Username
            success: Whether the authentication was successful
            details: Additional details (user_agent, location, etc.)
            reason: Reason for failure (if applicable)

        Example:
            AuditLogger.log_auth_event(
                event='login',
                user_id=user.id,
                username=user.username,
                success=True,
                details={'user_agent': request.headers.get('User-Agent')}
            )
        """
        severity = AuditSeverity.INFO if success else AuditSeverity.WARNING
        action = 'success' if success else 'failure'

        AuditLogger.log_audit_event(
            category=AuditEventCategory.AUTH,
            event=event,
            user_id=user_id,
            username=username,
            resource='authentication',
            action=action,
            severity=severity,
            details=details,
            reason=reason
        )

    @staticmethod
    def log_authz_event(
        event: str,
        user_id: str,
        username: str,
        resource: str,
        action: str,
        allowed: bool,
        details: Dict[str, Any] = None,
        reason: str = None
    ):
        """
        Log an authorization event (permission check, access denied, etc.).

        Args:
            event: Event name (permission_check, role_change, etc.)
            user_id: User ID
            username: Username
            resource: Resource being accessed
            action: Action attempted
            allowed: Whether access was allowed
            details: Additional details (required_role, user_role, etc.)
            reason: Reason for denial (if applicable)

        Example:
            AuditLogger.log_authz_event(
                event='access_check',
                user_id=current_user.id,
                username=current_user.username,
                resource='integration',
                action='delete',
                allowed=False,
                details={'required_role': 'admin', 'user_role': 'user'},
                reason='Insufficient permissions'
            )
        """
        severity = AuditSeverity.WARNING if not allowed else AuditSeverity.INFO
        result = 'allowed' if allowed else 'denied'

        resource = _plain_str(resource)
        action = _plain_str(action)

        AuditLogger.log_audit_event(
            category=AuditEventCategory.AUTHZ,
            event=event,
            user_id=user_id,
            username=username,
            resource=resource,
            action=f"{action}_{result}",
            severity=severity,
            details=details,
            reason=reason
        )

    @staticmethod
    def log_data_operation(
        resource: str,
        resource_id: str,
        action: str,
        user_id: str,
        username: str,
        changes: Dict[str, Any] = None,
        details: Dict[str, Any] = None,
        reason: str = None
    ):
        """
        Log a data operation (create, update, delete).

        Args:
            resource: Resource type (asset, tag_rule, integration, etc.)
            resource_id: ID of the resource
            action: Action performed (create, update, delete)
            user_id: User ID
            username: Username
            changes: Changes made (for updates: {'field': {'old': ..., 'new': ...}})
            details: Additional details
            reason: Reason for the operation

        Example:
            AuditLogger.log_data_operation(
                resource='asset',
                resource_id=asset.id,
                action='delete',
                user_id=current_user.id,
                username=current_user.username,
                details={'hostname': asset.hostname},
                reason='Asset decommissioned'
            )
        """
        resource = _plain_str(resource)
        action = _plain_str(action)

        severity = AuditSeverity.CRITICAL if action == 'delete' else AuditSeverity.INFO

        # Merge changes into details
        all_details = details or {}
        if changes:
            all_details['changes'] = changes

        AuditLogger.log_audit_event(
            category=AuditEventCategory.DATA,
            event=f"{resource}_{action}",
            user_id=user_id,
            username=username,
            resource=resource,
            resource_id=resource_id,
            action=action,
            severity=severity,
            details=all_details,
            reason=reason
        )

    @staticmethod
    def log_config_change(
        resource: str,
        resource_id: str,
        action: str,
        user_id: str,
        username: str,
        changes: Dict[str, Any] = None,
        details: Dict[str, Any] = None,
        reason: str = None
    ):
        """
        Log a configuration change (integration config, system settings, etc.).

        Args:
            resource: Resource type (integration, settings, connector, etc.)
            resource_id: ID of the resource
            action: Action performed (create, update, delete)
            user_id: User ID
            username: Username
            changes: Changes made ({'field': {'old': ..., 'new': ...}})
            details: Additional details
            reason: Reason for the change

        Example:
            AuditLogger.log_config_change(
                resource='integration',
                resource_id=integration.id,
                action='update',
                user_id=current_user.id,
                username=current_user.username,
                changes={'schedule': {'old': '0 0 * * *', 'new': '0 */6 * * *'}},
                reason='Changed to run every 6 hours'
            )
        """
        resource = _plain_str(resource)
        action = _plain_str(action)

        # Merge changes into details
        all_details = details or {}
        if changes:
            all_details['changes'] = changes

        AuditLogger.log_audit_event(
            category=AuditEventCategory.CONFIG,
            event=f"{resource}_config_{action}",
            user_id=user_id,
            username=username,
            resource=resource,
            resource_id=resource_id,
            action=action,
            severity=AuditSeverity.INFO,
            details=all_details,
            reason=reason
        )

    @staticmethod
    def log_bulk_operation(
        resource: str,
        action: str,
        user_id: str,
        username: str,
        count: int,
        details: Dict[str, Any] = None,
        reason: str = None
    ):
        """
        Log a bulk operation (bulk delete, bulk import, etc.).

        Args:
            resource: Resource type (asset, tag, etc.)
            action: Action performed (bulk_delete, bulk_import, etc.)
            user_id: User ID
            username: Username
            count: Number of records affected
            details: Additional details (filters used, source, etc.)
            reason: Reason for the operation

        Example:
            AuditLogger.log_bulk_operation(
                resource='asset',
                action='bulk_delete',
                user_id=current_user.id,
                username=current_user.username,
                count=150,
                details={'filter': 'status=decommissioned'},
                reason='Cleanup of decommissioned assets'
            )
        """
        resource = _plain_str(resource)
        action = _plain_str(action)

        severity = AuditSeverity.CRITICAL if action.startswith('bulk_delete') else AuditSeverity.WARNING

        # Add count to details
        all_details = details or {}
        all_details['affected_count'] = count

        AuditLogger.log_audit_event(
            category=AuditEventCategory.DATA,
            event=f"{resource}_{action}",
            user_id=user_id,
            username=username,
            resource=resource,
            action=action,
            severity=severity,
            details=all_details,
            reason=reason
        )

    @staticmethod
    def log_security_event(
        event: str,
        user_id: str = 'anonymous',
        username: str = 'anonymous',
        severity: AuditSeverity = AuditSeverity.WARNING,
        details: Dict[str, Any] = None,
        reason: str = None
    ):
        """
        Log a security-related event (suspicious activity, rate limiting, etc.).

        Args:
            event: Event name (rate_limit_exceeded, suspicious_login, etc.)
            user_id: User ID (if known)
            username: Username (if known)
            severity: Event severity
            details: Additional details (IP address, pattern detected, etc.)
            reason: Reason or explanation

        Example:
            AuditLogger.log_security_event(
                event='repeated_failed_login',
                user_id='unknown',
                username='admin',
                severity=AuditSeverity.WARNING,
                details={'attempt_count': 5, 'ip_address': '192.168.1.100'},
                reason='Potential brute force attack'
            )
        """
        AuditLogger.log_audit_event(
            category=AuditEventCategory.SECURITY,
            event=event,
            user_id=user_id,
            username=username,
            resource='security',
            action='alert',
            severity=severity,
            details=details,
            reason=reason
        )


def get_current_user_context() -> tuple:
    """
    Helper to get current user context from request.

    Returns:
        Tuple of (user_id, username)
    """
    user_id = 'anonymous'
    username = 'anonymous'

    if has_request_context():
        try:
            from flask_jwt_extended import get_jwt, verify_jwt_in_request

            # Try to verify JWT in request (returns None if no token)
            verify_jwt_in_request(optional=True)
            jwt_data = get_jwt()

            if jwt_data:
                user_id = jwt_data.get('sub', 'anonymous')
                username = jwt_data.get('username', 'anonymous')
        except Exception:
            pass

    return user_id, username
