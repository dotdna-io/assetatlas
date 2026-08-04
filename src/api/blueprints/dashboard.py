# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Dashboard Blueprint

Handles all dashboard analytics endpoints including summary stats, inventory,
visualization data, data sources, reliability metrics, and alerts.

Endpoints (7 total):
- GET /api/dashboard/summary - Comprehensive dashboard summary
- GET /api/dashboard/inventory - Inventory summary for top-left panel
- GET /api/dashboard/flowers - Visualization data for flower charts with filtering
- GET /api/dashboard/wheels-data - V2 visualization data with any-to-any relationships
- GET /api/dashboard/data-sources - Data source health and statistics
- GET /api/dashboard/reliability - Reliability statistics for top-right panel
- GET /api/dashboard/alerts - Active alerts summary for dashboard panel
"""

import logging
from flask import Blueprint, request, current_app
from flask_jwt_extended import jwt_required

from src.api.middleware.error_handler import handle_errors
from src.api.utils.responses import response_wrapper
from src.api.utils.caching import cached_dashboard

logger = logging.getLogger(__name__)

# Create blueprint
dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/api/dashboard')


# =============================================================================
# Dashboard Endpoints
# =============================================================================

@dashboard_bp.route('/summary', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
@cached_dashboard(timeout=60)  # Cache for 1 minute
def get_dashboard_summary():
    """
    Get comprehensive dashboard summary

    Returns:
        JSON with totalAssets, categoryBreakdown, dataQuality, visibility, reliability
    """
    dashboard_service = current_app.dashboard_service

    logger.info("GET /api/dashboard/summary")
    summary = dashboard_service.get_summary()

    return summary


@dashboard_bp.route('/inventory', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
@cached_dashboard(timeout=60)  # Cache for 1 minute
def get_dashboard_inventory():
    """
    Get inventory summary for top-left panel

    Returns:
        JSON with totalAssets, changeToday (added/removed), categoryGauges
    """
    dashboard_service = current_app.dashboard_service

    logger.info("GET /api/dashboard/inventory")
    inventory = dashboard_service.get_inventory_summary()

    return inventory


@dashboard_bp.route('/flowers', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
@cached_dashboard(timeout=60)  # Cache for 1 minute
def get_dashboard_flowers():
    """
    Get visualization data for flower charts with optional filtering

    Query Parameters:
        category (str, optional): Filter by category name
        subcategory (str, optional): Filter by subcategory name
        os (str, optional): Filter by OS name
        integration_id (str, optional): Filter by integration UUID

    Returns:
        JSON with totalAssets, categories, operatingSystems, subcategories, dataSources
    """
    dashboard_service = current_app.dashboard_service

    # Extract query parameters
    filters = {}
    if request.args.get('category'):
        filters['category'] = request.args.get('category')
    if request.args.get('subcategory'):
        filters['subcategory'] = request.args.get('subcategory')
    if request.args.get('os'):
        filters['os'] = request.args.get('os')
    if request.args.get('integration_id'):
        filters['integration_id'] = request.args.get('integration_id')

    logger.info(f"GET /api/dashboard/flowers - filters: {filters}")
    flower_data = dashboard_service.get_flower_data(filters if filters else None)

    return flower_data


@dashboard_bp.route('/wheels-data', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
@cached_dashboard(timeout=60)  # Cache for 1 minute
def get_dashboard_wheels_data():
    """
    Get V2 visualization data with direct any-to-any relationships.

    Unlike /flowers endpoint, this returns data where every entity
    has direct relationship arrays to all other entity types.

    Returns:
        JSON with:
        - totalAssets: Total asset count
        - categories: Array of categories with subcategories[], sources[], os[] arrays
        - subcategories: Array with categories[], sources[], os[] arrays
        - operatingSystems: Array with categories[], subcategories[], sources[] arrays
        - dataSources: Array with categories[], subcategories[], os[] arrays
    """
    dashboard_service = current_app.dashboard_service

    logger.info("GET /api/dashboard/wheels-data")
    wheels_data = dashboard_service.get_flower_data_v2()

    return wheels_data


@dashboard_bp.route('/data-sources', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
@cached_dashboard(timeout=60)  # Cache for 1 minute
def get_data_sources():
    """
    Get data source health and statistics

    Returns:
        JSON with count and sources array
    """
    dashboard_service = current_app.dashboard_service

    view = request.args.get('view', 'individual')
    if view not in ('individual', 'grouped'):
        view = 'individual'

    logger.info(f"GET /api/dashboard/data-sources (view={view})")
    sources = dashboard_service.get_data_sources(view=view)

    return sources


@dashboard_bp.route('/reliability', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
@cached_dashboard(timeout=60)  # Cache for 1 minute
def get_dashboard_reliability():
    """
    Get reliability statistics for top-right panel
    Returns most/least reliable sources and categories by average confidence

    Returns:
        JSON with mostReliableSource, leastReliableSource,
        mostReliableCategory, leastReliableCategory
    """
    dashboard_service = current_app.dashboard_service

    logger.info("GET /api/dashboard/reliability")
    reliability = dashboard_service.get_reliability()

    return reliability


@dashboard_bp.route('/alerts', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
@cached_dashboard(timeout=60)
def get_dashboard_alerts():
    """
    Get active alerts summary for dashboard panel.

    Returns top 5 active alerts sorted by severity (critical first) then recency,
    plus total count and severity breakdown for filter badges.
    """
    alerts_service = current_app.alerts_service

    logger.info("GET /api/dashboard/alerts")

    # Reuse existing alerts service — active alerts only, top 5
    result = alerts_service.get_alerts_response(
        status='active',
        per_page=5,
        page=1,
    )

    # Reshape for dashboard: extract what the panel needs
    summary = result.get('summary', {})
    severity_counts = summary.get('by_severity', {})

    # Transform assets_by_category dict → categories array for frontend
    alerts = result['alerts']
    for alert in alerts:
        by_cat = alert.pop('assets_by_category', None) or {}
        alert['categories'] = [
            {'category': k, 'count': v} for k, v in by_cat.items()
        ]

    return {
        'count': result['pagination']['total_items'],
        'summary': {
            'critical': severity_counts.get('critical', 0),
            'high': severity_counts.get('high', 0),
            'medium': severity_counts.get('medium', 0),
            'low': severity_counts.get('low', 0),
        },
        'alerts': alerts,
    }


@dashboard_bp.route('/posture', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
@cached_dashboard(timeout=60)
def get_dashboard_posture():
    """
    Get posture tier breakdown + weekly change deltas for the Posture Flow widget.

    Returns:
        JSON with:
        - posture: {asset_category: {secured, monitored, discovered}} for the seven
          canonical asset categories.
        - changes: {tier: {delta, period}} — delta is null until a 7+ day-old
          snapshot exists.
    """
    dashboard_service = current_app.dashboard_service

    logger.info("GET /api/dashboard/posture")
    return dashboard_service.get_posture_data()


@dashboard_bp.route('/confidence-breakdown', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
@cached_dashboard(timeout=60)
def get_dashboard_confidence_breakdown():
    """
    Per-category asset counts split by confidence band (confirmed /
    under_covered / shadows) for the confidence chevrons.

    Returns:
        JSON with breakdown: {category: {confirmed, under_covered, shadows}}
    """
    logger.info("GET /api/dashboard/confidence-breakdown")
    return current_app.dashboard_service.get_confidence_breakdown()


@dashboard_bp.route('/posture-trend', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
@cached_dashboard(timeout=60)
def get_dashboard_posture_trend():
    """
    Get daily secured/monitored/discovered tier totals for the Posture Flow
    chevron background bar charts.

    Query Parameters:
        days (int, optional): Look-back window in days (1-365, default 30).
            Out-of-range values are clamped.

    Returns:
        JSON with days and series: {secured: [...], monitored: [...], discovered: [...]}
    """
    dashboard_service = current_app.dashboard_service

    days = request.args.get('days', default=30, type=int)
    if days is None:
        days = 30
    days = max(1, min(365, days))

    logger.info(f"GET /api/dashboard/posture-trend - days: {days}")
    return dashboard_service.get_posture_trend(days=days)


@dashboard_bp.route('/asset-timeline', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
@cached_dashboard(timeout=60)
def get_dashboard_asset_timeline():
    """
    Get daily added/removed asset counts across all data sources for the
    homepage "Added & removed assets" butterfly chart.

    Query Parameters:
        days (int, optional): Look-back window in days (1-365, default 30).
            Out-of-range values are clamped.

    Returns:
        JSON with range, granularity and data: [{label, added, returned, removed}]
    """
    dashboard_service = current_app.dashboard_service

    days = request.args.get('days', default=30, type=int)
    if days is None:
        days = 30
    days = max(1, min(365, days))

    logger.info(f"GET /api/dashboard/asset-timeline - days: {days}")
    return dashboard_service.get_asset_timeline(days=days)


@dashboard_bp.route('/coverage-trend', methods=['GET'])
@jwt_required()
@handle_errors
@response_wrapper
@cached_dashboard(timeout=60)
def get_dashboard_coverage_trend():
    """
    Get daily posture-coverage counts (with/without) for the homepage Trend
    card's posture-coverage metrics.

    Query Parameters:
        metric (str, optional): one of srv_edr, ws_edr, mobile_mdm, vuln
            (default srv_edr). Unknown metrics return empty data.
        days (int, optional): Look-back window in days (1-365, default 30).
            Out-of-range values are clamped.

    Returns:
        JSON with metric, range, granularity and data: [{label, added, removed}]
    """
    dashboard_service = current_app.dashboard_service

    metric = request.args.get('metric', default='srv_edr', type=str)
    days = request.args.get('days', default=30, type=int)
    if days is None:
        days = 30
    days = max(1, min(365, days))

    logger.info(f"GET /api/dashboard/coverage-trend - metric: {metric}, days: {days}")
    return dashboard_service.get_coverage_trend(metric=metric, days=days)
