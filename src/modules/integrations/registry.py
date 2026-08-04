# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Connector Registry

Auto-discovery system for all coded data source connectors.
Connectors register themselves using the @ConnectorRegistry.register decorator.

Created: 2025-11-04
"""

from typing import Dict, List, Type, Optional
import logging

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """
    Registry for auto-discovering all coded connectors.

    Connectors register themselves using the decorator:

    @ConnectorRegistry.register
    class MyConnector(BaseConnector):
        integration_type = "my_connector"
        display_name = "My Connector"
        ...

    Then the registry can list all available connectors:

    available = ConnectorRegistry.get_all_connectors()
    """

    _connectors: Dict[str, Type] = {}

    @classmethod
    def register(cls, connector_class: Type) -> Type:
        """
        Decorator to register a connector class.

        Usage:
            @ConnectorRegistry.register
            class AzureADConnector(BaseConnector):
                integration_type = "azure_ad"
                ...

        Args:
            connector_class: Connector class to register

        Returns:
            The same connector class (allows use as decorator)

        Raises:
            ValueError: If integration_type is missing or duplicate
        """
        # Validate connector has integration_type
        if not hasattr(connector_class, 'integration_type') or not connector_class.integration_type:
            raise ValueError(
                f"Connector {connector_class.__name__} must define 'integration_type' class attribute"
            )

        integration_type = connector_class.integration_type

        # Check for duplicates
        if integration_type in cls._connectors:
            existing = cls._connectors[integration_type]
            logger.warning(
                f"Connector '{integration_type}' already registered by {existing.__name__}. "
                f"Overwriting with {connector_class.__name__}"
            )

        # Register connector
        cls._connectors[integration_type] = connector_class

        logger.debug(f"Registered connector: {integration_type} ({connector_class.__name__})")

        return connector_class

    @classmethod
    def get_connector_class(cls, integration_type: str) -> Optional[Type]:
        """
        Get connector class by integration_type.

        Args:
            integration_type: Integration type identifier (e.g., "azure_ad")

        Returns:
            Connector class or None if not found
        """
        return cls._connectors.get(integration_type)

    @classmethod
    def get_all_connectors(cls, tech_repository=None) -> List[Dict]:
        """
        Get all registered connectors with their metadata.

        Merges code-defined connectors with DB-seeded technology rows from the
        ``integration_technologies`` catalog. Code connectors win on
        ``integration_type`` conflicts.

        Args:
            tech_repository: Optional ``IntegrationTechnologyRepository`` for
                DB-defined tiles. If omitted the registry constructs one
                lazily; failures (e.g. DB unavailable in tests) are logged
                and skipped — code connectors are still returned.

        Each metadata dict includes a ``direct_connector`` field: the code
        connector key when the tile is backed by a Python class, otherwise
        the value from the DB row (NULL for SIEM-only tiles).
        """
        connectors: List[Dict] = []

        for integration_type, connector_class in cls._connectors.items():
            try:
                role = getattr(connector_class, 'connector_role', 'technology')
                metadata = {
                    'integration_type': integration_type,
                    'display_name': getattr(connector_class, 'display_name', integration_type),
                    'category': getattr(connector_class, 'category', 'Unknown'),
                    'description': getattr(connector_class, 'description', ''),
                    'logo_url': getattr(connector_class, 'logo_url', ''),
                    'connector_role': role,
                    'direct_connector': integration_type if role == 'technology' else None,
                    'config_schema': connector_class.get_config_schema()
                }
                connectors.append(metadata)

            except Exception as e:
                logger.error(
                    f"Error getting metadata for connector '{integration_type}': {e}"
                )
                continue

        seen_types = {c['integration_type'] for c in connectors}
        try:
            if tech_repository is None:
                from src.modules.integrations.repositories.technology_repository import (
                    IntegrationTechnologyRepository,
                )
                tech_repository = IntegrationTechnologyRepository()
            for tech in tech_repository.list_technologies():
                if tech['integration_type'] not in seen_types:
                    connectors.append(tech)
        except Exception as e:
            logger.warning(f"Skipping DB technology catalog: {e}")

        connectors.sort(key=lambda x: x['display_name'])
        return connectors

    @classmethod
    def get_connectors_by_category(cls) -> Dict[str, List[Dict]]:
        """
        Get connectors grouped by category.

        Returns:
            {
                'Identity Management': [
                    {'integration_type': 'azure_ad', ...},
                    ...
                ],
                'MDM': [...],
                ...
            }
        """
        connectors = cls.get_all_connectors()
        by_category = {}

        for connector in connectors:
            category = connector['category']
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(connector)

        return by_category

    @classmethod
    def create_connector(cls, integration_type: str, config: Dict) -> Optional[object]:
        """
        Factory method to create a connector instance.

        Args:
            integration_type: Integration type identifier
            config: Configuration dictionary

        Returns:
            Connector instance or None if type not found

        Raises:
            ValueError: If integration_type not found
        """
        connector_class = cls.get_connector_class(integration_type)

        if not connector_class:
            available = list(cls._connectors.keys())
            raise ValueError(
                f"Unknown integration_type: '{integration_type}'. "
                f"Available: {', '.join(available)}"
            )

        logger.info(f"Creating connector instance: {integration_type}")
        return connector_class(config)

    @classmethod
    def list_integration_types(cls) -> List[str]:
        """
        Get list of all registered integration types.

        Returns:
            List of integration type strings
        """
        return list(cls._connectors.keys())

    @classmethod
    def get_connector_count(cls) -> int:
        """
        Get total number of registered connectors.

        Returns:
            Count of registered connectors
        """
        return len(cls._connectors)

    @classmethod
    def is_registered(cls, integration_type: str) -> bool:
        """
        Check if an integration type is registered.

        Args:
            integration_type: Integration type to check

        Returns:
            True if registered, False otherwise
        """
        return integration_type in cls._connectors

    @classmethod
    def unregister(cls, integration_type: str) -> bool:
        """
        Unregister a connector (mainly for testing).

        Args:
            integration_type: Integration type to unregister

        Returns:
            True if connector was unregistered, False if not found
        """
        if integration_type in cls._connectors:
            del cls._connectors[integration_type]
            logger.debug(f"Unregistered connector: {integration_type}")
            return True
        return False

    @classmethod
    def clear(cls):
        """
        Clear all registered connectors (mainly for testing).
        """
        global _discovered
        cls._connectors.clear()
        _discovered = False
        logger.debug("Cleared all registered connectors")


# ===== Auto-discovery Helper =====

_discovered = False


def discover_connectors():
    """
    Auto-discover and import all connector modules.

    This function imports all Python files in the connectors/ directory,
    which triggers the @ConnectorRegistry.register decorators.

    Should be called on application startup. Idempotent — subsequent calls
    are no-ops (prevents redundant imports across Gunicorn workers).
    """
    global _discovered
    if _discovered:
        return

    import importlib
    from pathlib import Path

    # Get connectors directory
    connectors_dir = Path(__file__).parent / 'connectors'

    if not connectors_dir.exists():
        logger.warning(f"Connectors directory not found: {connectors_dir}")
        return

    logger.info(f"Discovering connectors in: {connectors_dir}")

    # Import all Python files in connectors/ directory
    for file_path in connectors_dir.glob('*_connector.py'):
        if file_path.name.startswith('_'):
            continue  # Skip __init__.py and other private files

        module_name = file_path.stem  # Filename without .py
        full_module_name = f'src.modules.integrations.connectors.{module_name}'

        try:
            importlib.import_module(full_module_name)
            logger.debug(f"Imported connector module: {module_name}")
        except Exception as e:
            logger.warning(f"Failed to import connector module '{module_name}': {e}")

    _discovered = True
    logger.info(f"Discovered {ConnectorRegistry.get_connector_count()} connectors")
