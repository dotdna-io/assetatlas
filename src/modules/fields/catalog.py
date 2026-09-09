# SPDX-License-Identifier: AGPL-3.0-or-later
"""Field catalog — canonical source of truth for all filterable field
identifiers, their data types, and their SQL mapping.

Static content migrated from src/modules/tag_rules/config/field_mappings.py.
Dynamic content (extended_attributes, tags, posture categories) is loaded
by the FieldCatalog class defined later in this module (see Task 6).
"""

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote

from src.modules.fields.types import FieldDataType, FilterOperator
from src.modules.fields.encoding import encode
from src.modules.fields.operators import operators_for as _operators_for
from src.modules.fields import catalog_version
from src.modules.fields.catalog_version import CatalogVersionSource
from src.modules.fields.models import (
    StaticField, CoreEnrichmentField, ExtendedAttributeField,
    IntegrationGroupField, TagField, PostureField, VirtualField,
)

TAG_FIELD_PREFIX = "Tags - "


LEGACY_FIELD_MAPPINGS: Dict[str, str] = {
    "OS Type": "OS Normalized",
    "Asset Type": "Category",
    "Asset Subtype": "Subcategory",
}


def normalize_legacy_name(field_name: str) -> str:
    """Map legacy UI names to canonical names. Pass-through for non-legacy."""
    return LEGACY_FIELD_MAPPINGS.get(field_name, field_name)


# Target filter + advanced + compliance criteria fields
STATIC_FIELDS: Dict[str, str] = {
    "Hostname": "hostname",
    "FQDN": "fqdn",
    "IP Address": "ip",
    "MAC Address": "mac",
    "Serial Number": "sn",
    "Category": "category",
    "Subcategory": "subcategory",
    "OS Normalized": "os_normalized",
    "MAC Vendor": "mac_vendor_enriched",
    "Manufacturer": "manufacturer",
    "Model": "model",
    "Data Source": "data_source",
    "Data Source Category": "data_source_category",
    "Owner": "owner",
    "Location": "location",
    "User Field": "user_field",
    "Confidence Score": "confidence",
    "OS EOL Status": "os_eol_status",
    "OS EOL Date": "os_eol_date",
    "OS Is LTS": "os_is_lts",
    "First Seen": "first_seen",
    "Last Seen": "last_seen",
    "IPAM Location": "ipam_location",
}


FIELD_DATA_TYPES: Dict[str, FieldDataType] = {
    "Hostname": FieldDataType.STRING_ARRAY,
    "FQDN": FieldDataType.STRING_ARRAY,
    "IP Address": FieldDataType.STRING_ARRAY,
    "MAC Address": FieldDataType.STRING_ARRAY,
    "Serial Number": FieldDataType.STRING_ARRAY,
    "Category": FieldDataType.STRING,
    "Subcategory": FieldDataType.STRING,
    "OS Normalized": FieldDataType.STRING,
    "MAC Vendor": FieldDataType.STRING_ARRAY,
    "Manufacturer": FieldDataType.STRING_ARRAY,
    "Model": FieldDataType.STRING_ARRAY,
    "Data Source": FieldDataType.STRING_ARRAY,
    "Data Source Category": FieldDataType.STRING_ARRAY,
    "Owner": FieldDataType.STRING_ARRAY,
    "Location": FieldDataType.STRING_ARRAY,
    "User Field": FieldDataType.STRING_ARRAY,
    "Confidence Score": FieldDataType.FLOAT,
    "OS EOL Status": FieldDataType.STRING,
    "OS EOL Date": FieldDataType.DATE,
    "OS Is LTS": FieldDataType.BOOLEAN,
    "First Seen": FieldDataType.TIMESTAMP,
    "Last Seen": FieldDataType.TIMESTAMP,
    "IPAM Location": FieldDataType.STRING_ARRAY,
}


CORE_ENRICHMENT_SOURCES: frozenset = frozenset({"AssetAtlas"})

CORE_ENRICHMENT_FIELDS: Dict[str, Dict] = {
    "Private IP":    {"path": "AssetAtlas.ip_private",    "data_type": FieldDataType.JSONB_ARRAY},
    "Public IP":     {"path": "AssetAtlas.ip_public",     "data_type": FieldDataType.JSONB_ARRAY},
    "IP Type":       {"path": "AssetAtlas.ip_type",       "data_type": FieldDataType.STRING},
    "Geo Country":   {"path": "AssetAtlas.geo_countries", "data_type": FieldDataType.JSONB_ARRAY},
    "Geo City":      {"path": "AssetAtlas.geo_cities",    "data_type": FieldDataType.JSONB_ARRAY},
    "Geo Region":    {"path": "AssetAtlas.geo_regions",   "data_type": FieldDataType.JSONB_ARRAY},
}


