# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Repository

Handles database queries for asset inventory data.
"""

import logging
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID

from src.database.postgres_adapter import PostgreSQLAdapter
from src.api.modules.settings.settings_schema import DEFAULT_CONFIDENCE_THRESHOLDS
from src.modules.fields import FieldResolver, InvalidFieldError, coerce_field_id
from src.modules.fields.catalog import is_filter_only_field
from src.modules.fields.encoding import decode as decode_field_id, encode as encode_field_id
from src.modules.fields.models import StaticField, VirtualField

logger = logging.getLogger(__name__)


# Default columns returned when ?fields= is omitted. Each entry is an encoded
# FieldId string whose display name must match STATIC_FIELDS in src/modules/fields/catalog.py.
DEFAULT_LIST_FIELDS_ENCODED: List[str] = [
    "static:Hostname",
    "static:Category",
    "static:Subcategory",
    "static:OS%20Normalized",
    "static:Manufacturer",
    "static:Model",
    "static:Confidence%20Score",
    "static:First%20Seen",
    "static:Last%20Seen",
]


# Single source of truth for an asset's keyword-searchable text: every field the
# Explorer free-text box looks at, concatenated into one string. A pg_trgm GIN
# index is built on THIS EXACT expression (migration
# 112_add_assets_search_trgm_index.sql and schema-full.sql) so that
# `<expr> ILIKE '%term%'` uses the index instead of a full sequential scan.
# Keep the column set, order and separators in sync with that index definition.
#
# Array columns go through asset_search_join() — an IMMUTABLE wrapper around
# array_to_string(arr, ' ') created by the same migration. array_to_string is
# only marked STABLE (its anyarray signature is conservative), so it cannot be
# used directly in an index expression; the wrapper is genuinely immutable for
# text[] and lets the planner index the expression.
_ASSET_SEARCH_EXPR = (
    "("
    "coalesce(asset_search_join(hostname), '') || ' ' || "
    "coalesce(asset_search_join(ip), '') || ' ' || "
    "coalesce(asset_search_join(mac), '') || ' ' || "
    "coalesce(asset_search_join(sn), '') || ' ' || "
    "coalesce(asset_search_join(fqdn), '') || ' ' || "
    "coalesce(asset_search_join(manufacturer), '') || ' ' || "
    "coalesce(asset_search_join(model), '') || ' ' || "
    "coalesce(asset_search_join(data_source), '') || ' ' || "
    "coalesce(asset_search_join(owner), '') || ' ' || "
    "coalesce(asset_search_join(location), '') || ' ' || "
    "coalesce(asset_search_join(user_field), '') || ' ' || "
    "coalesce(category, '') || ' ' || "
    "coalesce(subcategory, '') || ' ' || "
    "coalesce(os_normalized, '') || ' ' || "
    "coalesce(extended_attributes::text, '')"
    ")"
)


class AssetRepository:
    """Repository for asset data access"""

    def __init__(self, db_adapter: PostgreSQLAdapter, resolver: FieldResolver):
        """
        Initialize repository with database adapter.

        Args:
            db_adapter: PostgreSQL database adapter
            resolver: FieldResolver for typed field identifiers
        """
        self.db = db_adapter
        self._resolver = resolver
        # Transient per-iteration handle set by _build_filter_clauses so the
        # registry-backed _build_single_filter can route on the ResolvedField.
        self._current_resolved = None

    def get_asset_by_id(self, asset_id: str) -> Optional[Dict[str, Any]]:
        """
        Get asset by ID.

        Args:
            asset_id: Asset UUID

        Returns:
            Asset dict or None if not found
        """
        query = """
            SELECT
                id,
                asset,
                hostname,
                fqdn,
                ip,
                mac,
                sn,
                category,
                subcategory,
                os_normalized,
                os,
                os_orig,
                mac_vendor_enriched,
                manufacturer,
                model,
                data_source,
                data_source_category,
                vendor_category,
                confidence,
                confidence_breakdown,
                ipam_location,
                owner,
                location,
                user_field,
                extended_attributes,
                first_time,
                last_time,
                first_seen,
                last_seen,
                missing_since,
                updated_at
            FROM assets
            WHERE id = %(asset_id)s
        """

        results = self.db.execute_query(query, {'asset_id': asset_id})

        if results and len(results) > 0:
            return results[0]
        return None

    def get_asset_tags(self, asset_id: str) -> List[Dict[str, Any]]:
        """
        Get all tags for an asset with their colors.

        Args:
            asset_id: Asset UUID

        Returns:
            List of tag dicts with name and color
        """
        query = """
            SELECT
                at.tag_name,
                at.tag_type,
                CASE
                    WHEN tr.type = 'classification' THEN tr.tags->>'color'
                    WHEN tr.type = 'compliance' AND at.tag_type = 'pass' THEN '#10b981'
                    WHEN tr.type = 'compliance' AND at.tag_type = 'fail' THEN '#ef4444'
                    ELSE '#64748b'
                END as color
            FROM asset_tags_v2 at
            LEFT JOIN tag_rules tr ON at.tag_rule_id = tr.id
            WHERE at.asset_id = %(asset_id)s

            UNION ALL

            SELECT
                mt.tag_name,
                'manual' as tag_type,
                COALESCE(mt.tag_color, '#64748b') as color
            FROM asset_manual_tags mt
            WHERE mt.asset_id = %(asset_id)s::uuid

            ORDER BY tag_name
        """

        results = self.db.execute_query(query, {'asset_id': asset_id})

        return results if results else []

    def get_other_sources_for_category(self, category: str, exclude_sources: List[str]) -> List[Dict[str, Any]]:
        """
        Get integrations/groups that have assets in the given category
        but are not already providers for this asset.

        Group-aware logic:
        - If the asset has a provider from ANY member of a group, the whole
          group is considered "present" — no group members shown.
        - If the asset has NO provider from any member of a group, show the
          group name (not individual members).
        - Ungrouped integrations are shown individually as before.

        Returns:
            List of dicts with name, integration_type, logo, asset_count
        """
        if not category:
            return []

        ex_placeholders = ', '.join(
            ['%(ex_' + str(i) + ')s' for i in range(len(exclude_sources))]
        ) if exclude_sources else "''"

        # Find group_ids that are "present" (asset has a provider from a member)
        # Then return:
        #   1) Groups where NO member is present, aggregated as one row
        #   2) Ungrouped integrations not in exclude list
        query = f"""
            WITH present_groups AS (
                SELECT DISTINCT i.group_id
                FROM integrations i
                WHERE i.group_id IS NOT NULL
                AND i.name IN ({ex_placeholders})
            ),
            -- Groups where the asset is NOT present in any member
            missing_groups AS (
                SELECT
                    ig.name,
                    ig.integration_type,
                    (SELECT i2.logo FROM integrations i2
                     WHERE i2.group_id = ig.id AND i2.status = 'enabled'
                     LIMIT 1) AS logo,
                    COUNT(DISTINCT ap.asset_id) AS asset_count
                FROM integration_groups ig
                JOIN integrations i ON i.group_id = ig.id AND i.status = 'enabled'
                LEFT JOIN asset_providers ap ON ap.data_source = i.name
                LEFT JOIN assets a ON ap.asset_id = a.id AND a.category = %(category)s
                WHERE ig.id NOT IN (SELECT group_id FROM present_groups)
                GROUP BY ig.id, ig.name, ig.integration_type
                HAVING COUNT(DISTINCT ap.asset_id) FILTER (
                    WHERE a.category = %(category)s
                ) > 0
            ),
            -- Ungrouped integrations not already present
            missing_ungrouped AS (
                SELECT
                    i.name,
                    i.integration_type,
                    i.logo,
                    COUNT(DISTINCT ap.asset_id) AS asset_count
                FROM integrations i
                JOIN asset_providers ap ON ap.data_source = i.name
                JOIN assets a ON ap.asset_id = a.id
                WHERE a.category = %(category)s
                AND i.status = 'enabled'
                AND i.group_id IS NULL
                AND i.name NOT IN ({ex_placeholders})
                GROUP BY i.name, i.integration_type, i.logo
            )
            SELECT * FROM missing_groups
            UNION ALL
            SELECT * FROM missing_ungrouped
            ORDER BY asset_count DESC
        """

        params = {'category': category}
        for i, src in enumerate(exclude_sources):
            params[f'ex_{i}'] = src

        results = self.db.execute_query(query, params)
        return results if results else []

    def get_asset_providers(self, asset_id: str) -> List[Dict[str, Any]]:
        """
        Get all data providers for an asset with integration metadata.

        Args:
            asset_id: Asset UUID

        Returns:
            List of provider dicts with integration info
        """
        query = """
            SELECT
                ap.id,
                ap.asset_id,
                ap.data_source,
                ap.data_source_category,
                ap.hostname,
                ap.fqdn,
                ap.ip,
                ap.mac,
                ap.sn,
                ap.os,
                ap.manufacturer,
                ap.model,
                ap.vendor_category,
                ap.data_source_fields,
                ap.updated_at,
                i.name as source_name,
                i.integration_type as source_type,
                i.status as source_status,
                i.last_sync_at,
                i.category as source_category,
                i.logo as source_logo
            FROM asset_providers ap
            LEFT JOIN integrations i ON ap.data_source = i.name
            WHERE ap.asset_id = %(asset_id)s
            ORDER BY ap.updated_at DESC
        """

        results = self.db.execute_query(query, {'asset_id': asset_id})

        return results if results else []

    def get_merge_keys(self, asset_id: str) -> List[Dict[str, Any]]:
        """
        Get the merge-key components (asset_id_mappings rows) that were
        glued together to form this asset.

        Args:
            asset_id: Asset UUID

        Returns:
            List of dicts with component, first_seen, last_seen, ordered by
            first_seen (earliest-observed identifier first).
        """
        query = """
            SELECT component, first_seen, last_seen
            FROM asset_id_mappings
            WHERE asset_id = %(asset_id)s::uuid
            ORDER BY first_seen
        """

        results = self.db.execute_query(query, {'asset_id': asset_id})

        return results if results else []

    def list_assets(
        self,
        page: int = 1,
        limit: int = 50,
        category: Optional[str] = None,
        search: Optional[str] = None,
        fields: Optional[List[str]] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
        confidence_thresholds: tuple = None,
        logic: str = "and",
        alert_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        List assets with pagination, filtering, and sorting.

        Args:
            page: Page number (1-indexed)
            limit: Results per page
            category: Optional category filter (legacy, prefer filters param)
            search: Optional search term (searches hostname, ip, mac)
            fields: Optional list of encoded FieldId strings to include in response
            sort_by: Optional encoded FieldId string to sort by
            sort_dir: Sort direction ('asc' or 'desc', default 'desc')
            filters: Optional list of structured filter dicts
                     [{field_id: {...}, op: str, value: Any}]
            alert_id: Optional alert UUID — narrow to the assets that alert
                      names. Must already be canonical (see _build_alert_clause).

        Returns:
            Tuple of (asset list, total count)
        """
        offset = (page - 1) * limit

        # Build WHERE clause
        where_clauses = []
        params = {'limit': limit, 'offset': offset}

        if category:
            where_clauses.append("category = %(category)s")
            params['category'] = category

        # The alert scope narrows whatever else is asked for, so it ANDs with
        # the rest even when the structured filters are OR-ed together (#907).
        if alert_id:
            alert_clause, alert_params = self._build_alert_clause(alert_id)
            where_clauses.append(alert_clause)
            params.update(alert_params)

        if search:
            search_clauses, search_params = self._build_search_clause(search)
            where_clauses.extend(search_clauses)
            params.update(search_params)

        # Apply structured filters, combined with the requested flat logic
        # (default AND — _join_filter_clauses keeps the AND path byte-identical).
        if filters:
            filter_clauses, filter_params = self._build_filter_clauses(
                filters, confidence_thresholds=confidence_thresholds
            )
            where_clauses.extend(self._join_filter_clauses(filter_clauses, logic))
            params.update(filter_params)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        # Count query
        count_query = f"""
            SELECT COUNT(*) as total
            FROM assets
            {where_sql}
        """

        count_result = self.db.execute_query(count_query, params)
        total = count_result[0]['total'] if count_result else 0

        # Build SELECT clause based on requested fields
        select_columns = self._build_select_columns(fields, confidence_thresholds=confidence_thresholds)

        # Build ORDER BY clause
        order_sql = self._build_order_by(sort_by, sort_dir)

        # Data query with dynamic columns
        data_query = f"""
            SELECT
                {select_columns}
            FROM assets
            {where_sql}
            {order_sql}
            LIMIT %(limit)s OFFSET %(offset)s
        """

        results = self.db.execute_query(data_query, params)

        return results if results else [], total

    def get_matching_asset_ids(
        self,
        filters: Optional[List[Dict[str, Any]]] = None,
        search: Optional[str] = None,
        category: Optional[str] = None,
        logic: str = "and",
        limit: Optional[int] = None,
        confidence_thresholds: tuple = None,
        alert_id: Optional[str] = None,
    ) -> List[str]:
        """Return the ids of every asset matching the given filter/search.

        Builds the WHERE clause with the SAME helpers list_assets() uses so the
        result set is identical to what the Explorer table shows (confidence
        thresholds included). ``limit`` bounds the fetch — the bulk caller asks
        for cap+1 so it can detect "too many" with one query.

        ``alert_id`` narrows to the assets that alert names, resolved the same way
        list_assets() resolves it (#1011). This is what keeps a bulk action's
        target set identical to the count the Explorer shows under an alert
        drilldown — the count comes from list_assets, the targets from here, and
        the two must agree. Must already be canonical (see _build_alert_clause).
        """
        where_clauses: List[str] = []
        params: Dict[str, Any] = {}

        if category:
            where_clauses.append("category = %(category)s")
            params['category'] = category

        # ANDs with whatever else is asked for even when the structured filters
        # OR among themselves, so it can only narrow — the alert clause lands
        # outside the parenthesised OR group _join_filter_clauses builds below.
        if alert_id:
            alert_clause, alert_params = self._build_alert_clause(alert_id)
            where_clauses.append(alert_clause)
            params.update(alert_params)

        if search:
            search_clauses, search_params = self._build_search_clause(search)
            where_clauses.extend(search_clauses)
            params.update(search_params)

        if filters:
            filter_clauses, filter_params = self._build_filter_clauses(
                filters, confidence_thresholds=confidence_thresholds
            )
            where_clauses.extend(self._join_filter_clauses(filter_clauses, logic))
            params.update(filter_params)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        limit_sql = ""
        if limit is not None:
            limit_sql = "LIMIT %(limit)s"
            params['limit'] = limit

        query = f"SELECT id FROM assets {where_sql} {limit_sql}"
        rows = self.db.execute_query(query, params)
        return [str(r['id']) for r in (rows or [])]

    def _build_alert_clause(self, alert_id: str) -> Tuple[str, Dict[str, Any]]:
        """Build the WHERE clause narrowing assets to the ones an alert names.

        An alert stores its assets as `alerts.asset_ids uuid[]`, so the membership
        test is the same one the MCP alerts tool uses in the other direction
        (`<asset id> = ANY(asset_ids)`, src/mcp/tools/alerts.py). Resolving it as a
        subquery keeps the whole drilldown to ONE alert uuid in the URL: the ids
        never travel to the client and back, so an alert naming 35k assets links
        exactly like one naming three.

        EXISTS rather than `id = ANY((SELECT asset_ids ...))` — Postgres parses a
        parenthesised SELECT after ANY as a sublink whatever the nesting, so that
        spelling dies on `operator does not exist: uuid = uuid[]`. This form plans
        to an index-only scan on assets_pkey (`Index Cond: id = ANY(al.asset_ids)`)
        driven by the alerts PK lookup, measured at 31 ms against the widest alert
        on the dev DB (35,136 of 73,740 assets).

        An unknown alert id and an alert naming no assets both leave the EXISTS
        false for every row, so both answer "no assets" rather than raising —
        deliberate, see the blueprint. `alert_id` must already be canonical: the
        `::uuid` cast raises a psycopg2 error (not a ValueError) on a typo, which
        `handle_errors` would answer with a 500 (#973/#999), so the handler runs it
        through ensure_uuid() first.
        """
        clause = (
            "EXISTS (SELECT 1 FROM alerts al "
            "WHERE al.id = %(alert_id)s::uuid AND assets.id = ANY(al.asset_ids))"
        )
        return clause, {'alert_id': alert_id}

    def _build_search_clause(self, search: str) -> Tuple[List[str], Dict[str, Any]]:
        """Build the keyword-search WHERE clauses + params for the free-text box.

        Every searchable field is folded into one text expression
        (``_ASSET_SEARCH_EXPR``) that a pg_trgm GIN index accelerates for
        ``ILIKE '%token%'``. The query is tokenized on whitespace and tokens are
        AND-ed, so multi-word searches ("Windows Server 2019") narrow rather than
        loosen the result. Token count is capped to bound WHERE-clause growth.

        Previously each token expanded to a ~15-field ``unnest(...) ILIKE`` OR
        that no index could serve, so every search was a full sequential scan
        (slow / timeouts at scale). Returns one predicate per token.

        Note: a pg_trgm GIN index only accelerates tokens of 3+ characters;
        1-2 character tokens have no trigrams and still fall back to a scan
        (results stay correct, just not index-accelerated).
        """
        tokens = [t for t in search.split() if t][:8]
        if not tokens:
            tokens = [search]
        clauses: List[str] = []
        params: Dict[str, Any] = {}
        for idx, tok in enumerate(tokens):
            key = f"search_{idx}"
            params[key] = f"%{tok}%"
            clauses.append(f"{_ASSET_SEARCH_EXPR} ILIKE %({key})s")
        return clauses, params

    def _build_select_columns(
        self,
        field_encoded_list: Optional[List[str]],
        confidence_thresholds=None,
    ) -> str:
        """
        Build SQL SELECT column list.

        `id` is always included (primary key, not a FieldId). All other
        columns are decoded from encoded FieldId strings and resolved via
        the FieldResolver — the resolver's SQL expression is trusted, and
        the alias is re-encoded from the resolved FieldId so response dict
        keys are always in canonical percent-encoded form regardless of
        whether Flask URL-decoded the incoming query string.

        When the confidence_level virtual field is requested, the CASE
        expression is interpolated with the configured thresholds (safe —
        these are validated floats, not user input) so that sort/aggregate
        projection agrees with the filter and the cell post-processor.

        Args:
            field_encoded_list: List of encoded FieldId strings (e.g.
                ['static:Hostname', 'ext:Intune%20Full:deviceType']).
            confidence_thresholds: Optional (medium, high) float tuple.
                Defaults to DEFAULT_CONFIDENCE_THRESHOLDS when not supplied.

        Returns:
            SQL column list string.
        """
        medium, high = confidence_thresholds or DEFAULT_CONFIDENCE_THRESHOLDS

        if not field_encoded_list:
            field_encoded_list = list(DEFAULT_LIST_FIELDS_ENCODED)

        columns: List[str] = ["id"]  # primary key, not a FieldId
        for enc in field_encoded_list:
            field_id = decode_field_id(enc)
            # Re-encode so the alias is canonical (encode(decode(s)) == s
            # for any valid s — spaces become %20, etc).
            canonical = encode_field_id(field_id)
            if isinstance(field_id, VirtualField) and field_id.name == 'confidence_level':
                # Interpolate configured thresholds so projection agrees with
                # the filter (_build_confidence_level_clause) and the cell
                # post-processor (asset_service.py). Thresholds are validated
                # floats — safe to embed as numeric literals.
                case_expr = (
                    f"CASE WHEN confidence >= {float(high)} THEN 'high' "
                    f"WHEN confidence >= {float(medium)} THEN 'medium' "
                    f"ELSE 'low' END"
                )
                alias_escaped = canonical.replace('"', '""').replace('%', '%%')
                columns.append(f'{case_expr} AS "{alias_escaped}"')
                continue
            resolved = self._resolver.resolve(field_id)
            columns.append(resolved.select_expr(alias=canonical))
        return ", ".join(columns)

    def search_assets(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search assets by hostname, IP, MAC, or serial number.

        Args:
            query: Search query
            limit: Maximum results to return

        Returns:
            List of matching assets
        """
        sql = """
            SELECT
                id,
                hostname,
                category,
                subcategory,
                os_normalized,
                ip,
                mac,
                sn,
                owner,
                data_source,
                last_seen,
                CASE
                    WHEN EXISTS (
                        SELECT 1 FROM unnest(hostname) AS h
                        WHERE h ILIKE %(query)s
                    ) THEN 'hostname'
                    WHEN EXISTS (
                        SELECT 1 FROM unnest(ip) AS i
                        WHERE i = %(query_exact)s
                    ) THEN 'ip'
                    WHEN EXISTS (
                        SELECT 1 FROM unnest(mac) AS m
                        WHERE m = %(query_exact)s
                    ) THEN 'mac'
                    WHEN EXISTS (
                        SELECT 1 FROM unnest(sn) AS s
                        WHERE s ILIKE %(query)s
                    ) THEN 'serial_number'
                    ELSE 'other'
                END as match_field
            FROM assets
            WHERE
                EXISTS (
                    SELECT 1 FROM unnest(hostname) AS h
                    WHERE h ILIKE %(query)s
                )
                OR EXISTS (
                    SELECT 1 FROM unnest(ip) AS i
                    WHERE i = %(query_exact)s
                )
                OR EXISTS (
                    SELECT 1 FROM unnest(mac) AS m
                    WHERE m = %(query_exact)s
                )
                OR EXISTS (
                    SELECT 1 FROM unnest(sn) AS s
                    WHERE s ILIKE %(query)s
                )
            ORDER BY
                CASE
                    WHEN EXISTS (
                        SELECT 1 FROM unnest(hostname) AS h
                        WHERE h = %(query_exact)s
                    ) THEN 1
                    WHEN EXISTS (
                        SELECT 1 FROM unnest(hostname) AS h
                        WHERE h ILIKE %(query)s
                    ) THEN 2
                    WHEN EXISTS (
                        SELECT 1 FROM unnest(ip) AS i
                        WHERE i = %(query_exact)s
                    ) THEN 3
                    ELSE 4
                END
            LIMIT %(limit)s
        """

        params = {
            'query': f"%{query}%",
            'query_exact': query,
            'limit': limit
        }

        results = self.db.execute_query(sql, params)

        return results if results else []

    # -----------------------------------------------------------------
    # Filter-to-SQL translator
    # -----------------------------------------------------------------

    def _build_order_by(self, sort_by_encoded: Optional[str], sort_dir: Optional[str]) -> str:
        """Build ORDER BY clause from sort parameters.

        `sort_by_encoded` is a canonical encoded FieldId string (e.g.
        'static:Hostname'). Returns `last_time DESC` when omitted.

        Every branch appends `id ASC` as a unique tiebreaker so that
        paginated queries (LIMIT/OFFSET across pages) are deterministic.
        Without it, PostgreSQL may return different orderings for tied rows
        across separate executions, causing multi-page exports to skip or
        duplicate rows.

        Raises:
            ValueError: if the encoded string is malformed (caught by Pydantic
                before reaching here for sort_by, but may occur in other paths).
            InvalidFieldError: if the FieldId is well-formed but not live in the
                catalog. Propagates to the blueprint, which converts it to 400.
        """
        if not sort_by_encoded:
            return "ORDER BY last_time DESC NULLS LAST, id ASC"

        field_id = decode_field_id(sort_by_encoded)

        # A filter-only field's expression is a correlated subquery; ORDER BY
        # evaluates it once per matching row before LIMIT (>180s on the dev DB),
        # exactly the whole-table cost the pivot/values guards refuse. The UI
        # cannot request it (filter-only entries are absent from the pickers),
        # so this closes the hand-crafted `?sort_by=` path the same way the
        # aggregate guard closes the hand-crafted `?row_field=` one.
        if is_filter_only_field(field_id):
            raise InvalidFieldError(
                f"{sort_by_encoded!r} is filter-only and cannot be sorted"
            )

        resolved = self._resolver.resolve(field_id)

        direction = 'ASC' if sort_dir == 'asc' else 'DESC'
        nulls = 'NULLS LAST' if direction == 'DESC' else 'NULLS FIRST'

        expr = resolved.where_expr()
        # For native array columns, sort by the first element.
        if resolved.is_native_array():
            return f"ORDER BY {expr}[1] {direction} {nulls}, id ASC"

        return f"ORDER BY {expr} {direction} {nulls}, id ASC"

    def _build_filter_clauses(
        self, filters: List[Dict[str, Any]],
        confidence_thresholds: tuple = None
    ) -> Tuple[List[str], Dict[str, Any]]:
        """
        Build SQL WHERE clause fragments from structured filters.

        Args:
            filters: List of {field_id, op, value} dicts. `field_id` may be
                either a dict (parsed from JSON) or a typed FieldId instance.
            confidence_thresholds: Optional (medium, high) threshold tuple
                for translating confidence_level filters

        Returns:
            Tuple of (list of SQL clause strings, dict of named params)
        """
        clauses: List[str] = []
        params: Dict[str, Any] = {}

        for i, f in enumerate(filters):
            field_id_raw = f.get('field_id')
            op = f.get('op', '')
            value = f.get('value')
            if not field_id_raw or not op:
                continue

            try:
                field_id = coerce_field_id(field_id_raw)
            except Exception as exc:
                logger.warning(f"Skipping filter with invalid field_id {field_id_raw!r}: {exc}")
                continue

            param_key = f"f{i}"

            # Virtual confidence_level branches off before resolver — it maps
            # to a range condition on the `confidence` column, not to an
            # arbitrary SQL expression.
            if isinstance(field_id, VirtualField) and field_id.name == 'confidence_level':
                medium, high = confidence_thresholds or DEFAULT_CONFIDENCE_THRESHOLDS
                cl_clause = self._build_confidence_level_clause(
                    op, value, medium, high, param_key, params
                )
                if cl_clause:
                    clauses.append(cl_clause)
                continue

            # Virtual `alert` branches off before the resolver for the same
            # reason confidence_level does: the filter is not a predicate on the
            # projected expression. That expression is a correlated subquery
            # (>180s evaluated per row across assets); the clause below asks the
            # same question from the 50-row alerts side, measured at 33ms.
            if isinstance(field_id, VirtualField) and field_id.name == 'alert':
                alert_field_clause = self._build_alert_field_clause(
                    op, value, param_key, params
                )
                if alert_field_clause:
                    clauses.append(alert_field_clause)
                continue

            try:
                resolved = self._resolver.resolve(field_id)
            except InvalidFieldError as exc:
                logger.warning(f"Skipping filter with unresolvable field_id {field_id!r}: {exc}")
                continue

            col_expr = resolved.where_expr()
            is_array = resolved.is_native_array()
            is_jsonb = resolved.is_jsonb_sourced()

            self._current_resolved = resolved   # registry routing reads this
            clause = self._build_single_filter(
                col_expr, is_array, is_jsonb, op, value, param_key, params
            )
            if clause:
                # For StaticField("Data Source") filters, also match assets via
                # their asset_providers rows so a grouped integration's own
                # assets surface when filtered by the member name (the unified
                # assets.data_source stores the GROUP name post-collapse). The
                # guard resolves the operator spelling to a code first so label
                # spellings work and all/none (which never resolve to
                # eq/contains) don't trigger provider expansion.
                if (
                    isinstance(field_id, StaticField)
                    and field_id.name == "Data Source"
                    and value
                ):
                    try:
                        from src.modules.fields.operators import resolve as _rop
                        _code = _rop(op).code
                    except Exception:
                        _code = None
                    if _code in ("contains", "eq"):
                        provider_clause = self._build_provider_source_clause(
                            _code, value, param_key, params
                        )
                        if provider_clause:
                            clause = f"({clause} OR {provider_clause})"
                clauses.append(clause)

        return clauses, params

    def _build_single_filter(
        self, col: str, is_array: bool, is_jsonb: bool,
        op: str, value: Any, param_key: str, params: Dict[str, Any]
    ) -> Optional[str]:
        """Build one WHERE clause via the operator registry.

        Routing (scalar / native-array / jsonb-array / tag-boolean) is decided
        from the resolved field passed in via the caller's OperatorCtx. The
        legacy is_array/is_jsonb booleans are still accepted for signature
        back-compat but the registry reads the ResolvedField from ctx.field.
        """
        from src.modules.fields.operators import (
            resolve as resolve_operator, OperatorCtx, OperatorResolutionError,
        )
        try:
            operator = resolve_operator(op)
        except OperatorResolutionError:
            logger.warning("Skipping filter with unknown operator %r", op)
            return None
        # Drop binary-operator filters with no value (None or empty string) — an
        # empty value means "not provided", not a real filter (use the `empty`
        # operator to match empties). Matches the rules compiler; without it an
        # empty `contains` value would emit ILIKE '%%' and match every row.
        if operator.arity != "none" and (value is None or value == ""):
            return None
        ctx = OperatorCtx(field=self._current_resolved, param_key=param_key, params=params)
        frag, _ = operator.build_sql(col, value, ctx)
        return frag or None

    def _build_provider_source_clause(
        self, op: str, value: Any, param_key: str, params: Dict[str, Any]
    ) -> Optional[str]:
        """
        Build an additional clause matching assets that have an asset_providers
        row whose data_source matches the value.

        Integrations that belong to a group have their unified
        ``assets.data_source`` collapsed to the GROUP name during scoring
        (asset_scorer._collapse_groups), so filtering the array by a member's
        own name never matches. The per-provider contribution, however, is
        still recorded under the member name in ``asset_providers.data_source``.
        Matching through asset_providers lets a "Data Source" filter surface a
        grouped member's own assets — the same set the dashboard counts via
        asset_providers.integration_id. Ungrouped sources match here too (they
        already match the array clause), so this only ever widens the result to
        the intended rows.

        Case-folding (``eq``) and ILIKE-metachar escaping (``contains``) mirror
        the base array clause (src/modules/fields/operators.py) so the OR arm
        matches exactly the same values as the array arm — otherwise a
        case-variant ``eq`` or an underscore in the source name would make the
        two arms disagree.
        """
        from src.modules.fields.operators import _escape_like

        pk = f"{param_key}_prov"
        if op == 'eq':
            params[pk] = value
            return (
                f"EXISTS ("
                f"SELECT 1 FROM asset_providers ap "
                f"WHERE ap.asset_id = assets.id "
                f"AND LOWER(ap.data_source) = LOWER(%({pk})s)"
                f")"
            )
        if op == 'contains':
            params[pk] = f"%{_escape_like(value)}%"
            return (
                f"EXISTS ("
                f"SELECT 1 FROM asset_providers ap "
                f"WHERE ap.asset_id = assets.id "
                f"AND ap.data_source ILIKE %({pk})s ESCAPE '\\'"
                f")"
            )
        return None

    def _build_confidence_level_clause(
        self, op: str, value, medium: float, high: float,
        param_key: str, params: Dict[str, Any]
    ) -> Optional[str]:
        """Translate confidence_level filter to numeric range on confidence column."""

        def _level_condition(level: str, suffix: str) -> Optional[str]:
            level = level.lower().strip()
            if level == 'high':
                params[f'{param_key}_{suffix}_h'] = high
                return f"confidence >= %({param_key}_{suffix}_h)s"
            elif level == 'medium':
                params[f'{param_key}_{suffix}_m'] = medium
                params[f'{param_key}_{suffix}_mh'] = high
                return (
                    f"(confidence >= %({param_key}_{suffix}_m)s "
                    f"AND confidence < %({param_key}_{suffix}_mh)s)"
                )
            elif level == 'low':
                params[f'{param_key}_{suffix}_l'] = medium
                return (
                    f"(confidence < %({param_key}_{suffix}_l)s "
                    f"OR confidence IS NULL)"
                )
            return None

        if op == 'eq':
            return _level_condition(str(value), 'eq')
        elif op == 'any':
            values_list = (
                value if isinstance(value, list)
                else [v.strip() for v in str(value).split(',')]
            )
            conditions = []
            for j, v in enumerate(values_list):
                cond = _level_condition(v, f'any{j}')
                if cond:
                    conditions.append(cond)
            if conditions:
                return f"({' OR '.join(conditions)})"
        return None

    def _build_alert_field_clause(
        self, op: str, value, param_key: str, params: Dict[str, Any]
    ) -> Optional[str]:
        """Narrow to the assets an alert names, addressing it by NAME or by id.

        The alerts side MUST stay non-correlated with `assets` — i.e. no
        `assets.id = ANY(al.asset_ids)` inside the subquery. A correlated
        `EXISTS (SELECT 1 FROM alerts al WHERE assets.id = ANY(al.asset_ids)
        AND ...)` lets Postgres drive the scalar `?alert_id=` equality
        (`_build_alert_clause`) from `alerts_pkey`, but the moment the
        alerts-side predicate is `= ANY(array)` or a title match, the planner
        can no longer parameterize it and flips the join: `assets` becomes the
        OUTER relation and every row rescans the array. Measured on the dev
        DB against the widest open alert (35,136 of 73,740 assets), that
        correlated shape took 25,043 ms; the uncorrelated
        `assets.id IN (SELECT unnest(al.asset_ids) FROM alerts al WHERE ...)`
        below takes 56 ms for the identical result set. The frontend aborts
        at 30 s, so the correlated shape does not just run slow — it fails
        the drilldown outright. Do not "simplify" this back to EXISTS.

        At most one alert per rule is open at a time (AlertService
        ._evaluate_match_result updates it in place), while resolved rows with
        the same title accumulate. So a NAME means "the open alert for that
        rule" — hence the status predicate — and a drilldown from a RESOLVED
        card must send the id, which this also accepts.

        Values that parse as a uuid go to the id array, the rest to the title
        array. A malformed uuid therefore lands among the titles and matches
        nothing, instead of reaching Postgres as a bad ::uuid cast — psycopg2
        raises that as a non-ValueError which handle_errors answers with a 500
        (#973/#999). Filters arrive as JSON and bypass the blueprint's
        ensure_uuid(), so this split IS that guard.

        `None` is skipped rather than stringified, both as the whole value and
        per list element, since it means "nothing selected" and str(None) is
        a non-blank "None" that would otherwise pass as a literal title.

        Both arrays are bound; `= ANY('{}')` is simply false, so neither half
        needs a special case when every value lands on the other side.
        """
        if op not in ('eq', 'any'):
            return None

        if value is None:
            return None

        raw = value if isinstance(value, list) else [value]
        ids: List[str] = []
        titles: List[str] = []
        for v in raw:
            if v is None:
                continue
            s = str(v).strip()
            if not s:
                continue
            try:
                UUID(s)
                ids.append(s)
            except (ValueError, AttributeError, TypeError):
                titles.append(s.lower())

        if not ids and not titles:
            return None

        params[f'{param_key}_ids'] = ids
        params[f'{param_key}_titles'] = titles
        return (
            "assets.id IN (SELECT unnest(al.asset_ids) FROM alerts al "
            f"WHERE al.id = ANY(%({param_key}_ids)s::uuid[]) "
            "OR (al.status IN ('active','acknowledged') "
            f"AND lower(al.title) = ANY(%({param_key}_titles)s)))"
        )

    # -----------------------------------------------------------------
    # Distinct values for field suggestions / column filter dropdowns
    # -----------------------------------------------------------------

    def get_field_values(
        self,
        field: str,
        limit: int = 50,
        search: Optional[str] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
        logic: str = "and"
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Get distinct values and counts for a field.

        Args:
            field: Encoded FieldId string (e.g. 'static:Hostname')
            limit: Max values to return
            search: Optional prefix filter on values
            filters: Optional structured filters to narrow context
                     (each filter has typed field_id + op + value)

        Returns:
            Tuple of (list of {value, count} dicts, total distinct count)
        """
        params: Dict[str, Any] = {'limit': limit}

        # Decode first: the alert branch below must be reached BEFORE resolve(),
        # because resolving would hand the generic path a correlated subquery to
        # run DISTINCT over every asset row.
        try:
            field_id = decode_field_id(field)
        except ValueError as exc:
            logger.warning(f"get_field_values: cannot decode field {field!r}: {exc}")
            return [], 0

        if isinstance(field_id, VirtualField) and field_id.name == 'alert':
            return self._get_alert_field_values(limit, search)

        try:
            resolved = self._resolver.resolve(field_id)
        except (ValueError, InvalidFieldError) as exc:
            logger.warning(f"get_field_values: cannot resolve field {field!r}: {exc}")
            return [], 0

        col_expr = resolved.where_expr()
        is_array = resolved.is_native_array()
        is_jsonb = resolved.is_jsonb_sourced()

        # Build WHERE from contextual filters (combined with the flat logic).
        where_clauses = []
        if filters:
            filter_clauses, filter_params = self._build_filter_clauses(filters)
            where_clauses.extend(self._join_filter_clauses(filter_clauses, logic))
            params.update(filter_params)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        # Detect JSONB array fields (use -> not ->>, so array type is preserved)
        is_jsonb_array = is_jsonb and "->>'" not in col_expr

        if is_jsonb_array:
            # Unnest JSONB array via jsonb_array_elements_text
            search_clause = ""
            if search:
                search_clause = "AND x.value ILIKE %(val_search)s"
                params['val_search'] = f"{search}%"

            base_where = where_sql if where_sql else "WHERE 1=1"

            query = f"""
                SELECT x.value AS value, COUNT(*) AS count
                FROM assets, jsonb_array_elements_text({col_expr}) x
                {base_where}
                AND {col_expr} IS NOT NULL
                {search_clause}
                GROUP BY x.value
                ORDER BY count DESC, x.value ASC
                LIMIT %(limit)s
            """

            count_query = f"""
                SELECT COUNT(DISTINCT x.value) AS total
                FROM assets, jsonb_array_elements_text({col_expr}) x
                {base_where}
                AND {col_expr} IS NOT NULL
            """

        elif is_array:
            # Unnest array, then group
            value_expr = "x"
            from_clause = f"(SELECT unnest({col_expr}) AS x FROM assets {where_sql}) sub"
            where_value = ""
            if search:
                where_value = "WHERE x ILIKE %(val_search)s"
                params['val_search'] = f"{search}%"

            query = f"""
                SELECT x AS value, COUNT(*) AS count
                FROM {from_clause}
                {where_value}
                AND x IS NOT NULL AND x != ''
                GROUP BY x
                ORDER BY count DESC, x ASC
                LIMIT %(limit)s
            """
            # Fix: if no where_value, the AND needs to be WHERE
            if not search:
                query = f"""
                    SELECT x AS value, COUNT(*) AS count
                    FROM {from_clause}
                    WHERE x IS NOT NULL AND x != ''
                    GROUP BY x
                    ORDER BY count DESC, x ASC
                    LIMIT %(limit)s
                """

            count_query = f"""
                SELECT COUNT(DISTINCT x) AS total
                FROM {from_clause}
                WHERE x IS NOT NULL AND x != ''
            """
        else:
            # Scalar column
            having_clause = ""
            search_clause = ""
            if search:
                search_clause = f"AND {col_expr}::text ILIKE %(val_search)s"
                params['val_search'] = f"{search}%"

            base_where = where_sql if where_sql else "WHERE 1=1"

            query = f"""
                SELECT {col_expr} AS value, COUNT(*) AS count
                FROM assets
                {base_where}
                AND {col_expr} IS NOT NULL AND {col_expr}::text != ''
                {search_clause}
                GROUP BY {col_expr}
                ORDER BY count DESC, {col_expr} ASC
                LIMIT %(limit)s
            """

            count_query = f"""
                SELECT COUNT(DISTINCT {col_expr}) AS total
                FROM assets
                {base_where}
                AND {col_expr} IS NOT NULL AND {col_expr}::text != ''
            """

        results = self.db.execute_query(query, params)
        count_result = self.db.execute_query(count_query, params)
        total_distinct = count_result[0]['total'] if count_result else 0

        values = [{'value': str(r['value']), 'count': r['count']} for r in (results or [])]
        return values, total_distinct

    def _get_alert_field_values(
        self, limit: int, search: Optional[str]
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Alert names for the search bar's value dropdown.

        Reads `alerts` (50 rows on the dev DB) and never touches `assets`: this
        field's catalog expression is a correlated subquery, so the generic
        DISTINCT path would evaluate it once per asset row. `asset_count` is
        denormalised on the alert row, so no join is needed either.

        Only OPEN alerts with assets are offered, matching exactly what
        `Alert = "<name>"` can select (_build_alert_field_clause).

        The contextual `filters` narrowing other fields honour is deliberately
        NOT applied — counts here are each alert's own total, not a count within
        the rest of the query. Distinct open titles number in the tens, so the
        first page is the whole set and `len(values)` is the true total.
        """
        params: Dict[str, Any] = {'limit': limit}
        search_clause = ""
        if search:
            search_clause = "AND title ILIKE %(val_search)s"
            params['val_search'] = f"%{search}%"

        query = f"""
            SELECT title AS value, SUM(asset_count)::int AS count
            FROM alerts
            WHERE status IN ('active','acknowledged')
              AND asset_count > 0
              {search_clause}
            GROUP BY title
            ORDER BY count DESC, title ASC
            LIMIT %(limit)s
        """
        rows = self.db.execute_query(query, params) or []
        values = [{'value': r['value'], 'count': r['count']} for r in rows]
        return values, len(values)

    # -----------------------------------------------------------------
    # Aggregation for pivot table
    # -----------------------------------------------------------------

    # Aggregate functions the chat query_assets aggregate mode may request.
    # COUNT is the default (and the only one that needs no metric field).
    VALID_AGGREGATES = {"COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX"}
    _NUMERIC_AGGREGATES = {"SUM", "AVG", "MIN", "MAX"}

    @staticmethod
    def _join_filter_clauses(
        filter_clauses: List[str], logic: str = "and"
    ) -> List[str]:
        """Combine structured-filter sub-clauses with the requested flat logic.

        Returns the WHERE terms to ``extend`` a method's ``where_clauses`` with.
        For ``and`` (default) the clauses pass through UNCHANGED — byte-identical
        to the historical ``where_clauses.extend(filter_clauses)``, so Explorer /
        asset_service / MCP callers see exactly the same SQL. For ``or`` (and 2+
        clauses) they collapse into a single parenthesized OR group, so the
        filter set OR-combines internally while still AND-ing with any
        search/category term (correct precedence; flat AND/OR only — §8)."""
        if not filter_clauses:
            return []
        if (logic or "and").lower() == "or" and len(filter_clauses) > 1:
            return ["(" + " OR ".join(filter_clauses) + ")"]
        return list(filter_clauses)

    def _aggregate_select_expr(
        self, agg: str, metric_field: Optional[str]
    ) -> str:
        """Build the SQL aggregate expression for ``aggregate_assets``.

        ``COUNT`` → ``COUNT(*)`` (no metric). Every other function aggregates
        the resolved ``metric_field`` column. Raises ``ValueError`` — never
        emits degraded SQL — for an unknown function, a missing metric, or a
        numeric aggregate over an array column (``SUM(text[])`` is invalid).
        Whitelisted function names + a resolver-trusted column expression keep
        this safe to interpolate (no user string reaches a SQL position)."""
        agg_norm = (agg or "COUNT").upper()
        if agg_norm not in self.VALID_AGGREGATES:
            raise ValueError(
                f"Unknown aggregate function {agg!r}. "
                f"Valid: {sorted(self.VALID_AGGREGATES)}."
            )
        if agg_norm == "COUNT":
            return "COUNT(*)"
        if not metric_field:
            raise ValueError(
                f"Aggregate {agg_norm} requires a metric_field."
            )
        metric_resolved = self._resolver.resolve(decode_field_id(metric_field))
        metric_expr = metric_resolved.where_expr()
        if agg_norm == "COUNT_DISTINCT":
            return f"COUNT(DISTINCT {metric_expr})"
        if metric_resolved.is_native_array():
            raise ValueError(
                f"Aggregate {agg_norm} needs a scalar numeric field; "
                f"{metric_field!r} is an array."
            )
        return f"{agg_norm}({metric_expr})"

    def aggregate_assets(
        self,
        row_field: str,
        col_field: Optional[str] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
        *,
        agg: str = "COUNT",
        metric_field: Optional[str] = None,
        logic: str = "and",
        alert_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Aggregate assets by one or two dimensions.

        Args:
            row_field: Encoded FieldId string to group rows by
            col_field: Optional encoded FieldId string to group columns by
            filters: Optional structured filters (typed field_id + op + value)
            agg: Aggregate function (COUNT default; COUNT_DISTINCT/SUM/AVG/MIN/
                MAX aggregate ``metric_field``). See VALID_AGGREGATES.
            metric_field: Encoded FieldId the non-COUNT aggregates operate on.
            logic: Flat boolean combiner for ``filters`` ('and' default | 'or').
            alert_id: When set, narrow the aggregate to exactly the assets an
                alert names (``alerts.asset_ids``), so a pivot export under an
                Explorer drilldown aggregates the same set the table shows rather
                than the whole fleet (#1013). ANDs outside the filter OR group so
                it can only narrow. Must already be canonical (see
                _build_alert_clause); the handler runs it through ensure_uuid().

        Returns:
            Dict with rows, columns (if 2D), data matrix, grand_total. Cell
            values are the aggregate result (a count for COUNT, otherwise the
            SUM/AVG/MIN/MAX/distinct-count of ``metric_field``).
        """
        params: Dict[str, Any] = {}

        # Resolve the aggregate function BEFORE touching the DB so a bad
        # agg/metric combination raises loudly here instead of as an opaque
        # SQL error mid-query.
        agg_expr = self._aggregate_select_expr(agg, metric_field)

        # Build WHERE from filters (combined with the requested flat logic).
        where_clauses = []

        # ANDs with whatever the filters ask for even when they OR among
        # themselves, so it can only narrow — the alert clause lands outside the
        # parenthesised OR group _join_filter_clauses builds below, exactly as
        # list_assets and get_matching_asset_ids place it (#1013).
        if alert_id:
            alert_clause, alert_params = self._build_alert_clause(alert_id)
            where_clauses.append(alert_clause)
            params.update(alert_params)

        if filters:
            filter_clauses, filter_params = self._build_filter_clauses(filters)
            where_clauses.extend(self._join_filter_clauses(filter_clauses, logic))
            params.update(filter_params)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        # Resolve row field from encoded string
        try:
            row_field_id = decode_field_id(row_field)
        except ValueError as exc:
            logger.warning(f"aggregate_assets: cannot decode row_field {row_field!r}: {exc}")
            return {'rows': [], 'columns': [], 'data': {}, 'grand_total': 0}

        # A filter-only field's expression is a correlated subquery; grouping by
        # it evaluates that once per asset row (>180s on the dev DB). Answer
        # empty rather than hanging a worker, the same way the unresolvable
        # branch below does.
        if is_filter_only_field(row_field_id):
            logger.warning(
                f"aggregate_assets: {row_field!r} is filter-only and cannot be grouped"
            )
            return {'rows': [], 'columns': [], 'data': {}, 'grand_total': 0}

        try:
            row_resolved = self._resolver.resolve(row_field_id)
        except (ValueError, InvalidFieldError) as exc:
            logger.warning(f"aggregate_assets: cannot resolve row_field {row_field!r}: {exc}")
            return {'rows': [], 'columns': [], 'data': {}, 'grand_total': 0}

        row_expr = row_resolved.where_expr()
        row_is_array = row_resolved.is_native_array()

        # For array fields, use unnest to get individual values
        if row_is_array:
            row_select = f"unnest({row_expr})"
        else:
            row_select = row_expr

        col_expr = None
        col_is_array = False
        if col_field:
            try:
                col_field_id = decode_field_id(col_field)
                # Same reason as the row field above — a filter-only column
                # dimension is the identical per-row subquery, just on the other
                # axis. Empty rather than a partial pivot: silently dropping the
                # axis would answer a question nobody asked.
                if is_filter_only_field(col_field_id):
                    logger.warning(
                        f"aggregate_assets: {col_field!r} is filter-only and "
                        "cannot be grouped"
                    )
                    return {'rows': [], 'columns': [], 'data': {}, 'grand_total': 0}
                col_resolved = self._resolver.resolve(col_field_id)
                col_expr = col_resolved.where_expr()
                col_is_array = col_resolved.is_native_array()
            except (ValueError, InvalidFieldError) as exc:
                logger.warning(
                    f"aggregate_assets: cannot resolve col_field {col_field!r}: {exc}"
                )
                col_field = None
                col_expr = None

        if col_field and col_expr:
            if col_is_array:
                col_select = f"unnest({col_expr})"
            else:
                col_select = col_expr

            query = f"""
                SELECT
                    {row_select} AS row_val,
                    {col_select} AS col_val,
                    {agg_expr} AS count
                FROM assets
                {where_sql}
                GROUP BY row_val, col_val
                ORDER BY row_val, col_val
            """
        else:
            query = f"""
                SELECT
                    {row_select} AS row_val,
                    {agg_expr} AS count
                FROM assets
                {where_sql}
                GROUP BY row_val
                ORDER BY count DESC, row_val
            """

        results = self.db.execute_query(query, params)
        if not results:
            return {'rows': [], 'columns': [], 'data': {}, 'grand_total': 0}

        # Build response structure
        if col_field:
            rows_set = set()
            cols_set = set()
            data: Dict[str, Dict[str, int]] = {}
            grand_total = 0

            for r in results:
                row_val = str(r['row_val']) if r['row_val'] is not None else '(empty)'
                col_val = str(r['col_val']) if r['col_val'] is not None else '(empty)'
                count = r['count']

                rows_set.add(row_val)
                cols_set.add(col_val)
                data.setdefault(row_val, {})[col_val] = count
                if count is not None:
                    grand_total += count

            return {
                'rows': sorted(rows_set),
                'columns': sorted(cols_set),
                'data': data,
                'grand_total': grand_total
            }
        else:
            rows = []
            data = {}
            grand_total = 0

            for r in results:
                row_val = str(r['row_val']) if r['row_val'] is not None else '(empty)'
                count = r['count']
                rows.append(row_val)
                data[row_val] = {'count': count}
                if count is not None:
                    grand_total += count

            return {
                'rows': rows,
                'columns': [],
                'data': data,
                'grand_total': grand_total
            }

    def get_asset_count(self) -> int:
        """
        Get total asset count.

        Returns:
            Total number of assets
        """
        query = "SELECT COUNT(*) as total FROM assets"
        result = self.db.execute_query(query, {})

        return result[0]['total'] if result else 0

    # --- External API query ---

    # Fields that the external API is allowed to SELECT
    EXTERNAL_FIELDS = {
        'id', 'hostname', 'fqdn', 'ip', 'mac', 'sn',
        'os_normalized', 'os', 'os_orig',
        'category', 'subcategory',
        'model', 'manufacturer', 'mac_vendor_enriched',
        'owner', 'location', 'user_field',
        'data_source', 'data_source_category', 'vendor_category',
        'confidence', 'confidence_breakdown',
        'ipam_location', 'extended_attributes',
        'first_time', 'last_time', 'first_seen', 'last_seen', 'updated_at',
    }

    DEFAULT_EXTERNAL_FIELDS = [
        'id', 'hostname', 'fqdn', 'ip', 'mac', 'sn',
        'os_normalized', 'category', 'subcategory',
        'model', 'manufacturer', 'owner', 'location',
        'confidence', 'first_seen', 'last_seen',
    ]

    def query_assets(
        self,
        ip: str = None,
        hostname: str = None,
        fqdn: str = None,
        mac: str = None,
        sn: str = None,
        q: str = None,
        fields: list = None,
        limit: int = 10,
    ) -> list:
        """
        Query assets by one or more filters for the external API.

        Specific filters (ip, hostname, etc.) combine with AND.
        Free-text `q` searches all fields (OR) and is mutually exclusive
        with specific filters.
        """
        # Determine SELECT columns
        if fields:
            select_cols = [f for f in fields if f in self.EXTERNAL_FIELDS]
            if not select_cols:
                select_cols = list(self.DEFAULT_EXTERNAL_FIELDS)
        else:
            select_cols = list(self.DEFAULT_EXTERNAL_FIELDS)

        # Always include 'id'
        if 'id' not in select_cols:
            select_cols.insert(0, 'id')

        select_clause = ', '.join(select_cols)

        conditions = []
        params = {'limit': limit}

        if q:
            # Free-text: search across all common fields
            conditions.append("""(
                EXISTS (SELECT 1 FROM unnest(hostname) AS h WHERE h ILIKE %(q_like)s)
                OR EXISTS (SELECT 1 FROM unnest(fqdn) AS f WHERE f ILIKE %(q_like)s)
                OR EXISTS (SELECT 1 FROM unnest(ip) AS i WHERE i = %(q_exact)s)
                OR EXISTS (SELECT 1 FROM unnest(mac) AS m WHERE m ILIKE %(q_like)s)
                OR EXISTS (SELECT 1 FROM unnest(sn) AS s WHERE s ILIKE %(q_like)s)
            )""")
            params['q_like'] = f'%{q}%'
            params['q_exact'] = q
        else:
            if ip:
                conditions.append(
                    "EXISTS (SELECT 1 FROM unnest(ip) AS i WHERE i = %(ip)s)"
                )
                params['ip'] = ip
            if hostname:
                conditions.append(
                    "EXISTS (SELECT 1 FROM unnest(hostname) AS h WHERE h ILIKE %(hostname)s)"
                )
                params['hostname'] = f'%{hostname}%'
            if fqdn:
                conditions.append(
                    "EXISTS (SELECT 1 FROM unnest(fqdn) AS f WHERE f ILIKE %(fqdn)s)"
                )
                params['fqdn'] = f'%{fqdn}%'
            if mac:
                conditions.append(
                    "EXISTS (SELECT 1 FROM unnest(mac) AS m WHERE m = %(mac)s)"
                )
                params['mac'] = mac
            if sn:
                conditions.append(
                    "EXISTS (SELECT 1 FROM unnest(sn) AS s WHERE s ILIKE %(sn)s)"
                )
                params['sn'] = f'%{sn}%'

        where_clause = ' AND '.join(conditions) if conditions else '1=1'

        # Count total matches (before LIMIT)
        count_sql = f"SELECT COUNT(*) AS total FROM assets WHERE {where_clause}"
        count_result = self.db.execute_query(count_sql, params)
        total = count_result[0]['total'] if count_result else 0

        sql = f"""
            SELECT {select_clause}
            FROM assets
            WHERE {where_clause}
            ORDER BY last_seen DESC NULLS LAST
            LIMIT %(limit)s
        """

        rows = self.db.execute_query(sql, params)
        return rows, total

    def get_tags_for_assets(self, asset_ids: list) -> dict:
        """Batch-fetch tags for multiple assets. Returns {asset_id: [tag_dicts]}."""
        if not asset_ids:
            return {}
        sql = """
            SELECT
                at.asset_id::text,
                at.tag_name,
                at.tag_type,
                CASE
                    WHEN tr.type = 'classification' THEN tr.tags->>'color'
                    WHEN tr.type = 'compliance' AND at.tag_type = 'pass' THEN '#10b981'
                    WHEN tr.type = 'compliance' AND at.tag_type = 'fail' THEN '#ef4444'
                    ELSE '#64748b'
                END as color
            FROM asset_tags_v2 at
            LEFT JOIN tag_rules tr ON at.tag_rule_id = tr.id
            WHERE at.asset_id = ANY(%(ids)s::uuid[])

            UNION ALL

            SELECT
                mt.asset_id::text,
                mt.tag_name,
                'manual' as tag_type,
                COALESCE(mt.tag_color, '#64748b') as color
            FROM asset_manual_tags mt
            WHERE mt.asset_id = ANY(%(ids)s::uuid[])

            ORDER BY tag_name
        """
        rows = self.db.execute_query(sql, {'ids': asset_ids})
        result = {}
        for row in rows:
            aid = row['asset_id']
            result.setdefault(aid, []).append({
                'name': row['tag_name'], 'color': row['color'], 'type': row['tag_type'],
            })
        return result
