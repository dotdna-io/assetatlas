# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for AuditLogger (#1150, slice 2).

Two contracts pinned here:

1. FILE-FORMAT FREEZE — audit.log is a byte-frozen compliance artifact.
   Vocabulary enums (``AuditResource``/``AuditAction``) must produce
   byte-identical lines to the free-text spellings they replaced, including
   the ``resource:resource_id`` concatenation and the severity mapping.
   Slice 3's identity parity lives in the annotation ONLY — the file line
   must not grow user_id/username inside details.
2. ONE integration point — ``log_audit_event`` annotates the in-flight
   request via ``annotate_audit`` when (and only when) an app context is
   active, so wrappers and call sites annotate for free.

The 'audit' logger is propagate=False with a real RotatingFileHandler
installed at import, so caplog never sees these records — the tests attach
their own handler to the logger directly instead of fighting caplog.
"""

import io
import json
import logging
import re

import pytest
from flask import Flask

from src.api.utils.audit_context import get_audit_annotation
from src.api.utils.audit_logger import AuditLogger, audit_logger
from src.api.utils.audit_vocab import AuditAction, AuditResource


@pytest.fixture
def captured_audit_lines():
    """Attach a throwaway handler with the REAL audit formatter and yield
    the stream behind it. Uses the production formatter (found on the
    import-time file handler) so the frozen template is what renders."""
    real_formatter = next(
        h.formatter for h in audit_logger.handlers if h.formatter is not None
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(real_formatter)
    audit_logger.addHandler(handler)
    try:
        yield stream
    finally:
        audit_logger.removeHandler(handler)


def _lines(stream):
    return [line for line in stream.getvalue().strip().split('\n') if line]


def _strip_timestamp(line):
    return re.sub(r'"timestamp": "[^"]+"', '"timestamp": "T"', line)


class TestFileFormatFreeze:
    def test_enum_and_freetext_calls_produce_identical_lines(self, captured_audit_lines):
        """The representative-event freeze proof: the vocab swap must not
        change a single byte of audit.log output for existing call sites."""
        AuditLogger.log_config_change(
            resource=AuditResource.INTEGRATION,
            resource_id='int-42',
            action=AuditAction.UPDATE,
            user_id='u1',
            username='alice',
            changes={'schedule': {'old': '0 0 * * *', 'new': '0 */6 * * *'}},
            reason='Changed to run every 6 hours',
        )
        AuditLogger.log_config_change(
            resource='integration',
            resource_id='int-42',
            action='update',
            user_id='u1',
            username='alice',
            changes={'schedule': {'old': '0 0 * * *', 'new': '0 */6 * * *'}},
            reason='Changed to run every 6 hours',
        )

        enum_line, legacy_line = _lines(captured_audit_lines)
        assert _strip_timestamp(enum_line) == _strip_timestamp(legacy_line)

    def test_representative_line_shape(self, captured_audit_lines):
        """The frozen template: fixed key order, resource:resource_id
        concatenation, severity mapping, JSON details."""
        AuditLogger.log_data_operation(
            resource=AuditResource.TAG_RULE,
            resource_id='rule-7',
            action=AuditAction.DELETE,
            user_id='u1',
            username='alice',
            details={'name': 'Old rule'},
            reason='Cleanup',
        )

        (line,) = _lines(captured_audit_lines)
        # Key order is part of the frozen template — assert it on the raw text.
        assert list(json.loads(line).keys()) == [
            'timestamp', 'level', 'category', 'event', 'user_id', 'username',
            'resource', 'action', 'ip_address', 'request_id', 'severity',
            'details', 'reason',
        ]
        record = json.loads(line)
        assert record['level'] == 'CRITICAL'      # delete maps to CRITICAL...
        assert record['severity'] == 'critical'   # ...in both fields
        assert record['category'] == 'data'
        assert record['event'] == 'tag_rule_delete'
        assert record['resource'] == 'tag_rule:rule-7'  # the concatenation
        assert record['action'] == 'delete'
        assert record['details'] == {'name': 'Old rule'}  # inline JSON object
        assert record['reason'] == 'Cleanup'

    def test_auth_line_details_stay_identity_free(self, captured_audit_lines):
        """Identity parity (#1150 slice 3) is ANNOTATION-ONLY: a failed
        login's file line keeps exactly today's details keys (ip_address,
        user_agent, metadata keys, failure_reason) and does NOT grow
        user_id/username — identity already has its own top-level fields in
        the frozen template; the annotation is where it lands for the
        audit_trail row."""
        AuditLogger.log_auth_event(
            event='login',
            user_id='unknown',
            username='mallory',
            success=False,
            details={'ip_address': '1.2.3.4', 'user_agent': 'curl/8',
                     'failure_reason': 'invalid_password'},
            reason='invalid_password',
        )

        (line,) = _lines(captured_audit_lines)
        record = json.loads(line)
        assert record['category'] == 'auth'
        assert record['event'] == 'login'
        assert record['username'] == 'mallory'    # top-level field, as today
        assert record['resource'] == 'authentication'
        assert record['action'] == 'failure'      # the failure mapping
        assert record['severity'] == 'warning'
        assert record['details'] == {             # exactly today's keys…
            'ip_address': '1.2.3.4',
            'user_agent': 'curl/8',
            'failure_reason': 'invalid_password',
        }                                         # …no user_id/username
        assert record['reason'] == 'invalid_password'


class TestAnnotateIntegrationPoint:
    def test_annotates_inside_app_context(self):
        app = Flask(__name__)
        with app.test_request_context('/api/x', method='POST'):
            AuditLogger.log_config_change(
                resource=AuditResource.INTEGRATION,
                resource_id='int-42',
                action=AuditAction.UPDATE,
                user_id='u1',
                username='alice',
                details={'schedule': '0 0 * * *'},
                reason='reschedule',
            )

            assert get_audit_annotation() == {
                'category': 'config',
                'resource_type': 'integration',
                'resource_id': 'int-42',
                'action': 'update',
                'details': {
                    'event': 'integration_config_update',
                    'reason': 'reschedule',
                    'user_id': 'u1',
                    'username': 'alice',
                    'schedule': '0 0 * * *',
                },
            }

    def test_annotation_drops_none_valued_details(self):
        app = Flask(__name__)
        with app.test_request_context('/api/x', method='POST'):
            AuditLogger.log_data_operation(
                resource=AuditResource.ENRICHMENT_DATASET,
                resource_id='ds-1',
                action=AuditAction.CREATE,
                user_id='u1',
                username='alice',
                details={'name': 'vendors', 'row_count': None},
            )

            annotation = get_audit_annotation()
            assert annotation['details'] == {
                'event': 'enrichment_dataset_create',
                'user_id': 'u1',
                'username': 'alice',
                'name': 'vendors',
            }

    def test_placeholder_identities_are_not_annotated(self):
        """'anonymous'/'unknown'/None are placeholders, not facts — they must
        not land in the annotation (#1150 slice 3). A real identity does —
        that is what carries the attempted username of a failed login."""
        app = Flask(__name__)
        with app.test_request_context('/api/x', method='POST'):
            AuditLogger.log_security_event(  # defaults: anonymous/anonymous
                event='rate_limit_exceeded',
                details={'attempt_count': 5},
            )

            assert get_audit_annotation()['details'] == {
                'event': 'rate_limit_exceeded',
                'attempt_count': 5,
            }

        with app.test_request_context('/api/y', method='POST'):
            AuditLogger.log_auth_event(  # AuditService's failed-login shape
                event='login',
                user_id='unknown',
                username='mallory',
                success=False,
                details={'ip_address': '1.2.3.4'},
                reason='invalid_password',
            )

            assert get_audit_annotation()['details'] == {
                'event': 'login',
                'reason': 'invalid_password',
                'username': 'mallory',  # the attempted username survives…
                'ip_address': '1.2.3.4',
                # …while user_id='unknown' is dropped as a placeholder
            }

    def test_no_annotation_outside_app_context(self, caplog):
        """Non-request events (CLI, scheduler) only reach audit.log today.
        The integration point must skip annotation quietly — not even the
        annotate_audit no-op warning — and must still write the file line."""
        with caplog.at_level(logging.WARNING, logger='src.api.utils.audit_context'):
            AuditLogger.log_data_operation(
                resource=AuditResource.TAG_RULE,
                resource_id='r1',
                action=AuditAction.DELETE,
                user_id='u1',
                username='alice',
            )

        assert get_audit_annotation() is None
        assert 'outside an app/request context' not in caplog.text

    def test_authz_wrapper_composes_action_from_plain_strings(self):
        """The wrapper composes f"{action}_{result}" BEFORE log_audit_event
        sees it — enums must be flattened there too, or the frozen line
        would read 'AuditAction.ACCESS_denied'."""
        app = Flask(__name__)
        with app.test_request_context('/api/x', method='POST'):
            AuditLogger.log_authz_event(
                event='access_check',
                user_id='u1',
                username='alice',
                resource='/api/integrations',
                action=AuditAction.ACCESS,
                allowed=False,
            )

            annotation = get_audit_annotation()
            assert annotation['action'] == 'access_denied'
            assert annotation['category'] == 'authorization'
