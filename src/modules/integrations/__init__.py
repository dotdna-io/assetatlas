# SPDX-License-Identifier: AGPL-3.0-or-later
"""
AssetAtlas Integrations Module

Provides a unified framework for data source connectors.

Usage:
    from src.modules.integrations import ConnectorRegistry, discover_connectors

    # Discover all connectors
    discover_connectors()

    # List available connectors
    connectors = ConnectorRegistry.get_all_connectors()

    # Create connector instance
    connector = ConnectorRegistry.create_connector(
        integration_type="azure_ad",
        config={...}
    )

    # Test connection
    result = connector.test_connection()

    # Fetch data
    for record in connector.fetch_data(batch_id="123"):
        process(record)
"""

from src.modules.integrations.registry import ConnectorRegistry, discover_connectors
from src.modules.integrations.base.base_connector import BaseConnector

__all__ = [
    'ConnectorRegistry',
    'discover_connectors',
    'BaseConnector'
]
