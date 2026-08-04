# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the request-scoped audit annotation store (#1150).

The contract: last call wins within a request, a no-op (with a warning)
outside an app/request context, and the audit middleware is the only reader.
"""

import logging

import pytest
from flask import Flask

from src.api.utils.audit_context import annotate_audit, get_audit_annotation


@pytest.fixture
def request_context():
    app = Flask(__name__)
    with app.test_request_context('/api/x', method='POST'):
        yield


def test_annotate_outside_context_is_a_noop_with_warning(caplog):
    """Fire-and-forget like the rest of the subsystem: never raises, just
    logs, and stores nothing."""
    with caplog.at_level(logging.WARNING, logger='src.api.utils.audit_context'):
        annotate_audit(resource_type='integration', action='update')

    assert 'outside an app/request context' in caplog.text
    assert get_audit_annotation() is None


class TestWithinRequestContext:

    def test_annotation_roundtrips_with_default_category(self, request_context):
        annotate_audit(
            resource_type='integration',
            action='update',
            resource_id='int-123',
            details={'schedule': '0 0 * * *'},
        )

        assert get_audit_annotation() == {
            'category': 'admin',
            'resource_type': 'integration',
            'action': 'update',
            'resource_id': 'int-123',
            'details': {'schedule': '0 0 * * *'},
        }

    def test_last_call_wins(self, request_context):
        annotate_audit(resource_type='integration', action='update')
        annotate_audit(
            resource_type='asset',
            action='delete',
            resource_id='asset-1',
            category='lifecycle',
        )

        assert get_audit_annotation() == {
            'category': 'lifecycle',
            'resource_type': 'asset',
            'action': 'delete',
            'resource_id': 'asset-1',
            'details': None,
        }

    def test_nothing_annotated_returns_none(self, request_context):
        assert get_audit_annotation() is None
