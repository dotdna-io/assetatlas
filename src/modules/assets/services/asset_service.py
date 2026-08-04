# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Service

Business logic for asset inventory operations.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
import logging
import re

from flask import current_app

from src.api.modules.settings.settings_schema import (
    DEFAULT_CONFIDENCE_THRESHOLDS,
    get_confidence_thresholds,
)
from src.modules.assets.repositories.asset_repository import AssetRepository
from src.modules.alerts.repository import AlertsRepository
from src.modules.fields.encoding import decode as decode_field_id
from src.modules.fields.models import VirtualField
from src.modules.assets.models.asset import (
    AssetDetail,
    AssetDetailResponse,
    AssetProvider,
    AssetStatus,
    AssetTag,
    ConfidenceLevel,
    OtherSource,
    ProviderFieldSection,
    AssetListItem,
    AssetListResponse,
    AssetSearchResult,
    AssetSearchResponse
)

logger = logging.getLogger(__name__)


# Mapping of integration types to emoji icons
SOURCE_ICONS = {
    'jamf': '🍎',
    'vcenter': '☁️',
    'crowdstrike': '🦅',
    'servicenow': '⚙️',
    'intune': '📱',
    'nessus': '🔍',
    'lansweeper': '🖥️',
    'active_directory': '🗂️',
    'qualys': '🛡️',
    'splunk': '📊',
    'default': '📦'
}


# Merge-key decoding for the merge-keys viewer: classifies a raw
# asset_id_mappings.component string into the identifier type it represents,
# so an operator can see WHICH identifier glued this asset together. Mirrors
# how the pipeline writes components (asset_validator.py /
# asset_generation_service.py): serial numbers are "sn:"-prefixed, MAC/
# hostname/FQDN are written bare.
_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def decode_merge_component(component: str):
    """Classify a raw asset_id_mappings component into (type, value)."""
    c = str(component)
    if c.startswith("sn:"):
        return ("serial", c[3:])
    if _MAC_RE.match(c):
        return ("mac", c)
    if "." in c:
        return ("fqdn", c)
    return ("hostname", c)


# How far behind an asset's OWN newest sighting a component may sit and still
# count as active. Anchoring to the asset rather than to wall-clock now is what
# makes this work: a pipeline run stamps last_seen on every component in its
# snapshot, so components that are all still reported share a stamp and their
# gaps cancel, and the tell survives a pipeline that is behind. (On the dev DB
# the newest stamp anywhere is 9 days old and the pipeline paused for 13.4 days
# over 06-24..07-08; a now-anchored rule would have flagged nothing at all.)
#
# The value is deliberately not fitted: across the dev DB's 159509 components
# every still-reported one sits at a gap of exactly 0.0000d and every one that
# has gone quiet at >= 14.09d, so the band between is empty and every value in
# it returns the identical verdict. 7d is that band's midpoint. Source cadence
# fixes the order of magnitude — all 34 integrations sync on a daily cron, so a
# source must go quiet for a week before its identifiers read as stale.
#
# MUST stay below the `mapping_expiration_days` setting (default 30, min 7):
# stage 0 deletes every mapping inactive for longer than that, so a gone-quiet
# component only exists to be recognised inside the band (7d, that setting] —
# which is why the observed tail stops at 28.8d rather than running on. Set the
# two equal (both 7) and every surviving mapping falls inside this tolerance,
# every component reads active, and the tell silently decays into the per-type
# count #973 rejected. Nothing enforces the gap; it is a config-time contract.
_CONCURRENT_WITHIN = timedelta(days=7)

# Types where one live value should mean one device, so two live at once means
# two devices got glued together. MAC is excluded: a machine legitimately has
# several NICs, so concurrent MACs are the normal case rather than a tell —
# counting them flagged 6435/77964 dev-DB assets (8.25%), of which 5729 (89%)
# were single-hostname single-serial laptops reporting Ethernet plus Wi-Fi.
# Without MAC the tell fires on 706 (0.91%). Add 'mac' here to reverse that.
_IDENTIFYING_TYPES = ('hostname', 'fqdn', 'serial')


