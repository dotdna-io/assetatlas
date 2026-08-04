# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for IntegrationTechnologyRepository (#494)."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from src.modules.integrations.repositories.technology_repository import (
    IntegrationTechnologyRepository,
)


def _build_repo(rows):
    """Construct a repo whose connection returns ``rows`` from list_technologies."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = lambda *a: False

    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def _conn_ctx():
        yield conn

    cm = MagicMock()
    cm.get_connection = _conn_ctx

    repo = IntegrationTechnologyRepository(
        db_url='postgresql://x', connection_manager=cm,
    )
    return repo


class TestListTechnologies:
    def test_returns_wizard_shaped_dicts(self):
        repo = _build_repo([
            {
                'id': 'palo-alto-networks',
                'name': 'Palo Alto Networks',
                'category': 'Vendor',
                'logo_path': '/logos/paloalto.png',
                'direct_connector': None,
                'siem_passthrough_supported': True,
                'default_transports': ['splunk', 'log_analytics'],
                'description': 'Pull Palo Alto data via SIEM',
            }
        ])

        result = repo.list_technologies()
        assert len(result) == 1
        tech = result[0]
        # Shape must match what the wizard expects from ConnectorRegistry.
        assert tech['integration_type'] == 'palo-alto-networks'
        assert tech['display_name'] == 'Palo Alto Networks'
        assert tech['category'] == 'Vendor'
        assert tech['logo_url'] == '/logos/paloalto.png'
        assert tech['connector_role'] == 'technology'
        assert tech['direct_connector'] is None
        assert tech['siem_passthrough_supported'] is True
        assert tech['default_transports'] == ['splunk', 'log_analytics']
        assert tech['config_schema'] == {}

    def test_handles_null_description_and_transports(self):
        repo = _build_repo([
            {
                'id': 'cisco',
                'name': 'Cisco',
                'category': 'Vendor',
                'logo_path': '/logos/cisco.png',
                'direct_connector': None,
                'siem_passthrough_supported': True,
                'default_transports': None,
                'description': None,
            }
        ])

        tech = repo.list_technologies()[0]
        assert tech['description'] == ''
        assert tech['default_transports'] == []


class TestRequiresDatabaseUrl:
    def test_raises_when_no_db_url(self, monkeypatch):
        monkeypatch.delenv('DATABASE_URL', raising=False)
        with pytest.raises(ValueError, match='DATABASE_URL not set'):
            IntegrationTechnologyRepository()