TARGET_FILTER_FIELDS: List[str] = [
    "Hostname", "FQDN", "IP Address", "MAC Address", "Serial Number",
    "Category", "Subcategory", "OS Normalized", "MAC Vendor", "Manufacturer",
    "Model", "Data Source", "Data Source Category", "Owner", "Location", "User Field",
]

ADVANCED_FIELDS: List[str] = [
    "OS EOL Status", "OS EOL Date", "OS Is LTS",
    "First Seen", "Last Seen", "IPAM Location", "Confidence Score",
]

COMPLIANCE_CRITERIA_FIELDS: List[str] = TARGET_FILTER_FIELDS + ADVANCED_FIELDS

ENRICHMENT_FIELDS: List[str] = [
    "Private IP", "Public IP", "IP Type",
    "Geo Country", "Geo City", "Geo Region",
]


# Virtual fields — backend-computed expressions. SQL is trusted (no user input interpolated).
VIRTUAL_FIELDS: Dict[str, Dict] = {
    "confidence_level": {
        "display_name": "Confidence Level",
        "data_type": FieldDataType.STRING,
        # Static fallback used for ORDER BY and any consumer that doesn't pass
        # confidence_thresholds. Literals match DEFAULT_CONFIDENCE_THRESHOLDS
        # (0.45, 0.70). The live SELECT projection interpolates the configured
        # thresholds in asset_repository._build_select_columns instead.
        "sql": (
            "CASE "
            "WHEN confidence >= 0.70 THEN 'high' "
            "WHEN confidence >= 0.45 THEN 'medium' "
            "ELSE 'low' "
            "END"
        ),
        # Bespoke clause builder (_build_confidence_level_clause) only translates
        # eq/any into numeric ranges; any other operator is silently dropped. Offer
        # only what works so the dropdown can't produce a no-op filter (G-1).
        "operators": ["eq", "any"],
    },
    # How many ingesting sources the asset currently has in its
    # data_source[] array. Lets users ask "how many assets come from
    # multiple sources?" via aggregate_assets(row_field=data_source_count)
    # or "assets seen by 3+ sources" via filters. COALESCE handles the
    # rare row with NULL/empty data_source (counted as 0).
    "data_source_count": {
        "display_name": "Data Source Count",
        "data_type": FieldDataType.INTEGER,
        "sql": "COALESCE(array_length(data_source, 1), 0)",
    },
    "hostname_count": {
        "display_name": "Hostname Count",
        "data_type": FieldDataType.INTEGER,
        "sql": "COALESCE(array_length(hostname, 1), 0)",
    },
    "ip_count": {
        "display_name": "IP Count",
        "data_type": FieldDataType.INTEGER,
        "sql": "COALESCE(array_length(ip, 1), 0)",
    },
    "fqdn_count": {
        "display_name": "FQDN Count",
        "data_type": FieldDataType.INTEGER,
        "sql": "COALESCE(array_length(fqdn, 1), 0)",
    },
    "user_count": {
        "display_name": "User Count",
        "data_type": FieldDataType.INTEGER,
        "sql": "COALESCE(array_length(user_field, 1), 0)",
    },
    "owner_count": {
        "display_name": "Owner Count",
        "data_type": FieldDataType.INTEGER,
        "sql": "COALESCE(array_length(owner, 1), 0)",
    },
    "asset_label": {
        "display_name": "Asset Label",
        "data_type": FieldDataType.STRING,
        "sql": (
            "COALESCE("
            "NULLIF(asset_label_override, ''), "
            "NULLIF(hostname[1], ''), "
            "NULLIF(fqdn[1], ''), "
            "NULLIF(mac[1], ''), "
            "NULLIF(sn[1], ''), "
            "NULLIF(ip[1], '')"
            ")"
        ),
    },
    # Removal grace-period visibility (#795). `missing_since` (migration 115) is
    # NULL for present assets and set to the run an asset first went absent from
    # ALL inputs; stage s15c carries such an asset forward silently until it has
    # been absent >= the grace window, then hard-removes it.
    "in_grace": {
        "display_name": "In Grace",
        "data_type": FieldDataType.BOOLEAN,
        "sql": "(missing_since IS NOT NULL)",
        # eq only: is-empty/is-not-empty are useless on an expression that is never
        # NULL (notempty always-true, empty always-false), so gate them out (G-1) —
        # same reason confidence_level narrows.
        "operators": ["eq"],
    },
    "days_in_grace": {
        "display_name": "Days In Grace",
        "data_type": FieldDataType.INTEGER,
        # Whole days absent so far; NULL for present assets (NULL propagates), which
        # excludes them from `>= N`. Floor-of-epoch matches the pipeline's own
        # days_absent = (now - missing_since).days (s15c) exactly — deliberately NOT
        # calendar-date subtraction, which would drift up to a day from the cutoff.
        "sql": "FLOOR(EXTRACT(EPOCH FROM (now() - missing_since)) / 86400)::int",
    },
    # Alert membership (#907 follow-up). `alerts.asset_ids uuid[]` names the
    # assets an alert fired on. FILTER-ONLY: this expression is a correlated
    # subquery, so any path that evaluates it across the whole assets table —
    # pivot, ORDER BY, /values — cost >180s against the dev DB's 73,740 assets,
    # versus 33ms for the bespoke clause (_build_alert_field_clause) that asks
    # the same question from the 50-row alerts side. `filter_only` keeps it out
    # of every picker; the sql below is reached only by a hand-crafted
    # ?fields=virtual:alert projection, which LIMIT bounds to ~75ms/page.
    #
    # Scoped to OPEN alerts because resolved rows accumulate under the same
    # title (17 'Assets Disappeared' rows on the dev DB, none open) — see
    # _build_alert_field_clause, which must agree with this.
    "alert": {
        "display_name": "Alert",
        "data_type": FieldDataType.STRING,
        "sql": (
            "(SELECT string_agg(al.title, ', ' ORDER BY al.title) "
            "FROM alerts al "
            "WHERE al.status IN ('active','acknowledged') "
            "AND assets.id = ANY(al.asset_ids))"
        ),
        # Bespoke clause builder handles eq/any only — offer nothing that would
        # be silently dropped (G-1, as confidence_level).
        "operators": ["eq", "any"],
        "filter_only": True,
    },
}


