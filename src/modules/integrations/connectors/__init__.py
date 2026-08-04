# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Data Source Connectors

This package contains all connector implementations.
Each connector should be in its own file named *_connector.py

Connectors automatically register themselves using the @ConnectorRegistry.register decorator.
"""

# Connectors will be imported by discover_connectors() function
__all__ = []
