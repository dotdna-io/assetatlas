# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for IntegrationRegistry merging code + DB-defined tiles (#494)."""

from src.modules.integrations import discover_connectors
from src.modules.integrations.registry import ConnectorRegistry


class _FakeTechRepo:
    """Stand-in for IntegrationTechnologyRepository — no DB required."""

    def __init__(self, rows):
        self._rows = rows

    def list_technologies(self):
        return list(self._rows)


class TestRegistryMerge:
    @classmethod
    def setup_class(cls):
        discover_connectors()

    def test_db_only_tile_appears_in_merged_list(self):
        repo = _FakeTechRepo([
            {
                'integration_type': 'palo-alto-networks',
                'display_name': 'Palo Alto Networks',
                'category': 'Vendor',
                'description': 'Pull Palo Alto data via SIEM/datalake',
                'logo_url': '/logos/paloalto.png',
                'connector_role': 'technology',
                'direct_connector': None,
                'config_schema': {},
            }
        ])
        merged = ConnectorRegistry.get_all_connectors(tech_repository=repo)
        types = {c['integration_type'] for c in merged}
        assert 'palo-alto-networks' in types
        tile = next(c for c in merged if c['integration_type'] == 'palo-alto-networks')
        assert tile['direct_connector'] is None
        assert tile['connector_role'] == 'technology'

    def test_code_connector_wins_on_collision(self):
        repo = _FakeTechRepo([
            {
                'integration_type': 'crowdstrike',
                'display_name': 'OVERRIDDEN',
                'category': 'Vendor',
                'description': 'should not appear',
                'logo_url': '/logos/wrong.png',
                'connector_role': 'technology',
                'direct_connector': None,
                'config_schema': {},
            }
        ])
        merged = ConnectorRegistry.get_all_connectors(tech_repository=repo)
        crowdstrike = next(c for c in merged if c['integration_type'] == 'crowdstrike')
        assert crowdstrike['display_name'] != 'OVERRIDDEN'
        assert crowdstrike['display_name'] == 'CrowdStrike Falcon'

    def test_code_connectors_carry_direct_connector_field(self):
        merged = ConnectorRegistry.get_all_connectors(tech_repository=_FakeTechRepo([]))
        crowdstrike = next(c for c in merged if c['integration_type'] == 'crowdstrike')
        splunk = next(c for c in merged if c['integration_type'] == 'splunk')
        assert crowdstrike['direct_connector'] == 'crowdstrike'
        assert splunk['direct_connector'] is None

    def test_repository_failure_does_not_break_listing(self):
        class BoomRepo:
            def list_technologies(self):
                raise RuntimeError('db unavailable')

        merged = ConnectorRegistry.get_all_connectors(tech_repository=BoomRepo())
        types = {c['integration_type'] for c in merged}
        assert 'crowdstrike' in types
        assert 'splunk' in types
