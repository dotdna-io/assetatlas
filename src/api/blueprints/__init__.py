# SPDX-License-Identifier: AGPL-3.0-or-later
"""
API Blueprints

Flask blueprints for organizing API endpoints by domain.

Available blueprints:
- auth: Authentication and authorization (5 endpoints)
- tag_rules: Tag rules management (11 endpoints)
- assets: Asset operations (3 endpoints)
- dashboard: Dashboard and analytics (7 endpoints)
- health: Health check (1 endpoint)
- charts: Chart data queries (2 endpoints)
- search: Explorer search functionality (2 endpoints)
- suggestions: Semantic search suggestions (3 endpoints)
- integrations: Integration management (38 endpoints) - registered separately in factory
- posture_rules: Posture rules management (8 endpoints)
"""

from .auth import auth_bp
from .tag_rules import tag_rules_bp
from .assets import assets_bp
from .asset_edit import asset_edit_bp
from .dashboard import dashboard_bp
from .health import health_bp
from .charts import charts_bp
from .search import search_bp
from .suggestions import suggestions_bp
from .alerts import alerts_bp
from .posture_rules import posture_rules_bp
from .account import account_bp

__all__ = [
    'auth_bp',
    'tag_rules_bp',
    'assets_bp',
    'asset_edit_bp',
    'dashboard_bp',
    'health_bp',
    'charts_bp',
    'search_bp',
    'suggestions_bp',
    'alerts_bp',
    'posture_rules_bp',
    'account_bp',
]
