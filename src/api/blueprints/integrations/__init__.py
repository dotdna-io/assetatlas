# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration Blueprints.

Flask blueprints for managing data-source integrations, organised by
domain area.

Available blueprints:
- connectors: Connector discovery
- integrations: Integration CRUD and operations
- schedules: Schedule management
- batches: Batch processing and mapping
- asset_generation: Asset generation triggers
- preview: Pipeline preview / dry-run endpoints
- integration_groups: Integration grouping
"""

# Import all blueprints
from .connectors import connectors_bp
from .integrations import integrations_bp
from .schedules import schedules_bp
from .batches import batches_bp
from .asset_generation import asset_generation_bp
from .preview import preview_bp
from .integration_groups import integration_groups_bp

__all__ = [
    'connectors_bp',
    'integrations_bp',
    'schedules_bp',
    'batches_bp',
    'asset_generation_bp',
    'preview_bp',
    'integration_groups_bp',
]