def is_filter_only_field(field_id) -> bool:
    """True when a decoded FieldId names a filter-only catalog field.

    Single source of truth for the guards that keep such a field out of the
    whole-table paths. `.get(name, {})` also covers unified fields, which mint
    VirtualField ids whose slugs are not in VIRTUAL_FIELDS.
    """
    from src.modules.fields.models import VirtualField
    return (
        isinstance(field_id, VirtualField)
        and bool(VIRTUAL_FIELDS.get(field_id.name, {}).get("filter_only"))
    )


OPERATORS_BY_DATA_TYPE: Dict[FieldDataType, List[FilterOperator]] = {
    FieldDataType.STRING: [
        FilterOperator.EQUALS, FilterOperator.NOT_EQUALS,
        FilterOperator.CONTAINS, FilterOperator.NOT_CONTAINS,
        FilterOperator.STARTS_WITH, FilterOperator.ENDS_WITH,
        FilterOperator.IN, FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL,
    ],
    FieldDataType.STRING_ARRAY: [
        FilterOperator.EQUALS, FilterOperator.NOT_EQUALS,
        FilterOperator.CONTAINS, FilterOperator.NOT_CONTAINS,
        FilterOperator.STARTS_WITH, FilterOperator.ENDS_WITH,
        FilterOperator.IN, FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL,
    ],
    FieldDataType.INTEGER: [
        FilterOperator.EQUALS, FilterOperator.NOT_EQUALS,
        FilterOperator.GREATER_THAN, FilterOperator.LESS_THAN,
        FilterOperator.GREATER_THAN_OR_EQUAL, FilterOperator.LESS_THAN_OR_EQUAL,
        FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL,
    ],
    FieldDataType.FLOAT: [
        FilterOperator.EQUALS, FilterOperator.NOT_EQUALS,
        FilterOperator.GREATER_THAN, FilterOperator.LESS_THAN,
        FilterOperator.GREATER_THAN_OR_EQUAL, FilterOperator.LESS_THAN_OR_EQUAL,
        FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL,
    ],
    FieldDataType.BOOLEAN: [
        FilterOperator.EQUALS, FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL,
    ],
    FieldDataType.DATE: [
        FilterOperator.EQUALS, FilterOperator.NOT_EQUALS,
        FilterOperator.GREATER_THAN, FilterOperator.LESS_THAN,
        FilterOperator.GREATER_THAN_OR_EQUAL, FilterOperator.LESS_THAN_OR_EQUAL,
        FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL,
    ],
    FieldDataType.TIMESTAMP: [
        FilterOperator.EQUALS, FilterOperator.NOT_EQUALS,
        FilterOperator.GREATER_THAN, FilterOperator.LESS_THAN,
        FilterOperator.GREATER_THAN_OR_EQUAL, FilterOperator.LESS_THAN_OR_EQUAL,
        FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL,
    ],
    FieldDataType.JSONB: [
        FilterOperator.CONTAINS, FilterOperator.NOT_CONTAINS,
        FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL,
    ],
    FieldDataType.JSONB_ARRAY: [
        FilterOperator.EQUALS, FilterOperator.NOT_EQUALS,
        FilterOperator.CONTAINS, FilterOperator.NOT_CONTAINS,
        FilterOperator.STARTS_WITH, FilterOperator.ENDS_WITH,
        FilterOperator.IN, FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL,
    ],
}


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogEntry:
    """Internal row in the catalog. The resolver converts these to ResolvedField."""
    kind_name: str                   # "static", "core_enrichment", "extended_attribute",
                                     # "integration_group", "tag", "posture", "virtual"
    display_name: str
    data_type: FieldDataType
    operators: Tuple[FilterOperator, ...]
    # Optional per-field allow-list of operator CODES. When set, operator_specs is
    # filtered to these (and their order) instead of the full per-data-type set —
    # used by fields whose clause builder supports only a subset (G-1).
    allowed_operator_codes: Optional[Tuple[str, ...]] = None
    # per-kind fields (unused fields left as defaults):
    static_db_column: Optional[str] = None                # static
    core_path: Optional[str] = None                       # core_enrichment ("AssetAtlas.ip_private")
    source: Optional[str] = None                          # extended_attribute
    attribute: Optional[str] = None                       # extended_attribute, integration_group
    group: Optional[str] = None                           # integration_group
    member_sources: Optional[Tuple[str, ...]] = None      # integration_group
    tag_name: Optional[str] = None                        # tag
    posture_category: Optional[str] = None                # posture
    posture_read: Optional[str] = None                    # posture ("state"|"output")
    virtual_name: Optional[str] = None                    # virtual
    unified_function: Optional[str] = None             # unified field: merge function
    unified_sources: Optional[Tuple[str, ...]] = None  # unified field: ordered encoded source keys
    # unified field: True once refresh_unified_values stamped materialized_at —
    # the resolver then reads the stored value instead of the live merge SQL.
    materialized: bool = False
    is_array: bool = False
    is_grouped: bool = False
    # Filterable, but never projectable / aggregatable / sortable. Set for
    # fields whose SQL is a correlated subquery: cheap inside a WHERE driven
    # from the small side, unusable when evaluated once per `assets` row.
    filter_only: bool = False