def concurrent_identifiers(components):
    """
    Find the identity types with 2+ values still being reported at once.

    Concurrency rather than a count is what tells the two ways an asset ends up
    with several values of one type apart: a rename leaves them SEQUENTIAL (the
    old hostname stops being reported as the new one starts), while an
    over-merge leaves them CONCURRENT (both machines keep reporting their own
    name, so both stay live). Counting alone would flag every renamed host,
    which is why the tell was serial-only until #973.

    Args:
        components: iterable of (type, value, last_seen) decoded from
            asset_id_mappings rows.

    Returns:
        {type: sorted values} for each type with 2+ concurrently-active values
        — the reason an asset looks over-merged — or {} when none does.
    """
    # A component with no last_seen is no evidence of activity, so it cannot
    # make anything concurrent. asset_id_mappings.last_seen is NOT NULL, so
    # this only guards rows built by hand.
    seen = [(t, v, ls) for t, v, ls in components if ls is not None]
    if not seen:
        return {}

    # "Last sighting" spans every type, MAC included: a live MAC still proves
    # the asset is being reported, it just does not vote on the verdict.
    newest = max(ls for _, _, ls in seen)

    active: Dict[str, set] = {}
    for ktype, value, last_seen in seen:
        if ktype in _IDENTIFYING_TYPES and newest - last_seen <= _CONCURRENT_WITHIN:
            active.setdefault(ktype, set()).add(value)

    return {t: sorted(v) for t, v in active.items() if len(v) > 1}


