# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for audit_middleware: sensitive-field sanitization,
JWT-cache-backed username resolution, and the annotate_audit() merge (#1150)."""

import json
from unittest.mock import MagicMock, patch


def test_bearer_token_is_sanitized_in_audit_logs():
    """Test that bearer_token is sanitized in request body audit logging."""
    from src.api.middleware.audit_middleware import _sanitize_body

    body = {
        'bearer_token': 'secret-token-123',
        'other_field': 'public-data'
    }

    sanitized = _sanitize_body(body)

    assert sanitized['bearer_token'] == '***REDACTED***'
    assert sanitized['other_field'] == 'public-data'


def test_bearer_token_case_insensitive_sanitization():
    """Test that bearer_token sanitization is case-insensitive."""
    from src.api.middleware.audit_middleware import _sanitize_body

    body = {
        'Bearer_Token': 'secret-token-123',
        'BEARER_TOKEN': 'another-secret-456',
    }

    sanitized = _sanitize_body(body)

    assert sanitized['Bearer_Token'] == '***REDACTED***'
    assert sanitized['BEARER_TOKEN'] == '***REDACTED***'


class TestResolveUsername:
    """Direct tests for ``_resolve_username`` — write-time username lookup."""

    def test_anonymous_user_returns_none(self):
        from src.api.middleware.audit_middleware import _resolve_username
        assert _resolve_username(None) is None
        assert _resolve_username('anonymous') is None
        assert _resolve_username('') is None

    def test_cache_hit_returns_real_username(self):
        from src.api.middleware.audit_middleware import _resolve_username

        with patch(
            'src.api.middleware.audit_middleware.cache.get',
            return_value={'id': 'u1', 'username': 'alice', 'role': 'admin'},
        ) as mock_get:
            result = _resolve_username('u1')

        assert result == 'alice'
        mock_get.assert_called_once_with('jwt_user:u1')

    def test_cache_miss_returns_none(self):
        """The typical case for routes that don't run ``user_lookup_loader``
        (e.g. login). Must not raise."""
        from src.api.middleware.audit_middleware import _resolve_username

        with patch(
            'src.api.middleware.audit_middleware.cache.get',
            return_value=None,
        ):
            assert _resolve_username('u1') is None

    def test_cache_backend_error_returns_none(self):
        """Cache backend failure must not break the audit insert path —
        the entire write is fire-and-forget."""
        from src.api.middleware.audit_middleware import _resolve_username

        with patch(
            'src.api.middleware.audit_middleware.cache.get',
            side_effect=RuntimeError('redis unreachable'),
        ):
            assert _resolve_username('u1') is None


class TestAnnotationMerge:
    """annotate_audit() context merged into the single audit_trail INSERT.

    A MagicMock DB never evaluates SQL, so these assert BOTH the params dict
    and that the generated SQL string names the new columns/placeholders.
    DB-free: bare Flask app, real requests through the test client.
    """

    def _make_app(self):
        from flask import Flask

        from src.api.middleware.audit_middleware import init_audit_middleware
        from src.api.utils.audit_context import annotate_audit

        app = Flask(__name__)
        app.config['TESTING'] = True
        app.db_adapter = MagicMock()

        @app.route('/api/plain', methods=['POST'])
        def plain():
            return {'ok': True}

        @app.route('/api/annotated', methods=['POST'])
        def annotated():
            annotate_audit(
                resource_type='integration',
                action='update',
                resource_id=42,
                details={'schedule': '0 0 * * *'},
                category='security',
            )
            return {'ok': True}

        @app.route('/api/annotated-sensitive', methods=['POST'])
        def annotated_sensitive():
            annotate_audit(
                resource_type='integration',
                action='update',
                details={'api_key': 's3cr3t',
                         'nested': {'password': 'p', 'keep': 'v'}},
            )
            return {'ok': True}

        @app.route('/api/annotated-nan', methods=['POST'])
        def annotated_nan():
            annotate_audit(
                resource_type='integration',
                action='update',
                details={'value': float('nan')},
            )
            return {'ok': True}

        @app.route('/api/annotated-oversized', methods=['POST'])
        def annotated_oversized():
            annotate_audit(
                resource_type='integration',
                action='update',
                details={'blob': 'x' * (70 * 1024)},
            )
            return {'ok': True}

        @app.route('/api/annotated-nonstring-keys', methods=['POST'])
        def annotated_nonstring_keys():
            annotate_audit(
                resource_type='integration',
                action='update',
                details={42: 'renamed'},
            )
            return {'ok': True}

        @app.route('/api/logger-annotated', methods=['POST'])
        def logger_annotated():
            # No direct annotate_audit call: the AuditLogger call itself must
            # annotate via the single integration point in log_audit_event.
            from src.api.utils.audit_logger import AuditLogger
            from src.api.utils.audit_vocab import AuditAction, AuditResource

            AuditLogger.log_config_change(
                resource=AuditResource.INTEGRATION,
                resource_id='int-42',
                action=AuditAction.UPDATE,
                user_id='u1',
                username='alice',
                changes={'schedule': {'old': '0 0 * * *', 'new': '0 */6 * * *'}},
                reason='Changed to run every 6 hours',
            )
            return {'ok': True}

        init_audit_middleware(app)
        return app

    def _post_and_get_insert(self, app, path):
        """Drive one POST and return (sql, params) of the single audit INSERT."""
        with app.test_client() as client:
            response = client.post(path, json={'name': 'x'})
        assert response.status_code == 200
        app.db_adapter.execute_query.assert_called_once()
        sql, params = app.db_adapter.execute_query.call_args[0]
        return sql, params

    def test_annotated_request_carries_all_five_values(self):
        sql, params = self._post_and_get_insert(self._make_app(), '/api/annotated')

        # The MagicMock DB never sees whether the SQL names the columns —
        # assert the string itself, not just the params.
        for column in ('category', 'resource_type', 'resource_id', 'action', 'details'):
            assert column in sql
        for placeholder in ('%(category)s', '%(resource_type)s', '%(resource_id)s',
                            '%(action)s', '%(details)s'):
            assert placeholder in sql

        assert params['category'] == 'security'
        assert params['resource_type'] == 'integration'
        assert params['resource_id'] == '42'  # TEXT column — coerced from int
        assert params['action'] == 'update'
        assert json.loads(params['details']) == {'schedule': '0 0 * * *'}

        # The HTTP-shaped fields are untouched by the annotation.
        assert params['method'] == 'POST'
        assert params['path'] == '/api/annotated'
        assert params['status_code'] == 200
        assert json.loads(params['request_body']) == {'name': 'x'}

    def test_unannotated_request_writes_todays_row_plus_five_nulls(self):
        _, params = self._post_and_get_insert(self._make_app(), '/api/plain')

        for key in ('category', 'resource_type', 'resource_id', 'action', 'details'):
            assert params[key] is None

        # Everything else is byte-for-byte the row the middleware writes today.
        assert params['user_id'] is None
        assert params['username'] is None
        assert params['method'] == 'POST'
        assert params['path'] == '/api/plain'
        assert params['status_code'] == 200
        assert json.loads(params['request_body']) == {'name': 'x'}
        assert params['response_summary'].startswith('{"ok"')
        assert params['duration_ms'] is None
        assert params['request_id'] is None

    def test_details_sensitive_keys_are_redacted_recursively(self):
        _, params = self._post_and_get_insert(self._make_app(), '/api/annotated-sensitive')

        assert json.loads(params['details']) == {
            'api_key': '***REDACTED***',
            'nested': {'password': '***REDACTED***', 'keep': 'v'},
        }

    def test_nan_in_details_drops_details_but_keeps_the_row(self):
        """details are built in Python, so they can carry NaN — which
        psycopg2/Postgres reject in jsonb. The insert must survive."""
        _, params = self._post_and_get_insert(self._make_app(), '/api/annotated-nan')

        assert params['details'] is None
        assert params['resource_type'] == 'integration'

    def test_oversized_details_are_replaced_with_truncation_marker(self):
        _, params = self._post_and_get_insert(self._make_app(), '/api/annotated-oversized')

        assert params['details'] == json.dumps({'truncated': True})

    def test_nonstring_details_keys_drop_details_but_keep_the_row(self):
        """_sanitize_body calls key.lower() on every key, so a non-string key
        raises AttributeError — which must cost the details, never the row."""
        _, params = self._post_and_get_insert(
            self._make_app(), '/api/annotated-nonstring-keys'
        )

        assert params['details'] is None
        assert params['resource_type'] == 'integration'

    def test_audit_logger_call_annotates_the_same_single_row(self):
        """The ONE integration point: a handler that only calls
        AuditLogger.log_* gets its semantic fields into the middleware's
        single audit_trail INSERT — no direct annotate_audit, no second row
        (_post_and_get_insert asserts execute_query was called exactly once).
        Enum members arrive as their plain string values, never
        'ClassName.MEMBER'."""
        _, params = self._post_and_get_insert(self._make_app(), '/api/logger-annotated')

        assert params['category'] == 'config'
        assert params['resource_type'] == 'integration'
        assert params['resource_id'] == 'int-42'
        assert params['action'] == 'update'
        assert json.loads(params['details']) == {
            'event': 'integration_config_update',
            'reason': 'Changed to run every 6 hours',
            'user_id': 'u1',
            'username': 'alice',
            'changes': {'schedule': {'old': '0 0 * * *', 'new': '0 */6 * * *'}},
        }

    def _make_auth_fold_app(self):
        """App whose login routes run a REAL AuditService over a mocked
        conn_manager — the auth fold exercised end to end after the
        stop-writes (#1294 PR-A, table archived and dropped in PR-B): NO
        database I/O at all on the service's mock cursor, one annotated
        audit_trail INSERT on the middleware's db_adapter."""
        from uuid import uuid4

        from flask import Flask

        from src.api.middleware.audit_middleware import init_audit_middleware
        from src.auth.services.audit_service import AuditService

        app = Flask(__name__)
        app.config['TESTING'] = True
        app.db_adapter = MagicMock()

        conn_manager = MagicMock()
        cursor = MagicMock()
        conn = MagicMock()
        conn_manager.get_cursor.return_value.__enter__.return_value = (cursor, conn)
        conn_manager.get_cursor.return_value.__exit__.return_value = False
        service = AuditService(conn_manager)
        user_id = uuid4()

        @app.route('/api/auth-fold/login-failure', methods=['POST'])
        def login_failure():
            service.log_auth(
                action='login',
                success=False,
                username='mallory',
                ip='1.2.3.4',
                user_agent='curl/8',
                failure_reason='invalid_password',
            )
            return {'ok': True}

        @app.route('/api/auth-fold/login-success', methods=['POST'])
        def login_success():
            service.log_auth(
                action='login',
                success=True,
                user_id=user_id,
                username='alice',
                ip='10.0.0.1',
                user_agent='Mozilla/5.0',
                metadata={'session_id': 'sess-1'},
            )
            return {'ok': True}

        init_audit_middleware(app)
        return app, cursor, conn, user_id

    def test_failed_login_no_service_db_io_and_carries_attempted_username(self):
        """The auth fold after the stop-writes (#1294 PR-A): a failed login
        through a REAL AuditService (a) performs NO database I/O through the
        service's conn_manager — the table it once wrote was archived and
        dropped in PR-B, (b) lands ONE audit_trail row annotated
        category=auth/action=failure whose details carry the event, the
        failure reason AND the attempted username, and (c) leaves the row's
        username COLUMN NULL — there is no JWT, and an annotation must never
        spoof the middleware's authoritative columns."""
        app, cursor, _, _ = self._make_auth_fold_app()
        _, params = self._post_and_get_insert(app, '/api/auth-fold/login-failure')

        # (a) stop-writes: the service performs no database I/O at all, so
        # its conn_manager's cursor is never used.
        cursor.execute.assert_not_called()

        # (b) the single audit_trail row carries the semantic annotation —
        # exact match also proves user_id='unknown' was dropped as a
        # placeholder rather than recorded as fact.
        assert params['category'] == 'auth'
        assert params['resource_type'] == 'authentication'
        assert params['resource_id'] is None
        assert params['action'] == 'failure'
        assert json.loads(params['details']) == {
            'event': 'login',
            'reason': 'invalid_password',
            'username': 'mallory',
            'ip_address': '1.2.3.4',
            'user_agent': 'curl/8',
            'failure_reason': 'invalid_password',
        }

        # (c) the middleware's own identity columns stay NULL — no JWT.
        assert params['user_id'] is None
        assert params['username'] is None

    def test_successful_login_annotation_carries_identity(self):
        """Success path, equally pinned: the service performs NO database
        I/O (stop-writes, #1294 PR-A; table archived and dropped in PR-B)
        and the annotation carries action='success' with the acting
        identity (user_id + username) in details."""
        app, cursor, _, user_id = self._make_auth_fold_app()
        _, params = self._post_and_get_insert(app, '/api/auth-fold/login-success')

        cursor.execute.assert_not_called()

        assert params['category'] == 'auth'
        assert params['resource_type'] == 'authentication'
        assert params['action'] == 'success'
        assert json.loads(params['details']) == {
            'event': 'login',
            'user_id': str(user_id),
            'username': 'alice',
            'ip_address': '10.0.0.1',
            'user_agent': 'Mozilla/5.0',
            'session_id': 'sess-1',
        }