def _normalize_display_key(name: str) -> str:
    """Lowercase and collapse any run of non-alphanumerics to one space.
    'Beszel - cpu' / 'beszel  cpu' / 'BESZEL_CPU' all map to 'beszel cpu'."""
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


@dataclass(frozen=True)
class _Snapshot:
    """One consistent view of the catalog, replaced atomically on rebuild.

    entries/index/display_indexes are built from a single _build() pass so a
    reader can never observe a mix of two builds — an index() that disagrees
    with the entries() that preceded it, or display indexes stamped against a
    different generation. Accessors hold ONE local reference to the snapshot,
    so an invalidate() landing between entries() and index() cannot yield a
    None index; the reader simply finishes on the old snapshot.
    """
    entries: Tuple[CatalogEntry, ...]
    index: Dict[tuple, CatalogEntry]
    display_indexes: Tuple[Dict[str, Any], Dict[str, Any]]
    loaded_at: float
    version: Optional[int]
    generation: int


class FieldCatalog:
    """Builds the canonical entry list from static + dynamic sources, served
    from an atomically-replaced in-process snapshot.

    Freshness has two layers. The shared version stamp (``version_source``,
    a Redis INCR counter every content-changing event bumps) is polled at
    most once per ``stamp_check_interval``; a mismatch forces a rebuild, so an
    edit on any worker is visible everywhere within the interval. The TTL is
    the safety net behind it: ``ttl_seconds_with_stamp`` while the stamp is
    live (every content change bumps, so expiry is rare), and the historical
    ``ttl_seconds`` whenever the stamp is unavailable (no Redis, test config)
    — identical to the pre-stamp behaviour.
    """

    def __init__(
        self,
        db_adapter,
        group_mapping_provider: Callable[[], Dict[str, str]] = lambda: {},
        unified_fields_provider: Callable[[], List[Dict[str, Any]]] = lambda: [],
        ttl_seconds: float = 60.0,
        ttl_seconds_with_stamp: float = 900.0,
        stamp_check_interval: float = 2.0,
        version_source: Optional[CatalogVersionSource] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._db = db_adapter
        self._group_mapping_provider = group_mapping_provider
        self._unified_fields_provider = unified_fields_provider
        self._ttl = ttl_seconds
        self._ttl_with_stamp = ttl_seconds_with_stamp
        self._stamp_check_interval = stamp_check_interval
        self._version_source = version_source
        self._clock = clock
        self._snapshot: Optional[_Snapshot] = None
        self._generation = 0
        self._build_lock = threading.Lock()
        # Throttled stamp read: (time of last poll, value seen then). A fast
        # path within the interval serves the memoised value, so entries()
        # costs at most one Redis GET per interval per worker.
        self._stamp_checked_at = 0.0
        self._stamp_version: Optional[int] = None

    def entries(self) -> Tuple[CatalogEntry, ...]:
        return self._current_snapshot().entries

    def index(self) -> Dict[tuple, CatalogEntry]:
        """Return the (kind, *identifiers) -> entry lookup map. Triggers rebuild if needed."""
        return self._current_snapshot().index

    def invalidate(self) -> None:
        """Drop the cached snapshot so the next read rebuilds, and bump the
        shared stamp so every OTHER worker's catalog rebuilds within its
        stamp-check interval. Called by the Unified Fields service on
        create/update/delete so edits are immediate.

        Takes the build lock: an invalidate() that lands while another thread
        is mid-build must not be overwritten by that build's install — the
        lock serialises the two, and the cleared snapshot then forces the next
        read to rebuild instead of serving the edit away for up to the TTL."""
        with self._build_lock:
            self._snapshot = None
            self._generation += 1
        # The injected source when there is one (a test's fake shares it with
        # the "other workers"); the module default otherwise, so hand-built
        # catalogs still propagate. bump() never raises.
        source = self._version_source
        if source is not None:
            source.bump()
        else:
            catalog_version.bump()

    def _current_version(self, now: float, force: bool = False) -> Optional[int]:
        """The stamp value, throttled to one source read per check interval."""
        source = self._version_source
        if source is None:
            return None
        if force or (now - self._stamp_checked_at) >= self._stamp_check_interval:
            self._stamp_checked_at = now
            self._stamp_version = source.get()
        return self._stamp_version

    def _fresh_snapshot(self, now: float) -> Optional[_Snapshot]:
        snap = self._snapshot
        if snap is None:
            return None
        # A snapshot taken while the stamp was unavailable (None) gets the
        # historical short TTL; a stamped one the long safety-net TTL.
        ttl = self._ttl_with_stamp if snap.version is not None else self._ttl
        if (now - snap.loaded_at) >= ttl:
            return None
        if (self._version_source is not None
                and self._current_version(now) != snap.version):
            return None
        return snap

    def _current_snapshot(self) -> _Snapshot:
        snap = self._fresh_snapshot(self._clock())
        if snap is not None:
            return snap
        with self._build_lock:
            # Double-checked: with gthread workers several request threads can
            # arrive here on the same TTL expiry, and each unlocked _build()
            # is a ~255 ms full scan of extended_attributes. Re-test inside
            # the lock so only one thread rebuilds; the rest serve the fresh
            # snapshot.
            snap = self._fresh_snapshot(self._clock())
            if snap is not None:
                return snap
            return self._load_snapshot()

    def _load_snapshot(self) -> _Snapshot:
        """Caller holds _build_lock.

        The stamp is read (unthrottled) immediately BEFORE the build, so a
        bump that lands during the build mismatches on the next poll and
        forces one (possibly spare) rebuild — never a lost one. Reading it
        after the build would stamp the new version onto pre-edit content,
        and the update would stay invisible until the safety-net TTL.
        loaded_at is stamped AFTER the build, so the TTL window starts at
        install, not at build start.

        The forced pre-build get() runs under _build_lock: a hung Redis adds
        up to socket_timeout (1s, no retries) per rebuild. Bounded and
        acceptable — and the reason catalog_version.py must keep the short
        client timeouts on its Redis connection.
        """
        version = self._current_version(self._clock(), force=True)
        built = tuple(self._build())
        snap = _Snapshot(
            entries=built,
            index=self._build_index(built),
            display_indexes=self._build_display_indexes(built),
            loaded_at=self._clock(),
            version=version,
            generation=self._generation,
        )
        self._snapshot = snap
        return snap

    def field_id_for_display_name(self, name: str):
        """Resolve a display name to its FieldId, or None. Covers every catalog
        kind, incl. integration_group ('Beszel - cpu') and extended_attribute
        fields the static alias map doesn't know about. An EXACT (case-only)
        match wins first, so two fields whose display names differ solely in
        punctuation never shadow each other; a fuzzy key (punctuation collapsed)
        is the fallback for spelling variants like 'beszel cpu'."""
        if not name:
            return None
        exact, fuzzy = self._current_snapshot().display_indexes
        hit = exact.get(name.lower())
        if hit is not None:
            return hit
        return fuzzy.get(_normalize_display_key(name))

    @staticmethod
    def _build_display_indexes(entries):
        # An exact (lowercased display name) map plus a fuzzy
        # (punctuation-collapsed) map. The exact map is consulted first so real
        # display names resolve unambiguously and only true spelling variants
        # fall back to fuzzy (where first writer wins). Built with the
        # snapshot, not lazily against a separate stamp, so the lookup can
        # never disagree with the entry list it indexes.
        exact: Dict[str, Any] = {}
        fuzzy: Dict[str, Any] = {}
        for e in entries:
            fid = _entry_field_id(e)
            exact.setdefault(e.display_name.lower(), fid)
            fkey = _normalize_display_key(e.display_name)
            if fkey:
                fuzzy.setdefault(fkey, fid)
        return (exact, fuzzy)

    @staticmethod
    def _build_index(entries) -> Dict[tuple, CatalogEntry]:
        idx: Dict[tuple, CatalogEntry] = {}
        for e in entries:
            if e.kind_name == "static":
                idx[("static", e.display_name)] = e
            elif e.kind_name == "core_enrichment":
                idx[("core_enrichment", e.display_name)] = e
            elif e.kind_name == "extended_attribute":
                idx[("extended_attribute", e.source, e.attribute)] = e
            elif e.kind_name == "integration_group":
                idx[("integration_group", e.group, e.attribute)] = e
            elif e.kind_name == "tag":
                idx[("tag", e.tag_name)] = e
            elif e.kind_name == "posture":
                idx[("posture", e.posture_category, e.posture_read)] = e
            elif e.kind_name == "virtual":
                idx[("virtual", e.virtual_name)] = e
        return idx

    def _build(self) -> List[CatalogEntry]:
        out: List[CatalogEntry] = []
        out.extend(self._static_entries())
        out.extend(self._core_enrichment_entries())
        try:
            group_mapping = self._group_mapping_provider() or {}
        except Exception as exc:
            logger.warning("group_mapping_provider failed: %s — grouping disabled", exc)
            group_mapping = {}
        out.extend(self._extended_attribute_entries(group_mapping))
        out.extend(self._tag_entries())
        out.extend(self._posture_entries())
        out.extend(self._virtual_entries())
        out.extend(self._unified_field_entries())
        return out

    def _static_entries(self) -> List[CatalogEntry]:
        out = []
        for name, column in STATIC_FIELDS.items():
            dt = FIELD_DATA_TYPES[name]
            out.append(CatalogEntry(
                kind_name="static",
                display_name=name,
                data_type=dt,
                operators=tuple(OPERATORS_BY_DATA_TYPE[dt]),
                static_db_column=column,
                is_array=dt == FieldDataType.STRING_ARRAY,
            ))
        return out

    def _core_enrichment_entries(self) -> List[CatalogEntry]:
        out = []
        for name, cfg in CORE_ENRICHMENT_FIELDS.items():
            dt = cfg["data_type"]
            out.append(CatalogEntry(
                kind_name="core_enrichment",
                display_name=name,
                data_type=dt,
                operators=tuple(OPERATORS_BY_DATA_TYPE[dt]),
                core_path=cfg["path"],
                is_array=dt == FieldDataType.JSONB_ARRAY,
            ))
        return out

    def _extended_attribute_entries(self, group_mapping: Dict[str, str]) -> List[CatalogEntry]:
        try:
            rows = self._db.execute_query(
                """
                SELECT DISTINCT
                    source.key AS source_name,
                    jsonb_object_keys(source.value) AS field_name
                FROM assets,
                     LATERAL jsonb_each(extended_attributes) AS source(key, value)
                WHERE extended_attributes IS NOT NULL
                  AND jsonb_typeof(source.value) = 'object'
                ORDER BY source_name, field_name
                """,
                {},
            ) or []
        except Exception as exc:
            logger.warning("extended_attributes catalog load failed: %s", exc)
            return []

        # Group sources via group_mapping: a grouped attribute ALSO collapses to
        # one integration_group entry per (group, attribute) with sorted members.
        # The per-instance entry is emitted for EVERY source (grouped or not), so
        # a grouped integration's own fields stay individually selectable in
        # Explorer/rules alongside the merged group field.
        grouped: Dict[Tuple[str, str], List[str]] = {}  # (group, attribute) -> [instance_source,...]
        per_source: List[Tuple[str, str]] = []           # (source, attribute) for every instance
        for row in rows:
            src = row.get("source_name")
            attr = row.get("field_name")
            if not src or not attr or src in CORE_ENRICHMENT_SOURCES:
                continue
            group = group_mapping.get(src)
            if group:
                grouped.setdefault((group, attr), []).append(src)
            per_source.append((src, attr))

        out: List[CatalogEntry] = []
        for (group, attr), members in grouped.items():
            out.append(CatalogEntry(
                kind_name="integration_group",
                display_name=f"{group} - {attr}",
                data_type=FieldDataType.STRING,
                operators=tuple(OPERATORS_BY_DATA_TYPE[FieldDataType.STRING]),
                group=group,
                attribute=attr,
                member_sources=tuple(sorted(members)),
                is_grouped=True,
            ))
        for (src, attr) in per_source:
            out.append(CatalogEntry(
                kind_name="extended_attribute",
                display_name=f"{src} - {attr}",
                data_type=FieldDataType.STRING,
                operators=tuple(OPERATORS_BY_DATA_TYPE[FieldDataType.STRING]),
                source=src,
                attribute=attr,
            ))
        return out

    def _tag_entries(self) -> List[CatalogEntry]:
        try:
            rows = self._db.execute_query(
                """
                SELECT DISTINCT tag_name FROM (
                    SELECT tag_name FROM asset_tags_v2
                    UNION
                    SELECT tag_name FROM asset_manual_tags
                ) all_tags
                ORDER BY tag_name
                """,
                {},
            ) or []
        except Exception as exc:
            logger.warning("tag catalog load failed: %s", exc)
            return []
        out: List[CatalogEntry] = []
        for row in rows:
            name = row.get("tag_name")
            if not name:
                continue
            out.append(CatalogEntry(
                kind_name="tag",
                display_name=f"{TAG_FIELD_PREFIX}{name}",
                data_type=FieldDataType.BOOLEAN,
                operators=tuple(OPERATORS_BY_DATA_TYPE[FieldDataType.BOOLEAN]),
                tag_name=name,
            ))
        return out

    def _posture_entries(self) -> List[CatalogEntry]:
        try:
            rows = self._db.execute_query(
                "SELECT id FROM posture_categories ORDER BY id",
                {},
            ) or []
        except Exception as exc:
            logger.warning("posture catalog load failed: %s", exc)
            return []
        out: List[CatalogEntry] = []
        for row in rows:
            cat_id = row.get("id")
            if not cat_id:
                continue
            for read_kind in ("state", "output"):
                out.append(CatalogEntry(
                    kind_name="posture",
                    display_name=(cat_id.replace("_", " ").title()
                                  + (" (Output)" if read_kind == "output" else "")),
                    data_type=FieldDataType.STRING,
                    operators=tuple(OPERATORS_BY_DATA_TYPE[FieldDataType.STRING]),
                    posture_category=cat_id,
                    posture_read=read_kind,
                ))
        return out

    def _virtual_entries(self) -> List[CatalogEntry]:
        out: List[CatalogEntry] = []
        for name, cfg in VIRTUAL_FIELDS.items():
            dt = cfg["data_type"]
            out.append(CatalogEntry(
                kind_name="virtual",
                display_name=cfg["display_name"],
                data_type=dt,
                operators=tuple(OPERATORS_BY_DATA_TYPE[dt]),
                allowed_operator_codes=tuple(cfg["operators"]) if "operators" in cfg else None,
                virtual_name=name,
                filter_only=cfg.get("filter_only", False),
            ))
        return out

    def _unified_field_entries(self) -> List[CatalogEntry]:
        out: List[CatalogEntry] = []
        try:
            defs = self._unified_fields_provider() or []
        except Exception as exc:
            logger.warning("unified_fields_provider failed: %s — unified fields disabled", exc)
            return out
        for d in defs:
            if d["slug"] in VIRTUAL_FIELDS:
                logger.warning("unified field slug %r collides with a built-in virtual — skipping", d["slug"])
                continue
            dt = d["data_type"]
            out.append(CatalogEntry(
                kind_name="virtual",
                display_name=d["name"],
                data_type=dt,
                operators=tuple(OPERATORS_BY_DATA_TYPE[dt]),
                allowed_operator_codes=None,
                virtual_name=d["slug"],
                unified_function=d["function"],
                unified_sources=tuple(d["sources"]),
                materialized=d.get("materialized_at") is not None,
                is_array=dt in (FieldDataType.STRING_ARRAY, FieldDataType.JSONB_ARRAY),
            ))
        return out


def _entry_category(entry: CatalogEntry) -> str:
    if entry.kind_name == "static":
        if entry.display_name in TARGET_FILTER_FIELDS:
            return "target"
        if entry.display_name in ADVANCED_FIELDS:
            return "advanced"
        raise ValueError(
            f"static field {entry.display_name!r} is not in TARGET_FILTER_FIELDS or ADVANCED_FIELDS"
        )
    if entry.kind_name == "core_enrichment":
        return "enrichment"
    if entry.kind_name in ("extended_attribute", "integration_group"):
        return "extended_attributes"
    if entry.kind_name == "tag":
        return "tags"
    if entry.kind_name == "posture":
        return "posture"
    if entry.kind_name == "virtual":
        return "unified" if entry.unified_function is not None else "virtual"
    raise ValueError(f"unknown catalog entry kind: {entry.kind_name}")


def _entry_field_id(entry: CatalogEntry):
    if entry.kind_name == "static":
        return StaticField(kind="static", name=entry.display_name)
    if entry.kind_name == "core_enrichment":
        return CoreEnrichmentField(kind="core_enrichment", name=entry.display_name)
    if entry.kind_name == "extended_attribute":
        return ExtendedAttributeField(kind="extended_attribute",
                                      source=entry.source, attribute=entry.attribute)
    if entry.kind_name == "integration_group":
        return IntegrationGroupField(kind="integration_group",
                                     group=entry.group, attribute=entry.attribute)
    if entry.kind_name == "tag":
        return TagField(kind="tag", name=entry.tag_name)
    if entry.kind_name == "posture":
        return PostureField(kind="posture",
                            category=entry.posture_category, read=entry.posture_read)
    if entry.kind_name == "virtual":
        return VirtualField(kind="virtual", name=entry.virtual_name)
    raise ValueError(f"unknown kind: {entry.kind_name}")


def _operator_specs_for(data_type, is_array: bool, allowed=None):
    """Ordered operator_specs for a field (§5.3). label resolved for is_array.

    When ``allowed`` (a sequence of operator codes) is given, the result is
    filtered to those codes — preserving the per-data-type order — so a field can
    expose only the operators its clause builder supports (G-1).
    """
    specs = []
    for op in _operators_for(data_type, is_array=is_array):
        if allowed is not None and op.code not in allowed:
            continue
        specs.append({
            "code": op.code,
            "label": op.array_label if is_array else op.scalar_label,
            "symbol": op.symbol,
            "aliases": list(op.aliases),
            "arity": op.arity,
            "value_type": op.value_type,
        })
    return specs


def build_catalog_response(catalog: "FieldCatalog", include_buckets=None,
                          include_filter_only=False):
    out = []
    for entry in catalog.entries():
        # Off by default so column pickers, pivot pickers and chart wizards
        # cannot offer a field that would hang on those paths. Only the
        # Explorer's search bar opts in.
        if entry.filter_only and not include_filter_only:
            continue
        category = _entry_category(entry)
        if include_buckets is not None and category not in include_buckets:
            continue
        field_id = _entry_field_id(entry)
        out.append({
            "display_name": entry.display_name,
            "field_id": field_id.model_dump(),
            "encoded_key": encode(field_id),
            "category": category,
            "data_type": entry.data_type.value,
            "operator_specs": _operator_specs_for(
                entry.data_type, entry.is_array, entry.allowed_operator_codes
            ),
            "is_array": entry.is_array,
            "is_grouped": entry.is_grouped,
            "filter_only": entry.filter_only,
            "member_sources": list(entry.member_sources) if entry.member_sources else None,
        })
    return {"entries": out}


# Kind vocabulary served by the external field-catalog route (GET /api/v1/fields):
# exactly the FieldId string prefixes encode() can emit. Kept beside the builder
# so the route's filter validation and its 400 message cannot drift from what
# the catalog actually produces.
EXTERNAL_FIELD_KINDS: Tuple[str, ...] = (
    "static", "core", "ext", "group", "tag",
    "posture_state", "posture_output", "virtual",
)


def _plain_field_id(encoded_key: str) -> str:
    """The plain (unencoded) form of an encoded FieldId key: same kind prefix,
    raw segment strings — the form the inventory route accepts in its ``fields``
    CSV. Splitting on ':' is safe because encode() quotes with ``safe=''``, so a
    literal ':' can only ever appear as a segment separator."""
    parts = encoded_key.split(":")
    return ":".join([parts[0]] + [unquote(p) for p in parts[1:]])


def build_external_fields_response(catalog: "FieldCatalog", kinds=None):
    """Public field catalog for the external (API-key) route — one flat,
    machine-consumable entry per selectable field, sorted by (kind, display_name).

    Deliberately NOT build_catalog_response(): that builder's category buckets
    are lossy for an external consumer (target/advanced are both static;
    extended_attributes merges ext and group) and its operator_specs are
    internal UI filter-builder detail. Each entry here carries BOTH the plain
    field_id (the form GET /api/v1/assets/inventory accepts in its ``fields``
    CSV) and the canonical percent-encoded key (the form inventory response
    rows are keyed by), plus the kind prefix so a consumer can filter without
    parsing. ``success`` is included so the route returns this envelope
    verbatim (top-level count/fields, no ``data`` nesting — same shape
    convention as the inventory route).

    filter_only entries are ALWAYS skipped: they cannot be projected by the
    inventory route, so offering one would sell a field that hangs a scan.
    ``kinds`` (optional iterable of prefixes from EXTERNAL_FIELD_KINDS) narrows
    the listing; validation of the values is the route's job.
    """
    out = []
    for entry in catalog.entries():
        if entry.filter_only:
            continue
        encoded_key = encode(_entry_field_id(entry))
        kind = encoded_key.split(":", 1)[0]
        if kinds is not None and kind not in kinds:
            continue
        out.append({
            "field_id": _plain_field_id(encoded_key),
            "field_id_encoded": encoded_key,
            "display_name": entry.display_name,
            "kind": kind,
            "data_type": entry.data_type.value,
            "is_array": entry.is_array,
            "member_sources": (list(entry.member_sources)
                               if entry.member_sources else None),
        })
    out.sort(key=lambda e: (e["kind"], e["display_name"]))
    return {"success": True, "count": len(out), "fields": out}
