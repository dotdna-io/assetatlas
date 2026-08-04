# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Charts API Blueprint

Provides unified query endpoint for all chart types in the reporting dashboard.
Supports 14 chart types with flexible query structure for distribution, metric,
trend, comparison, and correlation queries.

Created: 2025-11-19
"""

from flask import Blueprint

# Create the charts blueprint
charts_bp = Blueprint('charts', __name__, url_prefix='/api/charts')

# Import routes to register them with the blueprint
from . import routes

__all__ = ['charts_bp']
