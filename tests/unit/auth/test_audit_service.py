# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for AuditService — log_auth forwards to AuditLogger only.

Since the legacy-table stop-writes (#1294 PR-A) — and the table's
archive-and-drop in PR-B — the service performs no database I/O at all;
the mock_conn_manager fixture proves the cursor is never used.
"""
import logging
from unittest.mock import patch
from uuid import uuid4

from src.auth.services.audit_service import AuditService


# ---------------------------------------------------------------------------
# log_auth
# ---------------------------------------------------------------------------


class TestLogAuth:
    """log_auth forwards to AuditLogger and emits the app-log line — and
    touches no database cursor at all (#1294: the legacy table was
    archived and dropped in PR-B)."""

    def test_successful_login_forwards_to_audit_logger(self, mock_conn_manager, caplog):
        cm, cursor, _ = mock_conn_manager
        user_id = uuid4()
        with patch('src.auth.services.audit_service.AuditLogger') as mock_audit_logger:
            with caplog.at_level(logging.INFO, logger='src.auth.services.audit_service'):
                AuditService(cm).log_auth(
                    action='login',
                    success=True,
                    user_id=user_id,
                    username='alice',
                    ip='10.0.0.1',
                    user_agent='Mozilla/5.0',
                    metadata={'session_id': 'sess-1'},
                )

        # Stop-writes (#1294 PR-A, table archived and dropped in PR-B): the
        # service performs no database I/O, so the cursor is never used.
        cursor.execute.assert_not_called()

        # App-log line for immediate visibility
        assert 'Auth event: login - user=alice ip=10.0.0.1 success=True' in caplog.text

        # Audit-file/annotation branch was hit
        mock_audit_logger.log_auth_event.assert_called_once()
        af_kwargs = mock_audit_logger.log_auth_event.call_args.kwargs
        assert af_kwargs['event'] == 'login'
        assert af_kwargs['user_id'] == str(user_id)
        assert af_kwargs['username'] == 'alice'
        assert af_kwargs['success'] is True
        assert af_kwargs['details']['ip_address'] == '10.0.0.1'
        assert af_kwargs['details']['user_agent'] == 'Mozilla/5.0'
        assert af_kwargs['details']['session_id'] == 'sess-1'
        assert af_kwargs['reason'] is None

    def test_failure_includes_reason_in_audit_file(self, mock_conn_manager, caplog):
        cm, cursor, _ = mock_conn_manager
        with patch('src.auth.services.audit_service.AuditLogger') as mock_audit_logger:
            with caplog.at_level(logging.WARNING, logger='src.auth.services.audit_service'):
                AuditService(cm).log_auth(
                    action='login_failed',
                    success=False,
                    username='bob',
                    ip='1.2.3.4',
                    user_agent='curl/8',
                    failure_reason='invalid_password',
                )

        cursor.execute.assert_not_called()

        # App-log warning carries the failure reason
        assert ('Auth event: login_failed - user=bob ip=1.2.3.4 success=False '
                'reason=invalid_password') in caplog.text

        af_kwargs = mock_audit_logger.log_auth_event.call_args.kwargs
        assert af_kwargs['user_id'] == 'unknown'  # no UUID for unknown user
        assert af_kwargs['username'] == 'bob'
        assert af_kwargs['success'] is False
        assert af_kwargs['details']['failure_reason'] == 'invalid_password'
        assert af_kwargs['reason'] == 'invalid_password'

    def test_audit_logger_failure_swallowed_does_not_raise(self, mock_conn_manager):
        """Audit logging must NEVER break authentication — an AuditLogger
        failure inside log_auth must not propagate."""
        cm, _, _ = mock_conn_manager
        with patch('src.auth.services.audit_service.AuditLogger') as mock_audit_logger:
            mock_audit_logger.log_auth_event.side_effect = RuntimeError('audit sink down')

            # Should not raise
            AuditService(cm).log_auth(action='login', success=True, username='alice')
