# SPDX-License-Identifier: AGPL-3.0-or-later
from src.modules.integrations.registry import ConnectorRegistry
from src.modules.integrations import discover_connectors


class TestConnectorRole:
    @classmethod
    def setup_class(cls):
        discover_connectors()

    def test_all_connectors_have_connector_role(self):
        connectors = ConnectorRegistry.get_all_connectors()
        for c in connectors:
            assert 'connector_role' in c, f"{c['integration_type']} missing connector_role"
            assert c['connector_role'] in ('technology', 'transport')

    def test_transport_connectors(self):
        connectors = ConnectorRegistry.get_all_connectors()
        transports = [c for c in connectors if c['connector_role'] == 'transport']
        transport_types = {c['integration_type'] for c in transports}
        assert 'splunk' in transport_types
        assert 'log_analytics' in transport_types
        assert 'sentinelone_siem' in transport_types
        assert 'databricks' in transport_types

    def test_technology_connectors_are_default(self):
        connectors = ConnectorRegistry.get_all_connectors()
        techs = [c for c in connectors if c['connector_role'] == 'technology']
        tech_types = {c['integration_type'] for c in techs}
        assert 'azure_ad' in tech_types
        assert 'crowdstrike' in tech_types