class AssetService:
    """Service for asset inventory operations"""

    def __init__(
        self,
        repository: AssetRepository,
        alerts_repository: Optional[AlertsRepository] = None,
    ):
        """
        Initialize service with repositories.

        Args:
            repository: Asset repository instance
            alerts_repository: Alerts repository used to count active alerts for
                the asset-detail header badge. Optional (defaults to None) so
                existing single-repository callers still construct the service;
                when absent, alert_count falls back to 0.
        """
        self.repo = repository
        self.alerts_repo = alerts_repository

    def get_asset_detail(self, asset_id: str) -> Optional[AssetDetailResponse]:
        """
        Get detailed information for a specific asset.

        Args:
            asset_id: Asset UUID

        Returns:
            AssetDetailResponse or None if not found
        """
        # Get asset from database
        asset_data = self.repo.get_asset_by_id(asset_id)

        if not asset_data:
            return None

        # Get tags
        tags = self.repo.get_asset_tags(asset_id)

        # Get providers
        providers_data = self.repo.get_asset_providers(asset_id)

        # Build asset detail
        asset_detail = self._build_asset_detail(asset_data, tags)

        # Build provider list
        providers = self._build_providers(providers_data)

        # Get other sources for same category that didn't contribute to this asset
        other_sources = []
        category = asset_data.get('category')
        if category:
            provider_names = [p.source_name for p in providers]
            other_sources_data = self.repo.get_other_sources_for_category(
                category, provider_names
            )
            for src in other_sources_data:
                logo = src.get('logo')
                if logo:
                    icon = f'/logos/{logo}'
                else:
                    src_type = (src.get('integration_type') or 'unknown').lower()
                    icon = SOURCE_ICONS.get(src_type, SOURCE_ICONS['default'])
                other_sources.append(OtherSource(
                    source_name=src['name'],
                    source_icon=icon,
                    source_type=src.get('integration_type') or 'unknown',
                    asset_count=src.get('asset_count', 0)
                ))

        return AssetDetailResponse(
            success=True,
            asset=asset_detail,
            providers=providers,
            other_sources=other_sources
        )

    def get_merge_keys(self, asset_id: str) -> dict:
        """
        Get the decoded merge-key components that were glued together to
        form this asset, plus a "tell" flagging likely over-merging.

        Read-only diagnostic for the "Don't trust this identifier" workflow:
        an operator inspects which component (serial/mac/fqdn/hostname) is
        the bad merge key before denylisting it. over_merged fires on 2+
        concurrently-active values of one identity type — see
        concurrent_identifiers() for why concurrency and not a count. The
        badge is a prompt for a human, not a verdict.

        Args:
            asset_id: Asset UUID

        Returns:
            Dict with 'keys' (list of {type, value, first_seen, last_seen})
            and 'tell' ({serial_count, over_merged, concurrent}), where
            'concurrent' is {type: [values]} naming why over_merged is set.
        """
        rows = self.repo.get_merge_keys(asset_id)
        keys = []
        observed = []
        serial_count = 0
        for r in rows:
            ktype, kval = decode_merge_component(r['component'])
            if ktype == 'serial':
                serial_count += 1
            observed.append((ktype, kval, r['last_seen']))
            keys.append({
                'type': ktype,
                'value': kval,
                'first_seen': r['first_seen'].isoformat() if r['first_seen'] else None,
                'last_seen': r['last_seen'].isoformat() if r['last_seen'] else None,
            })
        concurrent = concurrent_identifiers(observed)
        return {
            'keys': keys,
            'tell': {
                'serial_count': serial_count,
                'over_merged': bool(concurrent),
                'concurrent': concurrent,
            },
        }

    def list_assets(
        self,
        page: int = 1,
        limit: int = 50,
        category: Optional[str] = None,
        search: Optional[str] = None,
        fields: Optional[List[str]] = None,
        alert_id: Optional[str] = None
    ) -> AssetListResponse:
        """
        List assets with pagination and filtering.

        Args:
            page: Page number
            limit: Results per page
            category: Optional category filter
            search: Optional search term
            fields: Optional list of field keys to include (for Explorer dynamic columns)
            alert_id: Optional alert UUID — narrow to the assets that alert names

        Returns:
            AssetListResponse with paginated results
        """
        # Get assets and total count
        assets_data, total = self.repo.list_assets(
            page, limit, category, search, fields, alert_id=alert_id
        )

        # Build asset list items
        thresholds = self._get_confidence_thresholds()
        assets = []
        for asset_data in assets_data:
            # Get tags for each asset
            tags_data = self.repo.get_asset_tags(str(asset_data['id']))

            # Convert tag dicts to AssetTag objects
            tag_objects = [
                AssetTag(
                    name=tag['tag_name'],
                    color=tag['color'],
                    type=tag['tag_type']
                )
                for tag in tags_data
            ] if tags_data else []

            # Convert IP/MAC arrays to lists and extract first hostname
            hostname_arr = self._parse_array_field(asset_data.get('hostname'))
            hostname = hostname_arr[0] if hostname_arr else None
            ip_list = self._parse_array_field(asset_data.get('ip'))

            confidence_val = float(asset_data.get('confidence', 0.0))
            assets.append(AssetListItem(
                id=asset_data['id'],
                hostname=hostname,
                category=asset_data.get('category'),
                os=asset_data.get('os_normalized'),
                ip=ip_list,
                confidence=confidence_val,
                confidence_level=self._calculate_confidence_level(confidence_val, thresholds).value,
                last_seen=asset_data.get('last_time'),
                tags=tag_objects
            ))

        # Calculate pagination info
        pages = (total + limit - 1) // limit if total > 0 else 0

        return AssetListResponse(
            success=True,
            assets=assets,
            pagination={
                'total': total,
                'page': page,
                'limit': limit,
                'pages': pages
            }
        )

    def list_assets_dynamic(
        self,
        page: int = 1,
        limit: int = 50,
        category: Optional[str] = None,
        search: Optional[str] = None,
        fields: Optional[List[str]] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
        alert_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        List assets with dynamic field selection for Explorer.

        Unlike list_assets(), this returns raw data with any requested fields
        rather than the fixed AssetListItem structure.

        Args:
            page: Page number
            limit: Results per page
            category: Optional category filter
            search: Optional search term
            fields: List of field keys to include
            sort_by: Optional column to sort by
            sort_dir: Sort direction ('asc' or 'desc')
            filters: Optional list of structured filter dicts
            alert_id: Optional alert UUID — narrow to the assets that alert names

        Returns:
            Dict with assets (raw data), pagination info
        """
        # Get assets and total count with dynamic fields
        medium, high = self._get_confidence_thresholds()
        assets_data, total = self.repo.list_assets(
            page, limit, category, search, fields,
            sort_by=sort_by, sort_dir=sort_dir, filters=filters,
            confidence_thresholds=(medium, high), alert_id=alert_id
        )

        # Process the raw data
        assets = []
        for asset_data in assets_data:
            # Convert to regular dict if needed
            asset_dict = dict(asset_data) if hasattr(asset_data, 'items') else asset_data
            conf = float(asset_dict.get('confidence', 0.0))
            asset_dict['confidence_level'] = self._calculate_confidence_level(conf, (medium, high)).value
            assets.append(asset_dict)

        # Calculate pagination info
        pages = (total + limit - 1) // limit if total > 0 else 0

        return {
            'success': True,
            'assets': assets,
            'pagination': {
                'total': total,
                'page': page,
                'limit': limit,
                'pages': pages
            }
        }

    def get_matching_asset_ids(
        self,
        filters: Optional[List[Dict[str, Any]]] = None,
        search: Optional[str] = None,
        category: Optional[str] = None,
        logic: str = "and",
        limit: Optional[int] = None,
        alert_id: Optional[str] = None,
    ) -> List[str]:
        """Resolve the ids of all assets matching a filter, using the same
        confidence thresholds the Explorer list uses so the set matches the UI.

        ``alert_id`` narrows to the assets that alert names (#1011), so a bulk
        action from an alert drilldown targets exactly the set the count shows.
        """
        medium, high = self._get_confidence_thresholds()
        return self.repo.get_matching_asset_ids(
            filters=filters,
            search=search,
            category=category,
            logic=logic,
            limit=limit,
            confidence_thresholds=(medium, high),
            alert_id=alert_id,
        )

    def get_field_values(
        self,
        field: str,
        limit: int = 50,
        search: Optional[str] = None,
        filters: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Get distinct values and counts for a field.

        Args:
            field: Encoded FieldId string (e.g. 'static:Hostname', 'virtual:confidence_level')
            limit: Max values to return
            search: Optional prefix filter on values
            filters: Optional structured filters for context (typed field_id)

        Returns:
            Dict with field, values list, total_distinct
        """
        # Static enum values for the virtual confidence_level field.
        # Decode to a typed FieldId so the check is format-independent.
        field_id = decode_field_id(field)
        if isinstance(field_id, VirtualField) and field_id.name == 'confidence_level':
            static_values = [
                {'value': 'high', 'count': 0},
                {'value': 'medium', 'count': 0},
                {'value': 'low', 'count': 0},
            ]
            return {
                'field': field,
                'values': static_values,
                'total_distinct': 3
            }

        values, total_distinct = self.repo.get_field_values(
            field, limit, search, filters
        )

        return {
            'field': field,
            'values': values,
            'total_distinct': total_distinct
        }

    def aggregate_assets(
        self,
        row_field: str,
        col_field: Optional[str] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
        alert_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Aggregate assets by one or two dimensions for pivot table.

        Args:
            row_field: Field to group rows by
            col_field: Optional field to group columns by
            filters: Optional structured filters
            alert_id: Optional alert UUID — narrow the aggregate to that alert's
                assets so a pivot export under an Explorer drilldown matches the
                scoped table rather than the whole fleet (#1013).

        Returns:
            Dict with rows, columns, data matrix, grand_total
        """
        return self.repo.aggregate_assets(
            row_field, col_field, filters, alert_id=alert_id
        )

    def search_assets(
        self,
        query: str,
        field: Optional[str] = None,
        limit: int = 10,
    ) -> AssetSearchResponse:
        """
        Search for assets by hostname, IP, MAC, or serial number.

        The current implementation performs free-text search across a fixed
        set of columns. The `field` parameter is accepted (and validated in
        the request layer as an encoded FieldId) for forward compatibility
        but is currently ignored by the repository.

        Args:
            query: Search query
            field: Optional encoded FieldId to narrow the search (currently ignored)
            limit: Maximum results

        Returns:
            AssetSearchResponse with matching results
        """
        results_data = self.repo.search_assets(query, limit)

        results = []
        for result_data in results_data:
            # Extract first hostname from array
            hostname_arr = self._parse_array_field(result_data.get('hostname'))
            hostname = hostname_arr[0] if hostname_arr else None
            ip_list = self._parse_array_field(result_data.get('ip'))

            results.append(AssetSearchResult(
                id=result_data['id'],
                hostname=hostname,
                category=result_data.get('category'),
                ip=ip_list,
                match_field=result_data.get('match_field', 'unknown')
            ))

        return AssetSearchResponse(
            success=True,
            results=results,
            query=query
        )

    def _build_asset_detail(self, asset_data: Dict[str, Any], tags: List[Dict[str, Any]]) -> AssetDetail:
        """
        Build AssetDetail model from database row.

        Args:
            asset_data: Raw asset data from database
            tags: List of tag dicts with name, color, and type

        Returns:
            AssetDetail model
        """
        # Parse array fields (PostgreSQL arrays) - extract first value for single-value fields
        hostname_arr = self._parse_array_field(asset_data.get('hostname'))
        hostname = hostname_arr[0] if hostname_arr else None

        fqdn_arr = self._parse_array_field(asset_data.get('fqdn'))
        fqdn = fqdn_arr[0] if fqdn_arr else None

        model_arr = self._parse_array_field(asset_data.get('model'))
        model = model_arr[0] if model_arr else None

        manufacturer_arr = self._parse_array_field(asset_data.get('manufacturer'))
        manufacturer = manufacturer_arr[0] if manufacturer_arr else None

        mac_vendor_arr = self._parse_array_field(asset_data.get('mac_vendor_enriched'))
        mac_vendor = mac_vendor_arr[0] if mac_vendor_arr else None

        sn_arr = self._parse_array_field(asset_data.get('sn'))
        sn = sn_arr[0] if sn_arr else None

        # Arrays that can have multiple values
        ip_list = self._parse_array_field(asset_data.get('ip'))
        mac_list = self._parse_array_field(asset_data.get('mac'))

        # Per-source observable arrays — expose both array form and a primary scalar
        owner_arr = self._parse_array_field(asset_data.get('owner'))
        owner = owner_arr[0] if owner_arr else None
        location_arr = self._parse_array_field(asset_data.get('location'))
        location = location_arr[0] if location_arr else None
        ipam_location_arr = self._parse_array_field(asset_data.get('ipam_location'))
        ipam_location = ipam_location_arr[0] if ipam_location_arr else None
        user_arr = self._parse_array_field(asset_data.get('user_field'))
        user = user_arr[0] if user_arr else None
        vendor_category_arr = self._parse_array_field(asset_data.get('vendor_category'))
        vendor_category = vendor_category_arr[0] if vendor_category_arr else None

        # Calculate confidence level
        confidence = float(asset_data.get('confidence', 0.0))
        confidence_level = self._calculate_confidence_level(confidence)

        # Generate confidence explanation
        confidence_explanation = self._generate_confidence_explanation(
            asset_data,
            confidence,
            confidence_level
        )

        # Calculate status
        status = self._calculate_asset_status(asset_data)

        # Handle extended_attributes (can be None)
        extended_attrs = asset_data.get('extended_attributes') or {}

        # Use last_seen/first_seen if available, otherwise fall back to last_time/first_time
        last_seen = asset_data.get('last_seen') or asset_data.get('last_time')
        first_seen = asset_data.get('first_seen') or asset_data.get('first_time')

        # Convert tag dicts to AssetTag objects
        tag_objects = [
            AssetTag(
                name=tag['tag_name'],
                color=tag['color'],
                type=tag['tag_type']
            )
            for tag in tags
        ] if tags else []

        return AssetDetail(
            id=asset_data['id'],
            hostname=hostname,
            hostnames=hostname_arr,
            fqdn=fqdn,
            category=asset_data.get('category'),
            subcategory=asset_data.get('subcategory'),
            vendor_category=vendor_category,
            os=asset_data.get('os_normalized'),
            model=model,
            manufacturer=manufacturer,
            ip=ip_list,
            mac=mac_list,
            mac_vendor=mac_vendor,
            sn=sn,
            confidence=confidence,
            confidence_level=confidence_level,
            confidence_explanation=confidence_explanation,
            confidence_breakdown=asset_data.get('confidence_breakdown'),
            last_seen=last_seen,
            first_seen=first_seen,
            tags=tag_objects,
            status=status,
            owner=owner,
            owners=owner_arr,
            location=location,
            locations=location_arr,
            ipam_location=ipam_location,
            ipam_locations=ipam_location_arr,
            user=user,
            users=user_arr,
            extended_attributes=extended_attrs
        )

    def _build_providers(self, providers_data: List[Dict[str, Any]]) -> List[AssetProvider]:
        """
        Build AssetProvider models from database rows, grouped by data_source.

        Multiple raw records from the same integration are merged into a single
        provider entry with combined fields.

        Args:
            providers_data: Raw provider data from database

        Returns:
            List of AssetProvider models (one per data source)
        """
        if not providers_data:
            return []

        # Group rows by data_source
        from collections import OrderedDict
        grouped: OrderedDict[str, List[Dict[str, Any]]] = OrderedDict()
        for row in providers_data:
            key = row.get('data_source', 'unknown')
            grouped.setdefault(key, []).append(row)

        providers = []
        for source_id, rows in grouped.items():
            merged = self._merge_provider_rows(rows)

            source_name = rows[0].get('source_name', source_id)
            source_type = rows[0].get('source_type') or 'unknown'
            source_status = rows[0].get('source_status') or 'unknown'
            source_logo = rows[0].get('source_logo')

            if source_logo:
                source_icon = f'/logos/{source_logo}'
            else:
                source_icon = SOURCE_ICONS.get(source_type.lower() if source_type else 'unknown', SOURCE_ICONS['default'])

            fields = self._organize_provider_fields(merged)

            field_count = (
                len(fields.general) +
                len(fields.network) +
                len(fields.additional) +
                len(fields.extended)
            )

            status_display = "inactive" if source_status == "disabled" else "active"

            source_category = rows[0].get('source_category') or rows[0].get('data_source_category')

            providers.append(AssetProvider(
                source_id=source_id,
                source_name=source_name,
                source_icon=source_icon,
                source_type=source_type,
                source_category=source_category,
                status=status_display,
                last_sync=rows[0].get('last_sync_at'),
                field_count=field_count,
                record_count=len(rows),
                fields=fields
            ))

        return providers

    def _merge_provider_rows(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Merge multiple provider rows from the same data source into one dict.

        Standard fields get unique non-empty values joined with ', '.
        Extended fields (data_source_fields) are deep-merged with unique values per key.

        Args:
            rows: List of provider dicts from the same data_source

        Returns:
            Single merged dict suitable for _organize_provider_fields
        """
        if len(rows) == 1:
            return rows[0]

        merged = dict(rows[0])  # start with first row as base

        # Fields to collect unique values across rows
        merge_fields = ['hostname', 'fqdn', 'ip', 'mac', 'sn', 'os',
                        'manufacturer', 'model', 'vendor_category']

        for field in merge_fields:
            values = []
            for row in rows:
                val = row.get(field)
                if val and str(val).strip():
                    val_str = str(val).strip()
                    if val_str not in values:
                        values.append(val_str)
            merged[field] = ', '.join(values) if values else None

        # Deep-merge data_source_fields
        merged_dsf: Dict[str, Any] = {}
        for row in rows:
            dsf = row.get('data_source_fields')
            if not dsf or not isinstance(dsf, dict):
                continue
            for key, value in dsf.items():
                if key not in merged_dsf:
                    merged_dsf[key] = value
                else:
                    # Collect unique values
                    existing = merged_dsf[key]
                    if existing == value:
                        continue
                    # Convert to list of unique values
                    if not isinstance(existing, list):
                        existing = [existing]
                    if value not in existing:
                        existing.append(value)
                    merged_dsf[key] = existing

        merged['data_source_fields'] = merged_dsf if merged_dsf else None

        return merged

    def _organize_provider_fields(self, provider_data: Dict[str, Any]) -> ProviderFieldSection:
        """
        Organize provider fields into sections (general, network, additional, extended).

        Args:
            provider_data: Raw provider data

        Returns:
            ProviderFieldSection with organized fields
        """
        general = {}
        network = {}
        additional = {}
        extended = {}

        # General fields
        if provider_data.get('hostname'):
            general['Hostname'] = provider_data['hostname']
        if provider_data.get('os'):
            general['OS'] = provider_data['os']
        if provider_data.get('model'):
            general['Model'] = provider_data['model']
        if provider_data.get('sn'):
            general['Serial Number'] = provider_data['sn']
        if provider_data.get('manufacturer'):
            general['Manufacturer'] = provider_data['manufacturer']

        # Network fields
        if provider_data.get('ip'):
            network['IP Address'] = str(provider_data['ip'])
        if provider_data.get('mac'):
            network['MAC Address'] = str(provider_data['mac'])
        if provider_data.get('fqdn'):
            network['FQDN'] = provider_data['fqdn']

        # Extended fields from data_source_fields JSONB
        data_source_fields = provider_data.get('data_source_fields', {})
        if data_source_fields and isinstance(data_source_fields, dict):
            for key, value in data_source_fields.items():
                # Convert key to title case
                display_key = key.replace('_', ' ').title()

                # Handle nested dictionaries by flattening them
                if isinstance(value, dict):
                    # Add each nested field as a separate entry
                    for nested_key, nested_value in value.items():
                        nested_display_key = nested_key.replace('_', ' ').title()
                        extended[nested_display_key] = str(nested_value) if nested_value is not None else '-'
                elif isinstance(value, list):
                    # Convert lists to comma-separated strings
                    extended[display_key] = ', '.join(str(item) for item in value)
                else:
                    # Convert value to string representation
                    extended[display_key] = str(value) if value is not None else '-'

        return ProviderFieldSection(
            general=general,
            network=network,
            additional=additional,
            extended=extended
        )

    def _calculate_confidence_level(self, confidence: float, thresholds: tuple = None) -> ConfidenceLevel:
        """
        Calculate confidence level from confidence score using configurable thresholds.
        Falls back to defaults (0.45/0.70) outside Flask app context.
        """
        medium, high = thresholds or self._get_confidence_thresholds()
        if confidence >= high:
            return ConfidenceLevel.HIGH
        elif confidence >= medium:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    def _get_confidence_thresholds(self):
        """Return (medium, high) from settings or defaults. Delegates to the
        shared accessor so every surface reads the same source of truth."""
        try:
            return get_confidence_thresholds(current_app.settings_manager)
        except RuntimeError:
            return DEFAULT_CONFIDENCE_THRESHOLDS

    # Human-readable labels for identifier types
    _ID_LABELS = {
        'serial_number': 'a serial number',
        'fqdn_and_mac': 'FQDN paired with a physical MAC',
        'fqdn': 'an FQDN',
        'hostname_and_mac': 'hostname paired with a physical MAC',
        'hostname': 'a hostname only',
        'mac': 'a MAC address only',
        'ip_only': 'only an IP address',
        'minimal': 'minimal identifiers',
    }

    # Varied openers per level — picked by hash of asset_id for determinism
    _OPENERS = {
        'high': [
            "Strong confidence here — {sources} corroborate this asset, identified by {id}.",
            "Backed by {sources} with {id}, this asset scores high on confidence.",
            "This asset is well-established across {sources}, anchored by {id}.",
            "High confidence: {sources} consistently report this asset, keyed on {id}.",
        ],
        'medium': [
            "Moderate confidence — {sources} report this asset, identified by {id}.",
            "This asset has decent backing from {sources} with {id}, but room to improve.",
            "Identified by {id} across {sources}, confidence sits in the medium range.",
            "Medium confidence: {sources} recognize this asset via {id}.",
        ],
        'low': [
            "Low confidence — only {sources} feeding data, identified by {id}.",
            "This asset is weakly established with just {sources} and {id}.",
            "Confidence is low: {sources} and {id} leave significant gaps.",
            "Limited visibility — {sources} with {id} isn't enough for strong confidence.",
        ],
    }

    def _generate_confidence_explanation(
        self,
        asset_data: Dict[str, Any],
        confidence: float,
        level: ConfidenceLevel
    ) -> str:
        """
        Generate a human-readable explanation of the confidence score.

        Uses varied sentence templates selected deterministically by asset_id
        so the same asset always gets the same text, but different assets get
        different phrasing.

        Args:
            asset_data: Raw asset data (may contain 'confidence_breakdown')
            confidence: Confidence score
            level: Confidence level

        Returns:
            Explanation string
        """
        breakdown = asset_data.get('confidence_breakdown')
        if breakdown is None:
            return self._generate_legacy_explanation(asset_data, confidence, level)

        source_count = breakdown.get('source_count', 0)
        identifier_type = breakdown.get('identifier_type', 'minimal')
        consistency_penalty = breakdown.get('consistency_penalty', 1.0)
        conflicting_fields = breakdown.get('conflicting_fields', [])
        missing_fields = breakdown.get('missing_fields', [])
        days_since_seen = breakdown.get('days_since_seen', 0)

        id_label = self._ID_LABELS.get(identifier_type, identifier_type)
        src_phrase = f"{source_count} source{'s' if source_count != 1 else ''}"

        # Pick opener deterministically from asset_id
        level_label = level.value
        openers = self._OPENERS.get(level_label, self._OPENERS['medium'])
        asset_id = str(asset_data.get('id', ''))
        idx = sum(ord(c) for c in asset_id) % len(openers)
        opener = openers[idx].format(sources=src_phrase, id=id_label)

        details = []

        # Conflicts
        if consistency_penalty < 1.0 and conflicting_fields:
            fields_str = ', '.join(conflicting_fields[:3])
            if consistency_penalty < 0.8:
                details.append(f"Significant conflicts on {fields_str}.")
            else:
                details.append(f"Minor disagreements on {fields_str}.")
        # Only mention "no conflicts" for high confidence (otherwise it's noise)
        elif level_label == 'high':
            details.append("All sources agree.")

        # Missing fields
        if missing_fields:
            if len(missing_fields) <= 2:
                details.append(f"Missing: {', '.join(missing_fields)}.")
            else:
                details.append(f"{len(missing_fields)} key fields still missing.")

        # Staleness — use actual last_seen from asset, not stale breakdown
        last_seen = asset_data.get('last_seen')
        if last_seen:
            from datetime import datetime, timezone as tz
            try:
                if isinstance(last_seen, str):
                    from dateutil.parser import parse as parse_dt
                    last_seen = parse_dt(last_seen)
                if hasattr(last_seen, 'tzinfo') and last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=tz.utc)
                age_days = (datetime.now(tz.utc) - last_seen).days
                if age_days > 90:
                    details.append(f"Last seen {age_days} days ago — data is stale.")
                elif age_days > 30:
                    details.append(f"Not seen in {age_days} days.")
                elif age_days > 7:
                    details.append(f"Last reported {age_days} days ago.")
            except (ValueError, TypeError):
                pass

        parts = [opener] + details
        return ' '.join(parts)

    def _generate_legacy_explanation(
        self,
        asset_data: Dict[str, Any],
        confidence: float,
        level: ConfidenceLevel
    ) -> str:
        """
        Legacy template-based confidence explanation.

        Used as a fallback when no confidence_breakdown is stored for the asset.

        Args:
            asset_data: Raw asset data
            confidence: Confidence score
            level: Confidence level

        Returns:
            Explanation string
        """
        # Count data sources
        data_sources = self._parse_array_field(asset_data.get('data_source', []))
        source_count = len(data_sources)

        # Get time since last seen
        last_time = asset_data.get('last_time')
        time_desc = self._format_time_ago(last_time) if last_time else "unknown time"

        # Build explanation based on confidence level
        if level == ConfidenceLevel.HIGH:
            explanation = (
                f"This asset has a high confidence score due to consistent data from "
                f"{source_count} active source{'s' if source_count != 1 else ''} reporting within the last {time_desc}. "
                f"All critical attributes (hostname, IP, MAC address) are corroborated across multiple sources "
                f"with no conflicts detected. The asset is regularly monitored and has complete metadata coverage."
            )
        elif level == ConfidenceLevel.MEDIUM:
            explanation = (
                f"This asset has a medium confidence score based on data from "
                f"{source_count} source{'s' if source_count != 1 else ''}, last seen {time_desc} ago. "
                f"Most key attributes are present and consistent, though some minor discrepancies or "
                f"missing fields may exist. Additional data sources or more recent updates would improve confidence."
            )
        else:
            explanation = (
                f"This asset has a low confidence score. Data is available from "
                f"{source_count} source{'s' if source_count != 1 else ''}, last seen {time_desc} ago. "
                f"Key attributes may be missing, conflicting, or outdated. Consider verifying this asset's "
                f"information through additional sources or manual inspection."
            )

        return explanation

    def _calculate_asset_status(self, asset_data: Dict[str, Any]) -> AssetStatus:
        """
        Calculate asset status (compliance, security, alerts).

        Args:
            asset_data: Raw asset data

        Returns:
            AssetStatus model
        """
        # is_compliant / is_secure remain unpopulated placeholders — the actual
        # compliance and security checks are tracked separately (#866).
        is_compliant = None
        is_secure = None

        # alert_count is real data: the number of active (active/acknowledged)
        # alerts whose asset_ids array contains this asset. Falls back to 0 when
        # the alerts repository isn't wired or the row has no id, so the detail
        # view never crashes on status (#891).
        alert_count = 0
        asset_id = asset_data.get('id')
        if self.alerts_repo is not None and asset_id:
            alert_count = self.alerts_repo.count_active_alerts_for_asset(asset_id)

        # Grace-period visibility (#795): missing_since is NULL for present assets
        # and set to the run an asset first went absent. Floor-of-total-days matches
        # the pipeline (s15c) and the In Grace / Days In Grace Explorer fields.
        missing_since = asset_data.get('missing_since')
        in_grace = missing_since is not None
        days_in_grace: Optional[int] = None
        if in_grace:
            if missing_since.tzinfo is None:
                missing_since = missing_since.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - missing_since
            days_in_grace = int(delta.total_seconds() // 86400)

        return AssetStatus(
            is_compliant=is_compliant,
            is_secure=is_secure,
            alert_count=alert_count,
            in_grace=in_grace,
            days_in_grace=days_in_grace,
        )

    def _parse_array_field(self, field: Any) -> List[str]:
        """
        Parse PostgreSQL array field to Python list.

        Args:
            field: Database field value (could be list, string, or None)

        Returns:
            List of strings
        """
        if field is None:
            return []
        if isinstance(field, list):
            # Already a list, filter out None values and convert to strings
            return [str(item) for item in field if item is not None]
        if isinstance(field, str):
            # String representation of array, try to parse
            # PostgreSQL arrays come as {val1,val2,val3}
            if field.startswith('{') and field.endswith('}'):
                items = field[1:-1].split(',')
                return [item.strip() for item in items if item.strip()]
            return [field] if field else []
        return []

    def _format_time_ago(self, timestamp: datetime) -> str:
        """
        Format timestamp as relative time description.

        Args:
            timestamp: Timestamp to format

        Returns:
            Human-readable time description (e.g., "2 hours")
        """
        if not timestamp:
            return "unknown time"

        now = datetime.now(timestamp.tzinfo) if timestamp.tzinfo else datetime.now()
        delta = now - timestamp

        if delta.days > 365:
            years = delta.days // 365
            return f"{years} year{'s' if years != 1 else ''}"
        elif delta.days > 30:
            months = delta.days // 30
            return f"{months} month{'s' if months != 1 else ''}"
        elif delta.days > 0:
            return f"{delta.days} day{'s' if delta.days != 1 else ''}"
        elif delta.seconds > 3600:
            hours = delta.seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''}"
        elif delta.seconds > 60:
            minutes = delta.seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        else:
            return "a few seconds"

    def query_assets(self, **kwargs) -> tuple:
        """
        Query assets for the external API. Delegates to repository.
        Enriches results with tags from asset_tags table.

        Accepts: ip, hostname, fqdn, mac, sn, q, fields, limit.
        Returns: (rows, total_matches)
        """
        rows, total = self.repo.query_assets(**kwargs)

        # Enrich with tags if requested (tags are in separate table)
        fields = kwargs.get('fields')
        include_tags = fields is None or 'tags' in (fields or [])
        if include_tags and rows:
            asset_ids = [str(r['id']) for r in rows]
            tags_by_id = self.repo.get_tags_for_assets(asset_ids)
            for row in rows:
                row['tags'] = tags_by_id.get(str(row['id']), [])

        return rows, total
