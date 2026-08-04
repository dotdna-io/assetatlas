# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Dashboard Repository
Handles all database queries for dashboard data using existing schema.
"""
from typing import Dict, List, Optional, Any
import logging
from src.api.modules.settings.settings_schema import DEFAULT_CONFIDENCE_THRESHOLDS

logger = logging.getLogger(__name__)


class DashboardRepository:
    """Repository for dashboard data queries"""

    def __init__(self, db_adapter):
        """
        Initialize repository with database adapter

        Args:
            db_adapter: PostgreSQLAdapter instance
        """
        self.db = db_adapter

    def get_total_assets_count(self) -> int:
        """
        Get total count of all assets

        Returns:
            int: Total asset count
        """
        query = """
            SELECT COUNT(*) as total
            FROM assets
        """
        result = self.db.execute_query(query)
        return result[0]['total'] if result else 0

    def get_assets_added_today(self) -> int:
        """
        Get count of assets first seen today

        Returns:
            int: Assets added today
        """
        query = """
            SELECT COUNT(*) as added_today
            FROM assets
            WHERE first_seen::date = CURRENT_DATE
        """
        result = self.db.execute_query(query)
        return result[0]['added_today'] if result else 0

    def get_category_breakdown(self) -> List[Dict[str, Any]]:
        """
        Get asset counts grouped by category with percentages

        Returns:
            List of dicts with category, count, percentage
        """
        query = """
            SELECT
                COALESCE(category, 'Uncategorized') as category,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) as percentage
            FROM assets
            GROUP BY category
            ORDER BY count DESC
        """
        return self.db.execute_query(query)

    def get_confidence_distribution(self, medium: float = None, high: float = None) -> List[Dict[str, Any]]:
        """
        Get asset counts grouped by confidence level

        Args:
            medium: Lower confidence threshold (defaults to the configured medium threshold)
            high: Upper confidence threshold (defaults to the configured high threshold)

        Returns:
            List of dicts with level (high/medium/low), count, percentage
        """
        if medium is None or high is None:
            medium, high = DEFAULT_CONFIDENCE_THRESHOLDS
        query = """
            SELECT
                level,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) as percentage
            FROM (
                SELECT
                    CASE
                        WHEN confidence >= %(cl_high)s THEN 'high'
                        WHEN confidence >= %(cl_med)s THEN 'medium'
                        ELSE 'low'
                    END as level
                FROM assets
            ) levels
            GROUP BY level
            ORDER BY
                CASE level
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                END
        """
        return self.db.execute_query(query, {"cl_high": high, "cl_med": medium})

    def get_confidence_breakdown(self, medium: float = None, high: float = None) -> List[Dict[str, Any]]:
        """Per-(category, confidence band) asset counts for the confidence
        chevrons. Bands are split by the configured high/medium thresholds:
        confirmed >= high threshold, under_covered >= medium threshold,
        shadows below medium threshold.

        Args:
            medium: Lower confidence threshold (defaults to the configured medium threshold)
            high: Upper confidence threshold (defaults to the configured high threshold)

        Returns:
            List of {category, band, n}.
        """
        if medium is None or high is None:
            medium, high = DEFAULT_CONFIDENCE_THRESHOLDS
        query = """
            SELECT
                COALESCE(NULLIF(LOWER(TRIM(category)), ''), 'miscellaneous') AS category,
                CASE
                    WHEN confidence >= %(cl_high)s THEN 'confirmed'
                    WHEN confidence >= %(cl_med)s THEN 'under_covered'
                    ELSE 'shadows'
                END AS band,
                COUNT(*) AS n
            FROM assets
            GROUP BY 1, 2
        """
        return self.db.execute_query(query, {"cl_high": high, "cl_med": medium}) or []

    def get_os_distribution(self) -> List[Dict[str, Any]]:
        """
        Get asset counts grouped by operating system

        Returns:
            List of dicts with os_name, count
        """
        query = """
            SELECT
                COALESCE(os_normalized, 'Unknown') as os_name,
                COUNT(*) as count
            FROM assets
            WHERE os_normalized IS NOT NULL
            GROUP BY os_normalized
            ORDER BY count DESC
        """
        return self.db.execute_query(query)

    def get_subcategories(self) -> List[Dict[str, Any]]:
        """
        Get asset counts grouped by subcategory with parent category

        Returns:
            List of dicts with subcategory, category, count
        """
        query = """
            SELECT
                COALESCE(subcategory, 'Other') as subcategory,
                COALESCE(category, 'Uncategorized') as category,
                COUNT(*) as count
            FROM assets
            WHERE subcategory IS NOT NULL
            GROUP BY subcategory, category
            ORDER BY count DESC
        """
        return self.db.execute_query(query)

    def get_integration_stats(self, medium: float = None, high: float = None) -> List[Dict[str, Any]]:
        """
        Get statistics for each integration (data source)

        Args:
            medium: Lower confidence threshold (defaults to the configured medium threshold)
            high: Upper confidence threshold (defaults to the configured high threshold)

        Returns:
            List of dicts with integration details, asset counts, confidence stats
        """
        if medium is None or high is None:
            medium, high = DEFAULT_CONFIDENCE_THRESHOLDS
        query = """
            SELECT
                i.id::text,
                i.name,
                i.integration_type,
                i.status,
                i.logo,
                last_sync.last_sync_at,
                EXTRACT(EPOCH FROM (NOW() - last_sync.last_sync_at)) / 3600 as hours_since_sync,
                last_run.last_run_at,
                EXTRACT(EPOCH FROM (NOW() - last_run.last_run_at)) / 3600 as hours_since_run,
                COUNT(DISTINCT ap.asset_id) as asset_count,
                ROUND(AVG(a.confidence) * 100, 0) as avg_confidence,
                COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= %(cl_high)s) as high_conf_count,
                COUNT(DISTINCT a.id) FILTER (WHERE a.confidence >= %(cl_med)s AND a.confidence < %(cl_high)s) as medium_conf_count,
                COUNT(DISTINCT a.id) FILTER (WHERE a.confidence < %(cl_med)s) as low_conf_count
            FROM integrations i
            LEFT JOIN LATERAL (
                SELECT MAX(ib.started_at) AS last_sync_at
                FROM ingestion_batches ib
                WHERE ib.integration_id = i.id
                  AND ib.asset_generation_status = 'completed'
            ) last_sync ON TRUE
            LEFT JOIN LATERAL (
                SELECT MAX(ib.started_at) AS last_run_at
                FROM ingestion_batches ib
                WHERE ib.integration_id = i.id
                  AND ib.status = 'completed'
            ) last_run ON TRUE
            LEFT JOIN asset_providers ap ON i.id = ap.integration_id
            LEFT JOIN assets a ON ap.asset_id = a.id
            WHERE i.status = 'enabled'
            GROUP BY i.id, i.name, i.integration_type, i.status, i.logo,
                     last_sync.last_sync_at, last_run.last_run_at
            ORDER BY asset_count DESC NULLS LAST
        """
        return self.db.execute_query(query, {"cl_high": high, "cl_med": medium})

    def get_chronicle_changes_for_latest_run(self) -> Dict[str, int]:
        """
        Get new and removed asset counts from the latest generation run

        Returns:
            Dict with added_count and removed_count
        """
        query = """
            WITH latest_run AS (
                SELECT generation_run_id
                FROM asset_chronicle
                WHERE generation_run_id IS NOT NULL
                ORDER BY time DESC
                LIMIT 1
            )
            SELECT
                COUNT(*) FILTER (WHERE event_type = 'asset_created') as added_count,
                COUNT(*) FILTER (WHERE event_type = 'asset_removed') as removed_count
            FROM asset_chronicle
            WHERE generation_run_id = (SELECT generation_run_id FROM latest_run)
        """
        result = self.db.execute_query(query)
        if result and len(result) > 0:
            return {
                'added_count': result[0]['added_count'] or 0,
                'removed_count': result[0]['removed_count'] or 0
            }
        return {'added_count': 0, 'removed_count': 0}

    def get_last_updated_time(self) -> Optional[Any]:
        """
        Get the most recent event timestamp from asset_chronicle.

        Returns:
            Timestamp or None if no events exist
        """
        query = """
            SELECT MAX(time) as last_updated FROM asset_chronicle
        """
        try:
            result = self.db.execute_query(query)
            if result and result[0]['last_updated']:
                return result[0]['last_updated']
        except Exception as e:
            logger.warning(f"Failed to get last updated time: {e}")
        return None

    def get_asset_count_trend(self) -> List[Dict[str, Any]]:
        """
        Get daily created/removed counts from asset_chronicle for the last 7 days.

        Returns:
            List of dicts with day, created, removed
        """
        query = """
            SELECT
                DATE(time) as day,
                SUM(CASE WHEN event_type = 'asset_created' THEN 1 ELSE 0 END) as created,
                SUM(CASE WHEN event_type = 'asset_removed' THEN 1 ELSE 0 END) as removed
            FROM asset_chronicle
            WHERE time >= NOW() - INTERVAL '7 days'
            GROUP BY DATE(time)
            ORDER BY day
        """
        try:
            return self.db.execute_query(query) or []
        except Exception as e:
            logger.warning(f"Failed to get asset count trend: {e}")
            return []

    def get_category_confidence_averages(self, medium: float = None, high: float = None) -> List[Dict[str, Any]]:
        """
        Get average confidence score for each category (OT and IoT grouped together)

        Args:
            medium: Lower confidence threshold (defaults to the configured medium threshold)
            high: Upper confidence threshold (defaults to the configured high threshold)

        Returns:
            List of dicts with category, avg_confidence (0-1), count, confidence_level
        """
        if medium is None or high is None:
            medium, high = DEFAULT_CONFIDENCE_THRESHOLDS
        query = """
            SELECT
                CASE
                    WHEN LOWER(category) IN ('ot', 'iot') THEN 'OT/IoT'
                    ELSE COALESCE(category, 'Uncategorized')
                END as category,
                AVG(confidence) as avg_confidence,
                COUNT(*) as count,
                CASE
                    WHEN AVG(confidence) >= %(cl_high)s THEN 'high'
                    WHEN AVG(confidence) >= %(cl_med)s THEN 'medium'
                    ELSE 'low'
                END as confidence_level,
                COUNT(*) FILTER (WHERE confidence >= %(cl_high)s) as high_count,
                COUNT(*) FILTER (WHERE confidence >= %(cl_med)s AND confidence < %(cl_high)s) as medium_count,
                COUNT(*) FILTER (WHERE confidence < %(cl_med)s) as low_count
            FROM assets
            GROUP BY
                CASE
                    WHEN LOWER(category) IN ('ot', 'iot') THEN 'OT/IoT'
                    ELSE COALESCE(category, 'Uncategorized')
                END
            ORDER BY count DESC
        """
        return self.db.execute_query(query, {"cl_high": high, "cl_med": medium})

    def get_filtered_flower_data(
        self,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        os: Optional[str] = None,
        integration_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get visualization data with optional filtering

        Args:
            category: Filter by category name
            subcategory: Filter by subcategory name
            os: Filter by OS name
            integration_id: Filter by integration UUID

        Returns:
            Dict with totalAssets and breakdowns for categories, OS, subcategories
            Subcategories include 'sources' and 'os' arrays for relationship mapping
        """
        # Build WHERE clause dynamically
        where_clauses = []
        params = []

        if category:
            where_clauses.append("a.category = %s")
            params.append(category)

        if subcategory:
            where_clauses.append("a.subcategory = %s")
            params.append(subcategory)

        if os:
            where_clauses.append("a.os_normalized = %s")
            params.append(os)

        if integration_id:
            where_clauses.append("ap.integration_id = %s")
            params.append(integration_id)

        where_clause = " AND ".join(where_clauses) if where_clauses else "TRUE"

        # Query for filtered counts with relationship data
        query = f"""
            WITH filtered_assets AS (
                SELECT DISTINCT a.id, a.category, a.subcategory, a.os_normalized
                FROM assets a
                LEFT JOIN asset_providers ap ON a.id = ap.asset_id
                WHERE {where_clause}
            ),
            -- Pre-compute relationships from ALL assets (not filtered) for performance
            subcategory_relationships AS (
                SELECT
                    a.subcategory,
                    a.category,
                    array_agg(DISTINCT ap.integration_id::text) FILTER (WHERE ap.integration_id IS NOT NULL) as sources,
                    array_agg(DISTINCT a.os_normalized) FILTER (WHERE a.os_normalized IS NOT NULL) as os_list
                FROM assets a
                LEFT JOIN asset_providers ap ON a.id = ap.asset_id
                WHERE a.subcategory IS NOT NULL
                GROUP BY a.subcategory, a.category
            )
            SELECT
                (SELECT COUNT(*) FROM filtered_assets) as total_count,
                (
                    SELECT json_agg(json_build_object(
                        'name', COALESCE(category, 'Uncategorized'),
                        'count', count
                    ) ORDER BY count DESC)
                    FROM (
                        SELECT category, COUNT(*) as count
                        FROM filtered_assets
                        GROUP BY category
                    ) cat
                ) as categories,
                (
                    SELECT json_agg(json_build_object(
                        'name', COALESCE(os_base, 'Unknown'),
                        'count', count
                    ) ORDER BY count DESC)
                    FROM (
                        SELECT
                            REGEXP_REPLACE(os_normalized, '\\s+\\d+.*$', '', 'g') as os_base,
                            COUNT(*) as count
                        FROM filtered_assets
                        WHERE os_normalized IS NOT NULL
                        GROUP BY REGEXP_REPLACE(os_normalized, '\\s+\\d+.*$', '', 'g')
                    ) os
                ) as operating_systems,
                (
                    SELECT json_agg(json_build_object(
                        'name', COALESCE(fa_group.subcategory, 'Other'),
                        'category', COALESCE(fa_group.category, 'Uncategorized'),
                        'count', fa_group.count,
                        'sources', COALESCE(sr.sources, ARRAY[]::text[]),
                        'os', COALESCE(sr.os_list, ARRAY[]::text[])
                    ) ORDER BY fa_group.count DESC)
                    FROM (
                        SELECT subcategory, category, COUNT(*) as count
                        FROM filtered_assets
                        WHERE subcategory IS NOT NULL
                        GROUP BY subcategory, category
                    ) fa_group
                    LEFT JOIN subcategory_relationships sr
                        ON fa_group.subcategory = sr.subcategory
                        AND fa_group.category = sr.category
                ) as subcategories
        """

        result = self.db.execute_query(query, params)
        if result and len(result) > 0:
            return result[0]
        else:
            return {
                'total_count': 0,
                'categories': [],
                'operating_systems': [],
                'subcategories': []
            }

    def get_flower_data_v2(self) -> Dict[str, Any]:
        """
        Get visualization data with V2 structure (direct any-to-any relationships).
        Unlike V1, every entity has direct relationship arrays to all other entity types.

        Returns:
            Dict with categories, subcategories, operatingSystems, dataSources
            Each entity has arrays linking to IDs of related entities
        """

        # Get base entity counts
        categories_query = """
            SELECT
                COALESCE(category, 'Uncategorized') as name,
                COUNT(*) as count
            FROM assets
            GROUP BY category
            ORDER BY count DESC
        """

        subcategories_query = """
            SELECT
                COALESCE(subcategory, 'Other') as name,
                COALESCE(category, 'Uncategorized') as category,
                COUNT(*) as count
            FROM assets
            WHERE subcategory IS NOT NULL
            GROUP BY subcategory, category
            ORDER BY count DESC
            LIMIT 20
        """

        os_query = r"""
            SELECT
                REGEXP_REPLACE(os_normalized, '\\s+\\d+.*$', '', 'g') as name,
                COUNT(*) as count
            FROM assets
            WHERE os_normalized IS NOT NULL
                AND os_normalized != ''
            GROUP BY REGEXP_REPLACE(os_normalized, '\\s+\\d+.*$', '', 'g')
            ORDER BY count DESC
            LIMIT 20
        """

        # Use grouped sources: groups collapse members, ungrouped shown individually
        sources_query = """
            SELECT
                COALESCE(ig.id, i.id)::text as id,
                COALESCE(ig.name, i.name) as name,
                COUNT(DISTINCT ap.asset_id) as count
            FROM integrations i
            LEFT JOIN integration_groups ig ON i.group_id = ig.id
            LEFT JOIN asset_providers ap ON i.id = ap.integration_id
            LEFT JOIN assets a ON ap.asset_id = a.id
            WHERE i.status = 'enabled'
            GROUP BY COALESCE(ig.id, i.id), COALESCE(ig.name, i.name)
            ORDER BY count DESC NULLS LAST
        """

        # Get relationship data - remap integration_id to group_id where applicable
        relationships_query = r"""
            SELECT DISTINCT
                a.category,
                a.subcategory,
                REGEXP_REPLACE(a.os_normalized, '\s+\d+.*$', '', 'g') as os_name,
                COALESCE(ig.id, ap.integration_id)::text as source_id
            FROM assets a
            LEFT JOIN asset_providers ap ON a.id = ap.asset_id
            LEFT JOIN integrations i ON ap.integration_id = i.id
            LEFT JOIN integration_groups ig ON i.group_id = ig.id
            WHERE a.category IS NOT NULL
        """

        # Execute queries
        categories_raw = self.db.execute_query(categories_query)
        subcategories_raw = self.db.execute_query(subcategories_query)
        os_raw = self.db.execute_query(os_query)
        sources_raw = self.db.execute_query(sources_query)
        relationships_raw = self.db.execute_query(relationships_query)

        # Build entities with IDs
        categories = []
        cat_name_to_id = {}
        for idx, cat in enumerate(categories_raw):
            cat_id = idx + 1
            categories.append({
                'id': cat_id,
                'name': cat['name'],
                'count': cat['count'],
                'subcategories': [],
                'sources': [],
                'os': []
            })
            cat_name_to_id[cat['name']] = cat_id

        subcategories = []
        subcat_key_to_id = {}
        for idx, sub in enumerate(subcategories_raw):
            if sub['name'] and sub['name'] != 'Other':
                sub_id = idx + 1
                subcategories.append({
                    'id': sub_id,
                    'name': sub['name'],
                    'category': sub['category'],
                    'count': sub['count'],
                    'categories': [],
                    'sources': [],
                    'os': []
                })
                subcat_key_to_id[(sub['name'], sub['category'])] = sub_id

        operating_systems = []
        os_name_to_id = {}
        for idx, os_item in enumerate(os_raw):
            if os_item['name']:
                os_id = idx + 1
                operating_systems.append({
                    'id': os_id,
                    'name': os_item['name'],
                    'count': os_item['count'],
                    'categories': [],
                    'subcategories': [],
                    'sources': []
                })
                os_name_to_id[os_item['name']] = os_id

        data_sources = []
        source_id_to_uuid = {}
        for src in sources_raw:
            data_sources.append({
                'id': src['id'],  # UUID string
                'name': src['name'],
                'count': src['count'] or 0,
                'categories': [],
                'subcategories': [],
                'os': []
            })
            source_id_to_uuid[src['id']] = src['id']

        # Build relationship sets
        from collections import defaultdict

        cat_to_subcats = defaultdict(set)
        cat_to_sources = defaultdict(set)
        cat_to_os = defaultdict(set)

        subcat_to_cats = defaultdict(set)
        subcat_to_sources = defaultdict(set)
        subcat_to_os = defaultdict(set)

        os_to_cats = defaultdict(set)
        os_to_subcats = defaultdict(set)
        os_to_sources = defaultdict(set)

        source_to_cats = defaultdict(set)
        source_to_subcats = defaultdict(set)
        source_to_os = defaultdict(set)

        # Process relationships
        for rel in relationships_raw:
            cat_name = rel['category']
            subcat_name = rel['subcategory']
            os_name = rel['os_name']
            source_id = rel['source_id']

            cat_id = cat_name_to_id.get(cat_name)
            if not cat_id:
                continue

            # Get optional field IDs
            subcat_id = None
            if subcat_name:
                subcat_id = subcat_key_to_id.get((subcat_name, cat_name))

            os_id = os_name_to_id.get(os_name) if os_name else None

            if source_id and source_id not in source_id_to_uuid:
                source_id = None

            # Build relationships
            if subcat_id:
                cat_to_subcats[cat_id].add(subcat_id)
            if source_id:
                cat_to_sources[cat_id].add(source_id)
            if os_id:
                cat_to_os[cat_id].add(os_id)

            if subcat_id:
                subcat_to_cats[subcat_id].add(cat_id)
                if source_id:
                    subcat_to_sources[subcat_id].add(source_id)
                if os_id:
                    subcat_to_os[subcat_id].add(os_id)

            if os_id:
                os_to_cats[os_id].add(cat_id)
                if subcat_id:
                    os_to_subcats[os_id].add(subcat_id)
                if source_id:
                    os_to_sources[os_id].add(source_id)

            if source_id:
                source_to_cats[source_id].add(cat_id)
                if subcat_id:
                    source_to_subcats[source_id].add(subcat_id)
                if os_id:
                    source_to_os[source_id].add(os_id)

        # Add relationships to entities
        for cat in categories:
            cat['subcategories'] = sorted(list(cat_to_subcats.get(cat['id'], set())))
            cat['sources'] = sorted(list(cat_to_sources.get(cat['id'], set())))
            cat['os'] = sorted(list(cat_to_os.get(cat['id'], set())))

        for sub in subcategories:
            sub['categories'] = sorted(list(subcat_to_cats.get(sub['id'], set())))
            sub['sources'] = sorted(list(subcat_to_sources.get(sub['id'], set())))
            sub['os'] = sorted(list(subcat_to_os.get(sub['id'], set())))

        for os_item in operating_systems:
            os_item['categories'] = sorted(list(os_to_cats.get(os_item['id'], set())))
            os_item['subcategories'] = sorted(list(os_to_subcats.get(os_item['id'], set())))
            os_item['sources'] = sorted(list(os_to_sources.get(os_item['id'], set())))

        for src in data_sources:
            src['categories'] = sorted(list(source_to_cats.get(src['id'], set())))
            src['subcategories'] = sorted(list(source_to_subcats.get(src['id'], set())))
            src['os'] = sorted(list(source_to_os.get(src['id'], set())))

        return {
            'categories': categories,
            'subcategories': subcategories,
            'operatingSystems': operating_systems,
            'dataSources': data_sources
        }

    def refresh_materialized_views(self) -> Dict[str, Any]:
        """
        Refresh all dashboard materialized views concurrently.

        Each view is refreshed in its own try/except so a missing or locked
        view doesn't block the others. ``success`` is True if at least one
        view was refreshed.

        Returns:
            Dict with ``success``, ``refreshed_views`` (list), and
            ``failed_views`` (mapping of view name to error message).
        """
        views = [
            'mv_dashboard_summary',
            'mv_provider_coverage',
            'mv_provider_coverage_grouped',
            'mv_asset_quality_metrics',
        ]
        refreshed: List[str] = []
        failed: Dict[str, str] = {}
        for view in views:
            try:
                # CONCURRENTLY requires the matview to already be populated; a view
                # created WITH NO DATA and never refreshed (a fresh install bootstraps
                # from schema-full, where the populating migration is marked
                # applied-without-running) raises errcode 0A000 ("CONCURRENTLY cannot
                # be used when the materialized view is not populated"). Worse,
                # post-#681-A1 that error fires inside the pipeline's atomic scope and
                # rolls the run back. Pre-check relispopulated and use a plain
                # (non-concurrent) REFRESH to populate a never-populated view; every
                # later run then uses CONCURRENTLY (no read-blocking AccessExclusiveLock).
                # Resolve via to_regclass so the pre-check reads the exact relation the
                # bare-name REFRESH targets under search_path (relname is not
                # schema-unique); a missing view yields no row -> plain REFRESH, which
                # then surfaces the real error through the per-view failed_views path.
                rows = self.db.execute_query(
                    "SELECT relispopulated FROM pg_class "
                    "WHERE oid = to_regclass(%(view)s) AND relkind = 'm'",
                    {"view": view},
                )
                populated = bool(rows) and rows[0]["relispopulated"]
                concurrently = "CONCURRENTLY " if populated else ""
                self.db.execute_query(
                    f"REFRESH MATERIALIZED VIEW {concurrently}{view}",
                    fetch=False,
                )
                refreshed.append(view)
            except Exception as e:
                failed[view] = str(e)
                logger.warning(
                    "Failed to refresh materialized view %s: %s", view, e,
                )

        if refreshed:
            logger.info(
                "Refreshed %d/%d dashboard materialized views",
                len(refreshed), len(views),
            )
        return {
            "success": bool(refreshed),
            "refreshed_views": refreshed,
            "failed_views": failed,
        }

    def get_summary_from_view(self) -> Optional[Dict[str, Any]]:
        """
        Get dashboard summary data from materialized view.
        Returns pre-computed totals, category breakdown, and confidence distribution.
        Falls back to None if the view doesn't exist yet.
        """
        query = """
            SELECT total_assets, assets_added_today,
                   category_breakdown, confidence_distribution,
                   refreshed_at
            FROM mv_dashboard_summary
            WHERE view_key = 'summary'
        """
        try:
            result = self.db.execute_query(query)
            if result and len(result) > 0:
                return result[0]
        except Exception as e:
            logger.warning(f"Materialized view query failed, using live queries: {e}")
        return None

    def get_integration_stats_from_view(self, medium: float = None, high: float = None) -> List[Dict[str, Any]]:
        """
        Get integration stats from materialized view.
        Falls back to live query if the view doesn't exist yet.

        Args:
            medium: Lower confidence threshold forwarded to the live fallback
            high: Upper confidence threshold forwarded to the live fallback
        """
        query = """
            SELECT
                id::text,
                name,
                integration_type,
                status,
                logo,
                last_sync_at,
                EXTRACT(EPOCH FROM (NOW() - last_sync_at)) / 3600 as hours_since_sync,
                last_run_at,
                EXTRACT(EPOCH FROM (NOW() - last_run_at)) / 3600 as hours_since_run,
                asset_count,
                avg_confidence,
                high_conf_count,
                medium_conf_count,
                low_conf_count
            FROM mv_provider_coverage
            ORDER BY asset_count DESC NULLS LAST
        """
        try:
            result = self.db.execute_query(query)
            if result is not None:
                return result
        except Exception as e:
            logger.warning(f"Provider coverage view query failed, using live query: {e}")
        return self.get_integration_stats(medium=medium, high=high)

    def get_integration_stats_grouped(self, medium: float = None, high: float = None) -> List[Dict[str, Any]]:
        """Get integration stats from grouped materialized view.
        Returns groups and ungrouped integrations as logical sources.

        Args:
            medium: Lower confidence threshold forwarded to the live fallback chain
            high: Upper confidence threshold forwarded to the live fallback chain
        """
        query = """
            SELECT
                id::text,
                name,
                integration_type,
                source_type,
                logo,
                last_sync_at,
                EXTRACT(EPOCH FROM (NOW() - last_sync_at)) / 3600 as hours_since_sync,
                last_run_at,
                EXTRACT(EPOCH FROM (NOW() - last_run_at)) / 3600 as hours_since_run,
                asset_count,
                avg_confidence,
                high_conf_count,
                medium_conf_count,
                low_conf_count
            FROM mv_provider_coverage_grouped
            ORDER BY asset_count DESC NULLS LAST
        """
        try:
            result = self.db.execute_query(query)
            if result is not None:
                return result
        except Exception as e:
            logger.warning(f"Grouped provider coverage view query failed: {e}")
        return self.get_integration_stats_from_view(medium=medium, high=high)

    def get_provider_category_breakdown(self) -> List[Dict[str, Any]]:
        """Per-data-source asset counts grouped by asset category.

        Powers the per-source category mini-bars on the dashboard's Data
        Sources list. ``source`` matches the integration ``name`` (which is
        what ``asset_providers.data_source`` stores), so callers map the rows
        onto each source by name. Category is normalized (NULL/empty/whitespace
        → 'miscellaneous'); rolling non-canonical categories into 'misc' is the
        caller's job since it owns the canonical set.

        Returns:
            List of {source, category, n}.
        """
        query = """
            SELECT
                ap.data_source AS source,
                COALESCE(NULLIF(LOWER(TRIM(a.category)), ''), 'miscellaneous') AS category,
                COUNT(*) AS n
            FROM asset_providers ap
            JOIN assets a ON a.id = ap.asset_id
            GROUP BY ap.data_source, 2
        """
        try:
            return self.db.execute_query(query) or []
        except Exception as e:
            logger.warning(f"Failed to get provider category breakdown: {e}")
            return []

    def get_category_confidence_from_view(self, medium: float = None, high: float = None) -> List[Dict[str, Any]]:
        """
        Get category confidence averages from materialized view.
        Falls back to live query if the view doesn't exist yet.

        Args:
            medium: Lower confidence threshold forwarded to the live fallback
            high: Upper confidence threshold forwarded to the live fallback
        """
        query = """
            SELECT category, avg_confidence, count, confidence_level,
                   COALESCE(high_count, 0) AS high_count,
                   COALESCE(medium_count, 0) AS medium_count,
                   COALESCE(low_count, 0) AS low_count
            FROM mv_asset_quality_metrics
            ORDER BY count DESC
        """
        try:
            result = self.db.execute_query(query)
            if result is not None:
                return result
        except Exception as e:
            logger.warning(f"Quality metrics view query failed, using live query: {e}")
        return self.get_category_confidence_averages(medium=medium, high=high)

    def get_source_coverage(self) -> List[Dict[str, Any]]:
        """
        Get distinct data source count per asset category.

        Returns:
            List of dicts with category, source_count
        """
        query = """
            SELECT
                category,
                COUNT(DISTINCT unnest_source) as source_count
            FROM assets, unnest(data_source) as unnest_source
            WHERE category IS NOT NULL
            GROUP BY category
            ORDER BY source_count DESC
        """
        try:
            return self.db.execute_query(query) or []
        except Exception as e:
            logger.warning(f"Failed to get source coverage: {e}")
            return []

    def get_completeness(self) -> float:
        """
        Get average data completeness across all assets.

        Uses the data_completeness sub-score stored in confidence_breakdown JSONB.
        Returns a 0-100 percentage.
        """
        query = """
            SELECT
                AVG((confidence_breakdown->>'data_completeness')::float) * 100 as completeness
            FROM assets
            WHERE confidence_breakdown IS NOT NULL
              AND confidence_breakdown->>'data_completeness' IS NOT NULL
        """
        try:
            result = self.db.execute_query(query)
            if result and result[0]['completeness'] is not None:
                return round(float(result[0]['completeness']), 1)
        except Exception as e:
            logger.warning(f"Failed to get completeness: {e}")
        return 0

    def get_posture_tier_breakdown(self) -> List[Dict[str, Any]]:
        """Per-(asset_category, tier) asset counts using the hierarchical CASE.

        Each asset belongs to exactly one tier:
        - secured     → posture_rules.secured.state = 'true'
        - monitored   → not secured AND posture_rules.monitored.state = 'true'
        - discovered  → everything else (FAIL, N/A, missing key)

        Asset category is normalized: NULL/empty/whitespace → 'miscellaneous'.
        """
        query = """
            WITH tiered AS (
                SELECT
                    COALESCE(NULLIF(LOWER(TRIM(category)), ''), 'miscellaneous') AS asset_category,
                    CASE
                        WHEN posture_rules -> 'secured'   ->> 'state' = 'true' THEN 'secured'
                        WHEN posture_rules -> 'monitored' ->> 'state' = 'true' THEN 'monitored'
                        ELSE 'discovered'
                    END AS tier
                FROM assets
            )
            SELECT asset_category, tier, COUNT(*) AS n
            FROM tiered
            GROUP BY asset_category, tier
        """
        return self.db.execute_query(query)

    def get_posture_snapshot_at_or_before(self, days_ago: int) -> Optional[Dict[str, Any]]:
        """Return the most-recent snapshot row taken at or before (now - days_ago).

        Returns None if no snapshot is old enough yet. Columns are aliased to
        tier names so callers don't need to know the storage shape.
        """
        query = """
            SELECT secured_count    AS secured,
                   monitored_count  AS monitored,
                   discovered_count AS discovered
            FROM posture_tier_snapshots
            WHERE snapshot_at <= now() - (%(days)s || ' days')::interval
            ORDER BY snapshot_at DESC
            LIMIT 1
        """
        rows = self.db.execute_query(query, {"days": days_ago})
        return rows[0] if rows else None

    def get_posture_tier_trend(self, days: int = 30) -> List[Dict[str, Any]]:
        """Daily secured/monitored/discovered totals over the last ``days``.

        Snapshots in ``posture_tier_snapshots`` are written irregularly (per
        pipeline run), so each day carries forward the most recent snapshot at
        or before the end of that day — producing a continuous daily series
        even when snapshots are sparse. Days before the first snapshot come
        back NULL (rendered as 0 by the service).

        Returns:
            List of {day, secured, monitored, discovered}, oldest first.
        """
        query = """
            WITH date_series AS (
                SELECT generate_series(
                    CURRENT_DATE - make_interval(days => %(days)s - 1),
                    CURRENT_DATE,
                    '1 day'::interval
                )::date AS day
            )
            SELECT ds.day,
                   s.secured_count    AS secured,
                   s.monitored_count  AS monitored,
                   s.discovered_count AS discovered
            FROM date_series ds
            LEFT JOIN LATERAL (
                SELECT secured_count, monitored_count, discovered_count
                FROM posture_tier_snapshots
                WHERE snapshot_at < ds.day + 1
                ORDER BY snapshot_at DESC
                LIMIT 1
            ) s ON TRUE
            ORDER BY ds.day
        """
        try:
            return self.db.execute_query(query, {"days": days}) or []
        except Exception as e:
            logger.warning(f"Failed to get posture tier trend: {e}")
            return []

    def get_asset_timeline(self, days: int = 30) -> List[Dict[str, Any]]:
        """Daily New / Returned / Departed asset counts, net-distinct per day.

        Net-distinct per asset per day (from asset_chronicle presence events):
        an asset is counted on a day only when its end-of-day presence differs
        from its start-of-day presence, so multiple runs/day don't inflate and
        same-day round-trips cancel. New = asset_created, Returned =
        asset_readded, Departed = asset_removed EXCLUDING pipeline_merge
        (identity consolidations are not real departures). Classification runs
        in Python (timeline_classifier) so it's unit-testable.
        """
        from src.modules.dashboard.services.timeline_classifier import (
            classify_net_timeline,
        )

        series_sql = """
            SELECT TO_CHAR(d, 'Mon DD') AS label, d::date AS day
            FROM generate_series(
                CURRENT_DATE - make_interval(days => %(days)s - 1),
                CURRENT_DATE,
                '1 day'::interval
            ) d
            ORDER BY d
        """

        # One row per (asset, day-with-a-presence-event) for assets active in
        # the window, PLUS a baseline row per asset (day = window_start - 1)
        # carrying its last pre-window state, so day-1 transitions are seeded.
        state_sql = """
            WITH pe AS (
                SELECT c.asset_id, c.time,
                       (c.event_type IN ('asset_created','asset_readded')) AS is_enter,
                       c.event_type
                FROM asset_chronicle c
                WHERE c.event_type IN ('asset_created','asset_readded','asset_removed')
                  AND (c.event_type <> 'asset_removed'
                       OR c.causation IS DISTINCT FROM 'pipeline_merge')
                  AND c.time < CURRENT_DATE + 1
            ),
            windowed AS (
                SELECT DISTINCT asset_id FROM pe
                WHERE time >= CURRENT_DATE - make_interval(days => %(days)s - 1)
            ),
            ev AS (
                SELECT p.asset_id, p.time::date AS day, p.time,
                       p.is_enter, p.event_type
                FROM pe p JOIN windowed w USING (asset_id)
                WHERE p.time >= CURRENT_DATE - make_interval(days => %(days)s - 1)
                UNION ALL
                SELECT b.asset_id,
                       (CURRENT_DATE - make_interval(days => %(days)s - 1))::date - 1 AS day,
                       b.time, b.is_enter, b.event_type
                FROM (
                    SELECT DISTINCT ON (p.asset_id)
                           p.asset_id, p.time, p.is_enter, p.event_type
                    FROM pe p JOIN windowed w USING (asset_id)
                    WHERE p.time < CURRENT_DATE - make_interval(days => %(days)s - 1)
                    ORDER BY p.asset_id, p.time DESC
                ) b
            )
            SELECT asset_id, day, end_enter, last_evt
            FROM (
                SELECT DISTINCT ON (asset_id, day)
                       asset_id, day,
                       is_enter AS end_enter, event_type AS last_evt
                FROM ev
                ORDER BY asset_id, day, time DESC
            ) d
            ORDER BY asset_id, day
        """
        try:
            series = self.db.execute_query(series_sql, {"days": days}) or []
            if not series:
                return []
            window_start = series[0]['day']
            state_rows = self.db.execute_query(state_sql, {"days": days}) or []
            buckets = classify_net_timeline(state_rows, window_start)
            zero = {'added': 0, 'returned': 0, 'removed': 0}
            return [
                {
                    'label': row['label'],
                    'added': buckets.get(row['day'], zero)['added'],
                    'returned': buckets.get(row['day'], zero)['returned'],
                    'removed': buckets.get(row['day'], zero)['removed'],
                }
                for row in series
            ]
        except Exception as e:
            logger.warning(f"Failed to get asset timeline: {e}")
            return []

    def get_coverage_trend(
        self, control_id: str, asset_categories: List[str], days: int = 30,
    ) -> List[Dict[str, Any]]:
        """Daily covered/uncovered counts for one posture control over a set of
        asset categories, carrying forward the most recent snapshot at or before
        each day (mirrors get_posture_tier_trend).

        Returns oldest-first list of {label, added (=covered), removed (=total-covered)}.
        """
        query = """
            WITH date_series AS (
                SELECT generate_series(
                    CURRENT_DATE - make_interval(days => %(days)s - 1),
                    CURRENT_DATE,
                    '1 day'::interval
                )::date AS day
            )
            SELECT
                TO_CHAR(ds.day, 'Mon DD') AS label,
                COALESCE(s.covered, 0)              AS added,
                COALESCE(s.total - s.covered, 0)    AS removed
            FROM date_series ds
            LEFT JOIN LATERAL (
                SELECT
                    SUM(pass_count) AS covered,
                    SUM(pass_count + fail_count + na_count) AS total
                FROM posture_coverage_snapshots
                WHERE control_id = %(control)s
                  AND asset_category = ANY(%(cats)s)
                  AND snapshot_at = (
                      SELECT MAX(snapshot_at)
                      FROM posture_coverage_snapshots
                      WHERE control_id = %(control)s
                        AND snapshot_at < ds.day + 1
                  )
            ) s ON TRUE
            ORDER BY ds.day
        """
        try:
            return self.db.execute_query(
                query,
                {"days": days, "control": control_id, "cats": asset_categories},
            ) or []
        except Exception as e:
            logger.warning(f"Failed to get coverage trend: {e}")
            return []

    def get_coverage_current(
        self, control_id: str, asset_categories: List[str],
    ) -> Dict[str, int]:
        """Live covered/total for one posture control over a set of asset
        categories, read straight from assets.posture_rules (no snapshot).

        Used to override the latest trend point so the card's current value is
        always correct, even before any snapshot exists.
        """
        query = """
            SELECT
                COUNT(*) FILTER (
                    WHERE posture_rules -> %(control)s ->> 'state' = 'true'
                ) AS covered,
                COUNT(*) AS total
            FROM assets
            WHERE COALESCE(NULLIF(LOWER(TRIM(category)), ''), 'miscellaneous')
                  = ANY(%(cats)s)
        """
        try:
            rows = self.db.execute_query(
                query, {"control": control_id, "cats": asset_categories},
            )
            if rows:
                return {
                    "covered": int(rows[0]["covered"] or 0),
                    "total": int(rows[0]["total"] or 0),
                }
        except Exception as e:
            logger.warning(f"Failed to get current coverage: {e}")
        return {"covered": 0, "total": 0}
