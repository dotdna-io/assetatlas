# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Dashboard Service
Business logic layer for dashboard data, including default colors and formatting.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import logging
import colorsys
from src.api.modules.settings.settings_schema import get_confidence_thresholds

logger = logging.getLogger(__name__)


class DashboardService:
    """Service for dashboard business logic and data transformation"""

    # Base colors for each wheel type (hue, saturation, lightness ranges)
    COLOR_SCHEMES = {
        'sources': {'hue': 140, 'name': 'green'},      # Green for data sources
        'categories': {'hue': 220, 'name': 'blue'},    # Blue for categories
        'subcategories': {'hue': 0, 'name': 'gray'},   # Gray for subcategories
        'os': {'hue': 30, 'name': 'orange'}            # Orange for OS (adjusted to 30 for brighter orange)
    }

    # Default color mappings (can be overridden later with database config)
    DEFAULT_COLORS = {
        'categories': {
            'Server': '#1e40af',
            'Workstation': '#2563eb',
            'Network': '#3b82f6',
            'Mobile': '#60a5fa',
            'OT/IoT': '#93c5fd',
            'Miscellaneous': '#bfdbfe',
            'Uncategorized': '#dbeafe'
        },
        'os': {
            'Windows 11': '#ea580c',
            'Windows 10': '#f97316',
            'Windows Server': '#c2410c',
            'macOS': '#fb923c',
            'Linux': '#fdba74',
            'Ubuntu': '#fed7aa',
            'iOS': '#ffedd5',
            'Android': '#fef3c7',
            'Unknown': '#94a3b8'
        },
        'subcategories': {
            'Servers': '#374151',
            'Workstations': '#4b5563',
            'Laptops': '#6b7280',
            'Databases': '#9ca3af',
            'Applications': '#d1d5db'
        }
    }

    # Posture flow: canonical asset categories and tier labels
    POSTURE_CANONICAL_CATEGORIES = (
        "server", "workstation", "mobile", "network", "iot", "ot", "miscellaneous",
    )
    POSTURE_TIERS = ("secured", "monitored", "discovered")

    # Coverage-trend metrics: each is a (posture control × asset-category) slice
    # historized in posture_coverage_snapshots. Extensible — add an entry to
    # expose a new metric; the snapshot already stores every control × category.
    COVERAGE_METRICS = {
        'srv_edr':    {'control': 'secured',                'categories': ['server']},
        'ws_edr':     {'control': 'secured',                'categories': ['workstation']},
        'mobile_mdm': {'control': 'device_management',      'categories': ['mobile']},
        'vuln':       {'control': 'vulnerability_scanning', 'categories': ['server', 'workstation', 'network']},
    }

    def __init__(self, repository, settings_manager=None):
        """
        Initialize service with repository

        Args:
            repository: DashboardRepository instance
            settings_manager: Optional SettingsManager for reading configured thresholds
        """
        self.repo = repository
        self.settings_manager = settings_manager

    @staticmethod
    def generate_color_palette(count: int, color_type: str) -> List[str]:
        """
        Generate a palette of distinct colors for a given type

        Args:
            count: Number of colors to generate
            color_type: Type of color scheme (sources, categories, subcategories, os)

        Returns:
            List of hex color codes
        """
        if count == 0:
            return []

        scheme = DashboardService.COLOR_SCHEMES.get(color_type, {'hue': 200, 'name': 'default'})
        base_hue = scheme['hue']

        colors = []

        if color_type == 'subcategories':
            # Gray scale: truly neutral (saturation = 0)
            for i in range(count):
                # Range from dark to light gray
                lightness = 0.25 + (i / max(count - 1, 1)) * 0.60  # 25% to 85%
                saturation = 0.00  # Zero saturation for true gray

                # Convert HSL to RGB
                r, g, b = colorsys.hls_to_rgb(0, lightness, saturation)
                hex_color = '#{:02x}{:02x}{:02x}'.format(
                    int(r * 255), int(g * 255), int(b * 255)
                )
                colors.append(hex_color)
        else:
            # For colored schemes: vary both saturation and lightness
            for i in range(count):
                if color_type == 'os':
                    # Orange: keep higher saturation for vibrant colors
                    lightness = 0.40 + (i / max(count - 1, 1)) * 0.35  # 40% to 75%
                    saturation = 1.00 - (i / max(count - 1, 1)) * 0.30  # 100% to 70%
                else:
                    # Other colors
                    lightness = 0.35 + (i / max(count - 1, 1)) * 0.35  # 35% to 70%
                    saturation = 0.90 - (i / max(count - 1, 1)) * 0.40  # 90% to 50%

                # Convert HSL to RGB
                r, g, b = colorsys.hls_to_rgb(base_hue / 360, lightness, saturation)
                hex_color = '#{:02x}{:02x}{:02x}'.format(
                    int(r * 255), int(g * 255), int(b * 255)
                )
                colors.append(hex_color)

        return colors

    def get_inventory_summary(self) -> Dict[str, Any]:
        """
        Get inventory summary for top-left panel

        Returns:
            Dict with totalAssets, changeToday, categoryGauges
        """
        try:
            medium, high = get_confidence_thresholds(self.settings_manager)

            # Get total assets count
            total = self.repo.get_total_assets_count()

            # Get new/removed from latest generation run
            chronicle_changes = self.repo.get_chronicle_changes_for_latest_run()

            # Get category confidence averages
            category_confidence = self.repo.get_category_confidence_from_view(medium=medium, high=high)

            # Calculate total percentage
            total_count = sum(cat['count'] for cat in category_confidence)

            # Format category gauges
            gauges = []
            for cat in category_confidence:
                percentage = round((cat['count'] / total_count * 100), 1) if total_count > 0 else 0
                avg_confidence_pct = round(float(cat['avg_confidence']) * 100, 1)  # Convert 0-1 to 0-100

                gauges.append({
                    'category': cat['category'],
                    'count': cat['count'],
                    'percentage': percentage,
                    'avgConfidence': float(avg_confidence_pct),  # Ensure it's a float
                    'confidenceLevel': cat['confidence_level'],
                    'highCount': cat.get('high_count', 0),
                    'mediumCount': cat.get('medium_count', 0),
                    'lowCount': cat.get('low_count', 0)
                })

            # Get last updated timestamp
            last_updated = self.repo.get_last_updated_time()

            # Build 7-day asset count trend (working backwards from current total)
            trend_data = self.repo.get_asset_count_trend()
            asset_trend = self._build_asset_trend(total, trend_data)

            return {
                'totalAssets': total,
                'changeToday': {
                    'added': chronicle_changes['added_count'],
                    'removed': chronicle_changes['removed_count']
                },
                'categoryGauges': gauges,
                'lastUpdated': last_updated.isoformat() if last_updated else None,
                'assetTrend': asset_trend
            }

        except Exception as e:
            logger.error(f"Error getting inventory summary: {e}")
            raise

    def get_summary(self) -> Dict[str, Any]:
        """
        Get comprehensive dashboard summary

        Returns:
            Dict with totalAssets, changeToday, categoryBreakdown,
            dataQuality, visibility, reliability, alerts
        """
        try:
            medium, high = get_confidence_thresholds(self.settings_manager)

            # Try materialized view first for pre-computed summary data
            view_data = self.repo.get_summary_from_view()
            if view_data:
                total = view_data['total_assets']
                added_today = view_data['assets_added_today']
                categories = view_data['category_breakdown'] or []
                confidence_dist = view_data['confidence_distribution'] or []
            else:
                # Fall back to live queries
                total = self.repo.get_total_assets_count()
                added_today = self.repo.get_assets_added_today()
                categories = self.repo.get_category_breakdown()
                confidence_dist = self.repo.get_confidence_distribution(medium=medium, high=high)

            # Use grouped view: groups + ungrouped integrations as logical sources
            integrations = self.repo.get_integration_stats_grouped(medium=medium, high=high)
            logical_source_count = len(integrations)

            # Add colors to categories (dynamically generated)
            category_colors = self.generate_color_palette(len(categories), 'categories')
            for idx, cat in enumerate(categories):
                cat['color'] = category_colors[idx] if idx < len(category_colors) else '#60a5fa'

            # Calculate data quality rating
            quality_rating = self._calculate_quality_rating(confidence_dist)

            # Calculate visibility level using logical source count
            visibility = self._calculate_visibility_level(
                confidence_dist,
                logical_source_count
            )

            # Calculate reliability stats
            reliability = self._calculate_reliability_stats(
                integrations,
                categories
            )

            # Build confidence distribution dict
            conf_dist_dict = {
                item['level']: float(item['percentage'])
                for item in confidence_dist
            }

            # Calculate high/medium vs low confidence counts
            high_medium_pct = conf_dist_dict.get('high', 0) + conf_dist_dict.get('medium', 0)
            low_pct = conf_dist_dict.get('low', 0)
            high_medium_count = int(total * high_medium_pct / 100)
            low_count = int(total * low_pct / 100)

            # Source coverage: distinct sources per category
            source_coverage = self.repo.get_source_coverage()

            # Completeness: average data_completeness from confidence_breakdown
            completeness = self.repo.get_completeness()

            # Last updated: most recent event in asset_chronicle
            last_updated = self.repo.get_last_updated_time()

            return {
                'totalAssets': total,
                'changeToday': {
                    'added': added_today,
                    'removed': 0  # TODO: Calculate from last_seen
                },
                'categoryBreakdown': categories,
                'dataQuality': {
                    'rating': quality_rating,
                    'confidenceDistribution': conf_dist_dict
                },
                'visibility': visibility,
                'reliability': reliability,
                'assetsByConfidence': {
                    'highMedium': high_medium_count,
                    'low': low_count
                },
                'sourceCoverage': source_coverage,
                'completeness': completeness,
                'lastUpdated': last_updated.isoformat() if last_updated else None,
                'alerts': {
                    'total': 0,
                    'critical': 0,
                    'high': 0,
                    'medium': 0,
                    'low': 0
                }
            }

        except Exception as e:
            logger.error(f"Error getting dashboard summary: {e}")
            raise

    def get_flower_data(self, filters: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Get visualization data for flower charts

        Args:
            filters: Dict with category, subcategory, os, integration_id

        Returns:
            Dict with totalAssets, categories, operatingSystems,
            subcategories, dataSources
        """
        try:
            medium, high = get_confidence_thresholds(self.settings_manager)

            # Extract filter values
            filters = filters or {}
            category = filters.get('category')
            subcategory = filters.get('subcategory')
            os = filters.get('os')
            integration_id = filters.get('integration_id')

            # Get filtered data from repository
            filtered = self.repo.get_filtered_flower_data(
                category=category,
                subcategory=subcategory,
                os=os,
                integration_id=integration_id
            )

            # Add colors to categories (dynamically generated)
            categories = filtered.get('categories') or []
            category_colors = self.generate_color_palette(len(categories), 'categories')
            for idx, cat in enumerate(categories):
                cat['id'] = hash(cat['name']) % 10000  # Generate simple ID
                cat['color'] = category_colors[idx] if idx < len(category_colors) else '#60a5fa'

            # Add colors to OS (dynamically generated)
            operating_systems = filtered.get('operating_systems') or []
            os_colors = self.generate_color_palette(len(operating_systems), 'os')
            for idx, os in enumerate(operating_systems):
                os['id'] = hash(os['name']) % 10000
                os['color'] = os_colors[idx] if idx < len(os_colors) else '#fb923c'

            # Get integration stats (data sources) - use grouped view to collapse groups
            data_sources = self.repo.get_integration_stats_grouped(medium=medium, high=high)
            source_colors = self.generate_color_palette(len(data_sources), 'sources')
            for idx, source in enumerate(data_sources):
                source['id'] = source['id']  # Already a UUID string
                source['color'] = source_colors[idx] if idx < len(source_colors) else '#10b981'
                source['icon'] = f"icons/{source['name'].lower().replace(' ', '_')}.png"
                source['confidence'] = int(source.get('avg_confidence') or 0)

                # Calculate status
                hours = source.get('hours_since_sync')
                if hours is None:
                    source['status'] = 'unknown'
                    source['lastSync'] = 'Never'
                elif hours < 24:
                    source['status'] = 'healthy'
                    source['lastSync'] = self._format_time_ago(hours)
                elif hours < 48:
                    source['status'] = 'warning'
                    source['lastSync'] = self._format_time_ago(hours)
                else:
                    source['status'] = 'error'
                    source['lastSync'] = self._format_time_ago(hours)

                source['lastSyncHours'] = hours

                # Build confidence distribution
                source['confidenceDistribution'] = {
                    'high': source.get('high_conf_count', 0),
                    'medium': source.get('medium_conf_count', 0),
                    'low': source.get('low_conf_count', 0)
                }

            # Create OS name to ID mapping for relationship conversion
            os_name_to_id = {os['name']: os['id'] for os in operating_systems}

            # Add colors to subcategories and convert relationships to IDs
            subcategories = filtered.get('subcategories') or []
            subcategory_colors = self.generate_color_palette(len(subcategories), 'subcategories')
            for idx, sub in enumerate(subcategories):
                sub['id'] = hash(sub['name']) % 10000
                sub['categoryId'] = hash(sub.get('category', '')) % 10000
                sub['color'] = subcategory_colors[idx] if idx < len(subcategory_colors) else '#6b7280'

                # Convert OS names to IDs for relationship mapping
                os_names = sub.get('os', []) or []
                sub['os'] = [os_name_to_id.get(name) for name in os_names if name in os_name_to_id]

                # Sources are already UUID strings, keep as-is
                # Ensure sources is always a list
                if not sub.get('sources'):
                    sub['sources'] = []

            return {
                'totalAssets': filtered.get('total_count', 0),
                'categories': categories,
                'operatingSystems': operating_systems,
                'subcategories': subcategories,
                'dataSources': data_sources
            }

        except Exception as e:
            logger.error(f"Error getting flower data: {e}")
            raise

    def get_data_sources(self, view: str = 'individual', filters: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Get data source health and statistics

        Args:
            view: 'individual' for per-integration stats, 'grouped' for logical sources
            filters: Optional filters (for future use)

        Returns:
            Dict with count and sources array
        """
        try:
            medium, high = get_confidence_thresholds(self.settings_manager)
            if view == 'grouped':
                integrations = self.repo.get_integration_stats_grouped(medium=medium, high=high)
            else:
                integrations = self.repo.get_integration_stats_from_view(medium=medium, high=high)

            # Per-source category mix for the dashboard mini-bars, keyed by
            # source name (matches asset_providers.data_source). Non-canonical
            # categories roll into 'miscellaneous'.
            canonical = set(self.POSTURE_CANONICAL_CATEGORIES)
            category_by_source: Dict[str, Dict[str, int]] = {}
            for row in self.repo.get_provider_category_breakdown():
                src_name = row.get('source')
                if not src_name:
                    continue
                cat = row['category'] if row['category'] in canonical else 'miscellaneous'
                bucket = category_by_source.setdefault(
                    src_name, {c: 0 for c in self.POSTURE_CANONICAL_CATEGORIES}
                )
                bucket[cat] += int(row['n'] or 0)

            sources = []
            for integration in integrations:
                # Calculate status
                hours = integration.get('hours_since_sync')
                if hours is None:
                    status = 'unknown'
                    last_sync_display = 'Never'
                elif hours < 24:
                    status = 'healthy'
                    last_sync_display = self._format_time_ago(hours)
                elif hours < 48:
                    status = 'warning'
                    last_sync_display = self._format_time_ago(hours)
                else:
                    status = 'error'
                    last_sync_display = self._format_time_ago(hours)

                # Use logo from database if available
                logo = integration.get('logo')
                icon = f"/logos/{logo}" if logo else None

                source_type = integration.get('source_type', 'integration')
                integration_type = integration.get('integration_type')

                run_hours = integration.get('hours_since_run')
                if run_hours is None:
                    last_run_display = 'Never'
                else:
                    last_run_display = self._format_time_ago(run_hours)
                last_run_at = integration.get('last_run_at')

                sources.append({
                    'id': integration['id'],
                    'name': integration['name'],
                    'source_type': source_type,
                    'integration_type': integration_type,
                    'logo': icon,  # Logo path from database (e.g., /logos/intune.png)
                    'assetCount': integration['asset_count'] or 0,
                    'lastSync': integration['last_sync_at'].isoformat() if integration['last_sync_at'] else None,
                    'lastSyncHours': hours,
                    'lastSyncDisplay': last_sync_display,
                    'lastRun': last_run_at.isoformat() if last_run_at else None,
                    'lastRunHours': run_hours,
                    'lastRunDisplay': last_run_display,
                    'status': status,
                    'confidence': int(integration.get('avg_confidence') or 0),
                    'confidenceDistribution': {
                        'high': integration.get('high_conf_count', 0),
                        'medium': integration.get('medium_conf_count', 0),
                        'low': integration.get('low_conf_count', 0)
                    },
                    'categoryDistribution': category_by_source.get(
                        integration['name'],
                        {c: 0 for c in self.POSTURE_CANONICAL_CATEGORIES},
                    ),
                })

            return {
                'count': len(sources),
                'sources': sources
            }

        except Exception as e:
            logger.error(f"Error getting data sources: {e}")
            raise

    def get_reliability(self) -> Dict[str, Any]:
        """
        Get reliability statistics for top-right panel

        Returns:
            Dict with most/least reliable sources and categories
        """
        try:
            medium, high = get_confidence_thresholds(self.settings_manager)

            # Get integration stats for source reliability (grouped view)
            integrations = self.repo.get_integration_stats_grouped(medium=medium, high=high)

            # Calculate reliability using existing helper (which now uses real category data)
            reliability = self._calculate_reliability_stats(integrations, [])

            return reliability

        except Exception as e:
            logger.error(f"Error getting reliability stats: {e}")
            raise

    def get_posture_data(self) -> Dict[str, Any]:
        """Per-asset-category tier breakdown + week-over-week deltas."""
        rows = self.repo.get_posture_tier_breakdown()
        posture = self._pivot_posture_rows(rows)
        totals = {tier: sum(c[tier] for c in posture.values()) for tier in self.POSTURE_TIERS}

        snapshot = self.repo.get_posture_snapshot_at_or_before(days_ago=7)
        changes = self._compute_posture_changes(totals, snapshot)

        return {"posture": posture, "changes": changes}

    CONFIDENCE_BANDS = ("confirmed", "under_covered", "shadows")

    def get_confidence_breakdown(self) -> Dict[str, Any]:
        """Per-category asset counts split by confidence band, for the
        confidence chevrons. Non-canonical categories roll into 'miscellaneous'.
        """
        medium, high = get_confidence_thresholds(self.settings_manager)
        canonical = set(self.POSTURE_CANONICAL_CATEGORIES)
        out: Dict[str, Dict[str, int]] = {
            cat: {b: 0 for b in self.CONFIDENCE_BANDS}
            for cat in self.POSTURE_CANONICAL_CATEGORIES
        }
        for row in self.repo.get_confidence_breakdown(medium=medium, high=high):
            cat = row["category"] if row["category"] in canonical else "miscellaneous"
            band = row["band"]
            if band in out[cat]:
                out[cat][band] += int(row["n"] or 0)
        return {"breakdown": out}

    def get_posture_trend(self, days: int = 30) -> Dict[str, Any]:
        """Daily per-tier totals for the chevron background bar charts.

        The latest point is overridden with the live current tier totals: the
        most recent snapshot can lag a just-applied rule change, and the bars
        share a chevron with the live tier count, so their right edge must
        agree with that number.
        """
        rows = self.repo.get_posture_tier_trend(days=days)
        series = {tier: [int(r.get(tier) or 0) for r in rows] for tier in self.POSTURE_TIERS}

        if rows:
            breakdown = self.repo.get_posture_tier_breakdown()
            current = {tier: 0 for tier in self.POSTURE_TIERS}
            for row in breakdown:
                tier = row["tier"]
                if tier in current:
                    current[tier] += int(row["n"])
            for tier in self.POSTURE_TIERS:
                series[tier][-1] = current[tier]

        return {"days": days, "series": series}

    def get_asset_timeline(self, days: int = 30) -> Dict[str, Any]:
        """Daily added/removed asset counts across all data sources.

        Same shape as IntegrationService.get_timeline so the homepage card
        can reuse the data-source butterfly chart unchanged.
        """
        data = self.repo.get_asset_timeline(days=days)
        return {
            'range': f'{days}d',
            'granularity': 'daily',
            'data': data,
        }

    def get_coverage_trend(self, metric: str, days: int = 30) -> Dict[str, Any]:
        """Daily covered/uncovered counts for a posture-coverage metric.

        Mirrors get_asset_timeline's envelope. The latest point is overridden
        with the live current coverage (same approach as get_posture_trend) so
        the card shows the correct current value even before snapshots accrue.
        Unknown metrics return an empty series.
        """
        spec = self.COVERAGE_METRICS.get(metric)
        if spec is None:
            return {'metric': metric, 'range': f'{days}d', 'granularity': 'daily', 'data': []}

        data = self.repo.get_coverage_trend(spec['control'], spec['categories'], days=days)
        if data:
            live = self.repo.get_coverage_current(spec['control'], spec['categories'])
            data[-1]['added'] = live['covered']
            data[-1]['removed'] = max(0, live['total'] - live['covered'])
        return {'metric': metric, 'range': f'{days}d', 'granularity': 'daily', 'data': data}

    def _pivot_posture_rows(self, rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
        """Pivot [{asset_category, tier, n}] -> {category: {secured, monitored, discovered}}.

        Always returns the seven canonical keys, with zeros for unseen ones. Asset
        categories outside the canonical set roll into 'miscellaneous'.
        """
        out: Dict[str, Dict[str, int]] = {
            cat: {"secured": 0, "monitored": 0, "discovered": 0}
            for cat in self.POSTURE_CANONICAL_CATEGORIES
        }
        canonical = set(self.POSTURE_CANONICAL_CATEGORIES)
        for row in rows:
            cat = row["asset_category"] if row["asset_category"] in canonical else "miscellaneous"
            tier = row["tier"]
            if tier in out[cat]:
                out[cat][tier] += int(row["n"])
        return out

    def _compute_posture_changes(
        self,
        totals: Dict[str, int],
        snapshot: Optional[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """delta = current_total - snapshot_total per tier; None when no snapshot."""
        if snapshot is None:
            return {tier: {"delta": None, "period": "week"} for tier in self.POSTURE_TIERS}
        return {
            tier: {"delta": totals[tier] - int(snapshot[tier]), "period": "week"}
            for tier in self.POSTURE_TIERS
        }

    def _calculate_quality_rating(self, confidence_dist: List[Dict]) -> str:
        """Calculate data quality rating from confidence distribution"""
        high_pct = next(
            (float(x['percentage']) for x in confidence_dist if x['level'] == 'high'),
            0
        )

        if high_pct >= 70:
            return 'Excellent'
        elif high_pct >= 50:
            return 'Good'
        elif high_pct >= 30:
            return 'Fair'
        else:
            return 'Poor'

    def _calculate_visibility_level(
        self,
        confidence_dist: List[Dict],
        integration_count: int
    ) -> Dict[str, Any]:
        """Calculate visibility level based on confidence and integration diversity"""
        high_pct = next((float(x['percentage']) for x in confidence_dist if x['level'] == 'high'), 0)
        medium_pct = next((float(x['percentage']) for x in confidence_dist if x['level'] == 'medium'), 0)

        avg_confidence = (high_pct * 0.8 + medium_pct * 0.5)
        integration_score = min(integration_count * 10, 50)
        final_score = avg_confidence * 0.7 + integration_score * 0.3

        if final_score >= 70:
            level = 'Advanced'
        elif final_score >= 40:
            level = 'Developing'
        else:
            level = 'Fragmented'

        return {
            'level': level,
            'score': round(final_score, 1)
        }

    def _calculate_reliability_stats(
        self,
        integrations: List[Dict],
        categories: List[Dict]
    ) -> Dict[str, Any]:
        """Calculate most/least reliable sources and categories"""
        medium, high = get_confidence_thresholds(self.settings_manager)

        # Source reliability from integrations
        integrations_with_conf = [i for i in integrations if i.get('avg_confidence')]
        if integrations_with_conf:
            most_reliable_source = max(integrations_with_conf, key=lambda x: x['avg_confidence'])
            least_reliable_source = min(integrations_with_conf, key=lambda x: x['avg_confidence'])
        else:
            most_reliable_source = {'name': 'N/A', 'avg_confidence': 0}
            least_reliable_source = {'name': 'N/A', 'avg_confidence': 0}

        # Category reliability from database (using real confidence averages)
        category_confidence = self.repo.get_category_confidence_from_view(medium=medium, high=high)
        categories_with_conf = [c for c in category_confidence if c.get('avg_confidence') is not None]

        if categories_with_conf:
            most_reliable_cat = max(categories_with_conf, key=lambda x: x['avg_confidence'])
            least_reliable_cat = min(categories_with_conf, key=lambda x: x['avg_confidence'])
            most_reliable_category = {
                'name': most_reliable_cat['category'],
                'score': round(float(most_reliable_cat['avg_confidence']) * 100, 1)
            }
            least_reliable_category = {
                'name': least_reliable_cat['category'],
                'score': round(float(least_reliable_cat['avg_confidence']) * 100, 1)
            }
        else:
            most_reliable_category = {'name': 'N/A', 'score': 0}
            least_reliable_category = {'name': 'N/A', 'score': 0}

        return {
            'mostReliableSource': {
                'name': most_reliable_source['name'],
                'score': float(most_reliable_source['avg_confidence'])
            },
            'leastReliableSource': {
                'name': least_reliable_source['name'],
                'score': float(least_reliable_source['avg_confidence'])
            },
            'mostReliableCategory': most_reliable_category,
            'leastReliableCategory': least_reliable_category
        }

    def _format_time_ago(self, hours: float) -> str:
        """Convert hours to human-readable format"""
        if hours is None:
            return 'Never'
        elif hours < 1:
            minutes = int(hours * 60)
            return f"{minutes} min ago"
        elif hours < 24:
            return f"{int(hours)} hours ago"
        else:
            days = int(hours / 24)
            return f"{days} days ago"

    def _build_asset_trend(self, current_total: int, trend_data: List[Dict]) -> List[Dict[str, Any]]:
        """
        Build 7-day asset count trend working backwards from current total.

        Args:
            current_total: Current total asset count
            trend_data: Daily created/removed counts from chronicle

        Returns:
            List of dicts with day (ISO date string) and count
        """
        if not trend_data:
            return []

        # Index net changes by date string
        net_by_day = {}
        for row in trend_data:
            day_str = str(row['day'])
            net_by_day[day_str] = int(row['created'] or 0) - int(row['removed'] or 0)

        # Build last 7 days working backwards from today
        today = datetime.now().date()
        days = [(today - timedelta(days=i)).isoformat() for i in range(7)]
        days.reverse()  # oldest first

        # Walk backwards: today's count is current_total, subtract net to get previous
        result = []
        count = current_total
        for day_str in reversed(days):
            result.append({'day': day_str, 'count': count})
            count -= net_by_day.get(day_str, 0)

        result.reverse()  # oldest first
        return result

    def get_flower_data_v2(self) -> Dict[str, Any]:
        """
        Get V2 visualization data with direct any-to-any relationships.
        Each entity has arrays linking to all other entity types.

        Returns:
            Dict with categories, subcategories, operatingSystems, dataSources
            Each entity includes dynamically generated colors
        """
        try:
            # Get data from repository
            data = self.repo.get_flower_data_v2()

            # Add colors to categories
            categories = data.get('categories', [])
            category_colors = self.generate_color_palette(len(categories), 'categories')
            for idx, cat in enumerate(categories):
                cat['color'] = category_colors[idx] if idx < len(category_colors) else '#60a5fa'

            # Add colors to subcategories
            subcategories = data.get('subcategories', [])
            subcategory_colors = self.generate_color_palette(len(subcategories), 'subcategories')
            for idx, sub in enumerate(subcategories):
                sub['color'] = subcategory_colors[idx] if idx < len(subcategory_colors) else '#6b7280'

            # Add colors to operating systems
            operating_systems = data.get('operatingSystems', [])
            os_colors = self.generate_color_palette(len(operating_systems), 'os')
            for idx, os in enumerate(operating_systems):
                os['color'] = os_colors[idx] if idx < len(os_colors) else '#fb923c'

            # Add colors to data sources
            data_sources = data.get('dataSources', [])
            source_colors = self.generate_color_palette(len(data_sources), 'sources')
            for idx, source in enumerate(data_sources):
                source['color'] = source_colors[idx] if idx < len(source_colors) else '#10b981'

            # Calculate total assets from categories
            total_assets = sum(cat['count'] for cat in categories)

            return {
                'totalAssets': total_assets,
                'categories': categories,
                'subcategories': subcategories,
                'operatingSystems': operating_systems,
                'dataSources': data_sources
            }

        except Exception as e:
            logger.error(f"Error getting V2 flower data: {e}")
            raise
