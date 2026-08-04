# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Enricher - Data enrichment and normalization for assets.

This module is responsible for:
- OS normalization using modern CPE-compliant approach
- MAC vendor enrichment
- IPAM location matching
- OS End-of-Life (EOL) enrichment
- Data preprocessing and cleaning
"""

import ipaddress
import logging
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional, Dict, Any

import pandas as pd
from intervaltree import IntervalTree

# Import the new OS normalization modules
from src.modules.asset_generation.domain.os_normalization.hybrid_cpe_normalizer import HybridCPENormalizer
from src.modules.asset_generation.domain.mac_utils import canonical_mac_component

logger = logging.getLogger(__name__)

# IPv4 pattern used in hostname/FQDN normalization (string for pandas str.match)
_IPV4_RE = r'^\d{1,3}(\.\d{1,3}){3}$'


@lru_cache(maxsize=4096)
def _classify_ip(ip_str: str) -> str:
    """Classify a single IP string as 'private' or 'public'. Returns 'unknown' for invalid.

    Uses ``is_global`` rather than ``not is_private`` so that special-use ranges
    such as CGNAT (100.64.0.0/10, RFC 6598) and documentation ranges are treated
    as non-public — consistent with "reachable on the public internet" semantics.
    """
    try:
        addr = ip_str.strip().split("/")[0]  # defensive: strip CIDR if present
        return "public" if ipaddress.ip_address(addr).is_global else "private"
    except (ValueError, AttributeError):
        return "unknown"


class AssetEnricher:
    """
    Enriches asset data with vendor information, OS normalization, IPAM matching, and EOL tracking.
    """

    def __init__(
        self,
        os_normalization_cache_dir: Optional[Path] = None,
        postgres_adapter: Optional[Any] = None
    ):
        """
        Initialize asset enricher.

        Args:
            os_normalization_cache_dir: Directory for OS normalization caches
            postgres_adapter: PostgreSQL adapter for EOL data access (optional)
        """
        # Initialize OS normalizer
        logger.info("Initializing Hybrid CPE OS Normalizer")
        self.os_normalizer = HybridCPENormalizer(cache_dir=os_normalization_cache_dir)

        # Initialize caches
        self._mac_vendor_cache = {}
        self._vendor_lookup = None
        self._ipam_location_cache = {}

        # Initialize EOL enrichment (if postgres_adapter is provided)
        self.eol_enricher = None
        if postgres_adapter:
            try:
                from src.modules.asset_generation.services.eol_data_service import EOLDataService
                from src.modules.asset_generation.domain.os_eol_enricher import OSEOLEnricher

                logger.info("Initializing OS EOL enrichment")
                eol_service = EOLDataService(postgres_adapter)
                self.eol_enricher = OSEOLEnricher(eol_service)
                logger.info("OS EOL enrichment initialized successfully")
            except Exception as e:
                logger.warning(f"Could not initialize EOL enrichment: {e}")
                self.eol_enricher = None

        logger.info("AssetEnricher initialized")

    # =========================================================================
    # MAC VENDOR ENRICHMENT
    # =========================================================================

    def enrich_mac_vendor(
        self,
        df: pd.DataFrame,
        mac_vendor_df: pd.DataFrame,
        mac_field: str = 'mac',
        enriched_field: str = 'mac_vendor_enriched'
    ) -> pd.DataFrame:
        """
        Enrich MAC addresses with vendor information using prefix-based lookup.

        Args:
            df: DataFrame with MAC addresses
            mac_vendor_df: DataFrame with vendor prefixes (columns: 'mac_prefix', 'vendor')
            mac_field: Name of MAC address column
            enriched_field: Name of output column for vendor

        Returns:
            DataFrame with enriched vendor information
        """
        start_time = time.time()
        logger.info(f"Enriching MAC vendors for {len(df)} records...")

        @lru_cache(maxsize=4096)
        def normalize_mac(mac: str) -> Optional[str]:
            """Normalize MAC address to standard format with caching."""
            if pd.isna(mac) or not mac:
                return None

            mac_str = str(mac).strip()
            if not mac_str or mac_str.lower() == 'unknown':
                return None

            # Remove all non-alphanumeric characters
            cleaned_mac = ''.join(filter(str.isalnum, mac_str)).lower()

            # Ensure we have at least 6 characters (first 3 octets)
            if len(cleaned_mac) < 6:
                return None

            # Format as colon-separated uppercase (matching database format)
            return ':'.join(cleaned_mac[i:i+2] for i in range(0, 12, 2)).upper()

        # Rebuild the vendor lookup each pipeline run to pick up new vendor data
        # (mac_vendor_df is freshly loaded from the DB each run).
        #
        # Key by separator-free UPPERCASE hex so a prefix's length equals its
        # hex-digit count. mac_vendor_ranges stores two prefix encodings (#681 A9b):
        #   * IEEE-scraper colon format, <=8 hex -- AA:BB:CC (24-bit MA-L),
        #     AA:BB:CC:D (28-bit MA-M), 8C:1F:64:AF (MA-S, 36-bit truncated to 32).
        #   * CIDR notation, e.g. 04:11:19:10/28 -- the /28 netmask (not the literal
        #     digits) defines the 28-bit prefix 0411191, so per-vendor sub-blocks of
        #     a shared OUI key as 7 hex rather than a spurious 10.
        self._vendor_lookup = {}
        self._mac_vendor_cache = {}
        prefix_lengths = set()
        for _, row in mac_vendor_df.iterrows():
            try:
                addr, _sep, mask = str(row['mac_prefix']).strip().partition('/')
                prefix_hex = ''.join(filter(str.isalnum, addr)).upper()
                if mask:
                    prefix_hex = prefix_hex[:int(mask) // 4]
                vendor = str(row['vendor']).strip()
                # Skip rows with no usable prefix or a blank vendor (mac_vendor_ranges
                # has ~35 blank-vendor rows): indexing an empty vendor would enrich a
                # MAC to '' (counted as a successful enrichment) and a blank finer
                # block would shadow the real coarser-OUI vendor.
                if not prefix_hex or not vendor:
                    continue
                self._vendor_lookup[prefix_hex] = vendor
                prefix_lengths.add(len(prefix_hex))
            except (ValueError, KeyError, AttributeError) as e:
                logger.warning(f"Skipping invalid MAC prefix: {row.get('mac_prefix', 'N/A')} - {e}")
                continue
        # Longest-prefix-first: a more-specific MA-M/MA-S block (longer prefix)
        # wins over the shared 24-bit OUI it sits under.
        self._prefix_lengths_desc = sorted(prefix_lengths, reverse=True)
        logger.info(
            f"Built vendor lookup with {len(self._vendor_lookup)} prefixes "
            f"(lengths {self._prefix_lengths_desc})"
        )

        def find_vendor(mac: str) -> Optional[str]:
            """Find the vendor for a MAC via longest-prefix-first lookup.

            Tries the longest stored prefix the MAC can match (a specific
            MA-M/MA-S block) before the shared 24-bit OUI, so IoT/embedded devices
            in a finer block get the right vendor (#681 A9b). None if nothing matches.
            """
            try:
                # Check cache first
                if mac in self._mac_vendor_cache:
                    return self._mac_vendor_cache[mac]

                # Normalize, then match on separator-free hex slices, longest first.
                mac_normalized = normalize_mac(mac)
                if mac_normalized is None:
                    result = None
                else:
                    mac_hex = mac_normalized.replace(':', '')
                    result = None
                    for length in self._prefix_lengths_desc:
                        if length > len(mac_hex):
                            continue
                        result = self._vendor_lookup.get(mac_hex[:length])
                        if result is not None:
                            break
                    if result is None:
                        logger.debug(f"Unknown MAC prefix: {mac_hex[:6]}")

                # Cache the result
                self._mac_vendor_cache[mac] = result
                return result
            except Exception as e:
                logger.warning(f"MAC vendor lookup failed for '{mac}': {e}")
                return None

        # Apply enrichment
        df[enriched_field] = df[mac_field].apply(find_vendor)

        # Count successful enrichments
        enriched_count = df[enriched_field].notna().sum()
        elapsed = time.time() - start_time
        logger.info(
            f"MAC vendor enrichment completed in {elapsed:.2f}s "
            f"({len(df)/elapsed:.0f} records/sec, {enriched_count}/{len(df)} enriched)"
        )

        return df

    # =========================================================================
    # OS NORMALIZATION
    # =========================================================================

    def normalize_os(
        self,
        df: pd.DataFrame,
        os_column: str = 'os',
        output_column: str = 'os_normalized',
        include_cpe: bool = True
    ) -> pd.DataFrame:
        """
        Normalize OS strings using modern CPE-compliant approach.

        Uses the HybridCPENormalizer for high-performance OS normalization
        with CPE 2.3 format output.

        Args:
            df: DataFrame with OS strings
            os_column: Name of column containing raw OS strings
            output_column: Name of output column for normalized OS
            include_cpe: Whether to include CPE and vendor/product columns

        Returns:
            DataFrame with normalized OS information
        """
        start_time = time.time()
        logger.info(f"Normalizing OS for {len(df)} records...")

        # Preserve original OS value before normalization
        if os_column in df.columns:
            df['os_orig'] = df[os_column].copy()
            logger.info(f"Preserved original OS values in os_orig column")

        def normalize_single_os(os_string: str) -> Dict[str, Any]:
            """Normalize a single OS string."""
            empty_result = {
                'os_normalized': None,
                'cpe': None,
                'vendor': None,
                'product': None,
                'version': None,
                'os_family': None,
                'confidence': 0.0
            }
            try:
                if pd.isna(os_string) or not str(os_string).strip():
                    return empty_result

                # Use the new normalizer
                result = self.os_normalizer.normalize(str(os_string))

                return {
                    'os_normalized': result.os_normalized if result.os_normalized else None,
                    'cpe': result.cpe,
                    'vendor': result.vendor,
                    'product': result.product,
                    'version': result.version,
                    'os_family': result.os_family,
                    'confidence': result.confidence
                }
            except Exception as e:
                logger.warning(f"OS normalization failed for '{os_string}': {e}")
                return empty_result

        # Normalize all OS strings
        normalized_results = df[os_column].apply(normalize_single_os)

        # Extract normalized OS
        df[output_column] = normalized_results.apply(lambda x: x['os_normalized'])

        # Add OS enrichment data to JSONB field
        if include_cpe:
            def build_os_enrichment(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                """Build OS enrichment JSONB object."""
                # Only create enrichment object if we have some data
                if not any([result.get('cpe'), result.get('vendor'), result.get('product'),
                           result.get('version'), result.get('os_family')]):
                    return None

                enrichment = {}
                if result.get('cpe'):
                    enrichment['cpe'] = result['cpe']
                if result.get('vendor'):
                    enrichment['vendor'] = result['vendor']
                if result.get('product'):
                    enrichment['product'] = result['product']
                if result.get('version'):
                    enrichment['version'] = result['version']
                if result.get('os_family'):
                    enrichment['os_family'] = result['os_family']
                if result.get('confidence') and result['confidence'] > 0:
                    enrichment['confidence'] = result['confidence']

                return enrichment if enrichment else None

            df['os_enrichment'] = normalized_results.apply(build_os_enrichment)

        elapsed = time.time() - start_time
        logger.info(
            f"OS normalization completed in {elapsed:.2f}s "
            f"({len(df)/elapsed:.0f} records/sec)"
        )

        # Log statistics from normalizer
        if hasattr(self.os_normalizer, 'get_stats'):
            stats = self.os_normalizer.get_stats()
            logger.info(f"OS Normalization stats: {stats}")

        return df

    # =========================================================================
    # IPAM MATCHING
    # =========================================================================

    def match_ipam_location(
        self,
        df: pd.DataFrame,
        ipam_df: pd.DataFrame,
        ip_column: str = 'ip',
        location_column: str = 'ipam_location'
    ) -> pd.DataFrame:
        """
        Match IP addresses to IPAM network locations using interval tree.

        Args:
            df: DataFrame with IP addresses
            ipam_df: DataFrame with IPAM networks (columns: 'network', 'location')
            ip_column: Name of IP address column
            location_column: Name of output column for location

        Returns:
            DataFrame with IPAM location information
        """
        start_time = time.time()
        logger.info(f"Matching IPAM locations for {len(df)} records...")

        def validate_network(cidr: str) -> Optional[ipaddress.IPv4Network]:
            """Validate and parse CIDR network."""
            try:
                return ipaddress.ip_network(cidr, strict=False)
            except (ValueError, TypeError):
                return None

        # Validate networks
        ipam_df['network_obj'] = ipam_df['network'].apply(validate_network)
        ipam_df = ipam_df.dropna(subset=['network_obj'])

        logger.info(f"Building interval tree from {len(ipam_df)} IPAM networks...")

        # Build interval tree for fast lookup
        ipam_tree = IntervalTree()
        for _, row in ipam_df.iterrows():
            network = row['network_obj']
            start_ip = int(network.network_address)
            end_ip = int(network.broadcast_address) + 1
            ipam_tree[start_ip:end_ip] = row['location']

        @lru_cache(maxsize=4096)
        def find_location(ip: str) -> Optional[str]:
            """Find IPAM location for IP address with caching."""
            # Check cache first
            if ip in self._ipam_location_cache:
                return self._ipam_location_cache[ip]

            if pd.isna(ip) or ip == "<NA>" or not str(ip).strip():
                return None

            try:
                ip_addr = ipaddress.ip_address(str(ip).strip())
                ip_int = int(ip_addr)
                matches = ipam_tree[ip_int]
                if matches:
                    # Most-specific network wins: among overlapping IPAM networks
                    # (e.g. 10.0.0.0/8 and 10.1.2.0/24 both containing the IP),
                    # pick the smallest address range (longest prefix) instead of
                    # an arbitrary set-iteration order. (#681 B1)
                    result = min(matches, key=lambda iv: iv.end - iv.begin).data
                else:
                    result = None
            except (ValueError, TypeError):
                result = None

            # Cache the result
            self._ipam_location_cache[ip] = result
            return result

        # Apply location matching
        df[location_column] = df[ip_column].apply(find_location)

        elapsed = time.time() - start_time
        matched_count = df[location_column].notna().sum()
        logger.info(
            f"IPAM matching completed in {elapsed:.2f}s "
            f"({matched_count}/{len(df)} matched, {matched_count/len(df)*100:.1f}%)"
        )

        return df

    # =========================================================================
    # IP CLASSIFICATION
    # =========================================================================

    def enrich_ip_classification(
        self,
        df: pd.DataFrame,
        ip_column: str = 'ip'
    ) -> pd.DataFrame:
        """
        Classify IP addresses per row as private, public, mixed, or none.

        Each row's IP list is split into private and public buckets. The
        derived ``ip_type`` column contains one of:
          - ``private_only``  — all valid IPs are private
          - ``public_only``   — all valid IPs are public
          - ``mixed``         — both private and public IPs present
          - ``none``          — no IPs or only invalid IPs

        Invalid IP strings are silently dropped.

        Args:
            df: DataFrame with an IP column (values are lists of strings or None).
            ip_column: Name of the column containing IP lists.

        Returns:
            A copy of df with three new columns: ip_private, ip_public, ip_type.
        """
        start_time = time.time()
        logger.info(f"Classifying IPs for {len(df)} records...")

        ip_private_col = []
        ip_public_col = []
        ip_type_col = []

        for ip_value in df[ip_column]:
            # Normalise the cell value to a list of strings
            if ip_value is None or (isinstance(ip_value, float) and pd.isna(ip_value)):
                ips = []
            elif isinstance(ip_value, list):
                ips = [str(i) for i in ip_value if i is not None]
            else:
                ips = [str(ip_value)]

            private_list = []
            public_list = []
            for ip_str in ips:
                classification = _classify_ip(ip_str)
                if classification == "private":
                    private_list.append(ip_str)
                elif classification == "public":
                    public_list.append(ip_str)
                # "unknown" → silently dropped

            has_private = bool(private_list)
            has_public = bool(public_list)

            if has_private and has_public:
                ip_type = "mixed"
            elif has_private:
                ip_type = "private_only"
            elif has_public:
                ip_type = "public_only"
            else:
                ip_type = "none"

            ip_private_col.append(private_list)
            ip_public_col.append(public_list)
            ip_type_col.append(ip_type)

        result = df.copy()
        result["ip_private"] = ip_private_col
        result["ip_public"] = ip_public_col
        result["ip_type"] = ip_type_col

        elapsed = time.time() - start_time
        counts = pd.Series(ip_type_col).value_counts().to_dict()
        logger.info(
            f"IP classification completed in {elapsed:.2f}s — "
            f"private_only={counts.get('private_only', 0)}, "
            f"public_only={counts.get('public_only', 0)}, "
            f"mixed={counts.get('mixed', 0)}, "
            f"none={counts.get('none', 0)}"
        )

        return result

    # =========================================================================
    # DATA PREPROCESSING
    # =========================================================================

    def preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Preprocess asset data - fill missing values and standardize format.

        Args:
            df: DataFrame to preprocess

        Returns:
            Preprocessed DataFrame
        """
        start_time = time.time()
        logger.info(f"Preprocessing {len(df)} records...")

        # Deduplicate rows that are identical across all asset attributes
        # within the same data source. Rows with ANY difference (e.g. different
        # MAC addresses) are kept — only true duplicates are collapsed.
        dedup_cols = [
            'hostname', 'fqdn', 'ip', 'mac', 'sn', 'os',
            'manufacturer', 'model', 'vendor_category', 'data_source',
        ]
        available_dedup_cols = [c for c in dedup_cols if c in df.columns]
        if available_dedup_cols:
            # text[] columns (migration 054) arrive as Python lists (possibly
            # nested) which pandas can't hash.  Build a hashable shadow copy
            # for duplicate detection without mutating the original data.
            shadow = df[available_dedup_cols].apply(
                lambda col: col.map(lambda x: str(x) if isinstance(x, list) else x)
            )
            before = len(df)
            df = df[~shadow.duplicated(keep='first')].copy()
            dropped = before - len(df)
            if dropped:
                logger.info(
                    f"Deduplicated {dropped} identical rows "
                    f"(kept {len(df)} of {before})"
                )

        # Extract fields from extended_fields JSONB that match asset schema
        if 'extended_fields' in df.columns:
            # Define asset schema fields that can be extracted from extended_fields
            extractable_fields = {
                'owner', 'location', 'user_field',
                'first_time', 'last_time', 'category', 'subcategory', 'manufacturer',
                'model', 'vendor_category'
            }

            # Also check for user -> user_field mapping
            field_mappings = {
                'user': 'user_field'  # Map 'user' from extended_fields to 'user_field' column
            }

            extracted_fields = []

            # Determine which fields need extraction (column missing or entirely null)
            fields_needing_extraction = {
                f for f in extractable_fields
                if f not in df.columns or df[f].isna().all()
            }
            mapped_targets_needing = {
                target: source
                for source, target in field_mappings.items()
                if target not in df.columns or df[target].isna().all()
            }

            # Collect all dict keys to extract in a single pass
            keys_to_extract = fields_needing_extraction | set(mapped_targets_needing.values())

            if keys_to_extract:
                # Single-pass extraction: pull all needed keys from each row's dict
                def extract_all_fields(extended_fields):
                    if not isinstance(extended_fields, dict):
                        return {k: None for k in keys_to_extract}
                    return {k: extended_fields.get(k) for k in keys_to_extract}

                extracted_df = df['extended_fields'].apply(extract_all_fields).apply(pd.Series)

                # Assign directly-named extractable fields
                for field_name in fields_needing_extraction:
                    if field_name in extracted_df.columns:
                        df[field_name] = extracted_df[field_name]
                        field_count = df[field_name].notna().sum()
                        if field_count > 0:
                            extracted_fields.append(f"{field_name}({field_count})")

                # Handle field mappings (e.g., 'user' in dict -> 'user_field' column)
                # Only apply if the target column is still empty after direct extraction
                for target_field, source_field in mapped_targets_needing.items():
                    if target_field not in df.columns or df[target_field].isna().all():
                        if source_field in extracted_df.columns:
                            df[target_field] = extracted_df[source_field]
                            field_count = df[target_field].notna().sum()
                            if field_count > 0:
                                extracted_fields.append(f"{target_field}({field_count})")

            if extracted_fields:
                logger.info(f"Extracted {len(extracted_fields)} fields from extended_fields: {', '.join(extracted_fields)}")

            # Remove extracted fields from extended_fields to avoid duplication
            def clean_extended_fields(extended_fields):
                if pd.isna(extended_fields) or not isinstance(extended_fields, dict):
                    return extended_fields

                cleaned = extended_fields.copy()
                # Remove all extractable fields and mapped source fields
                for field in extractable_fields | set(field_mappings.keys()):
                    cleaned.pop(field, None)

                return cleaned if cleaned else None

            df['extended_fields'] = df['extended_fields'].apply(clean_extended_fields)

            # Note: extended_fields will be restructured by data_source during aggregation
            # to avoid pandas issues with nested dicts during row operations

            # Convert timestamp fields to datetime
            for time_field in ['first_time', 'last_time']:
                if time_field in df.columns:
                    df[time_field] = self._parse_timestamps(df[time_field])
                    converted_count = df[time_field].notna().sum()
                    if converted_count > 0:
                        logger.info(f"Converted {time_field} to datetime: {converted_count} records")

        # Normalize hostname/FQDN field placement
        # Detects when FQDNs end up in hostname column (or vice versa) and corrects
        if 'hostname' in df.columns and 'fqdn' in df.columns:
            # Skip rows where hostname or fqdn is a Python list (multi-value
            # text[] cell). Without this guard, .astype(str) would stringify
            # the list into "['x']" which silently corrupts the value. Those
            # rows are normalized later: s02's _explode_array_identifiers
            # expands them into scalar rows, and any per-element FQDN handling
            # happens inside create_asset_for_row.
            scalar_rows = ~(
                df['hostname'].apply(lambda x: isinstance(x, list))
                | df['fqdn'].apply(lambda x: isinstance(x, list))
            )

            def _fqdn_mask(series):
                """Vectorized: True if value contains dots and is not an IP."""
                s = series.fillna('').astype(str).str.strip()
                present = series.notna() & (s != '')
                has_dot = s.str.contains('.', regex=False)
                is_ip = s.str.match(_IPV4_RE)
                return present & has_dot & ~is_ip

            hostname_is_fqdn = _fqdn_mask(df['hostname']) & scalar_rows
            fqdn_is_fqdn = _fqdn_mask(df['fqdn']) & scalar_rows
            hostname_str = (
                df['hostname'].where(scalar_rows).fillna('').astype(str).str.strip()
            )
            fqdn_str = (
                df['fqdn'].where(scalar_rows).fillna('').astype(str).str.strip()
            )
            hostname_present = df['hostname'].notna() & (hostname_str != '') & scalar_rows
            fqdn_present = df['fqdn'].notna() & (fqdn_str != '') & scalar_rows
            fqdn_is_ip = fqdn_str.str.match(_IPV4_RE)
            fqdn_is_short = fqdn_present & ~fqdn_is_fqdn & ~fqdn_is_ip

            counts = {'moved_to_fqdn': 0, 'moved_to_hostname': 0, 'swapped': 0, 'cleared_fqdn': 0}

            # Case 1: hostname=FQDN, fqdn=empty -> move hostname to fqdn, extract short
            mask1 = hostname_is_fqdn & ~fqdn_present
            if mask1.any():
                df.loc[mask1, 'fqdn'] = df.loc[mask1, 'hostname']
                df.loc[mask1, 'hostname'] = df.loc[mask1, 'hostname'].str.split('.').str[0]
                counts['moved_to_fqdn'] = int(mask1.sum())

            # Case 2: hostname=FQDN, fqdn=short -> swap
            mask2 = hostname_is_fqdn & fqdn_is_short
            if mask2.any():
                orig_hostname = df.loc[mask2, 'hostname'].copy()
                df.loc[mask2, 'hostname'] = df.loc[mask2, 'fqdn']
                df.loc[mask2, 'fqdn'] = orig_hostname
                counts['swapped'] = int(mask2.sum())

            # Case 3: hostname=FQDN, fqdn=also FQDN -> extract short from hostname, keep fqdn
            mask3 = hostname_is_fqdn & fqdn_is_fqdn
            if mask3.any():
                df.loc[mask3, 'hostname'] = df.loc[mask3, 'hostname'].str.split('.').str[0]
                counts['moved_to_fqdn'] += int(mask3.sum())

            # Case 4: hostname=empty, fqdn=short -> move to hostname, clear fqdn
            mask4 = ~hostname_present & fqdn_is_short
            if mask4.any():
                df.loc[mask4, 'hostname'] = df.loc[mask4, 'fqdn']
                df.loc[mask4, 'fqdn'] = None
                counts['moved_to_hostname'] = int(mask4.sum())

            # Case 5: hostname=short, fqdn=short -> clear fqdn (not a real FQDN)
            mask5 = hostname_present & ~hostname_is_fqdn & fqdn_is_short
            if mask5.any():
                df.loc[mask5, 'fqdn'] = None
                counts['cleared_fqdn'] = int(mask5.sum())

            total = sum(counts.values())
            if total:
                logger.info(
                    f"Hostname/FQDN normalization: {total} rows adjusted "
                    f"({counts})"
                )

        # Normalize MAC addresses if column exists
        if 'mac' in df.columns:
            def normalize_mac_address(mac):
                """Normalize MAC address to standard format (lowercase, colon-separated).

                Identifier columns are text[] in raw_asset_data and arrive here
                as Python lists when a record carries multiple MACs (multi-NIC
                hosts, or multi-source mappings). pd.isna() on a multi-element
                list returns an array, which then explodes the surrounding
                truthy check. Handle lists by recursing per element and
                preserving the list shape so s02's _explode_array_identifiers
                can still expand them into separate rows.

                The scalar case delegates to the shared canonical form
                (mac_utils.canonical_mac_component) so the denylist encoder
                and the pipeline never diverge.
                """
                if isinstance(mac, list):
                    normalized = [normalize_mac_address(m) for m in mac]
                    normalized = [m for m in normalized if m is not None]
                    if not normalized:
                        return None
                    return normalized if len(normalized) > 1 else normalized[0]

                return canonical_mac_component(mac)

            df['mac'] = df['mac'].apply(normalize_mac_address)
            logger.info("Normalized MAC addresses to standard format")

        # DO NOT fill with "unknown" - keep NULL/None values
        # This makes data lineage clear and improves data quality
        # Enrichment functions will handle missing values appropriately

        # Only fill numeric columns with 0 if needed
        for col in df.columns:
            if pd.api.types.is_float_dtype(df[col]):
                # Fill numeric columns with 0
                df[col] = df[col].fillna(0.0)

        elapsed = time.time() - start_time
        logger.info(f"Preprocessing completed in {elapsed:.2f}s")

        return df

    def preprocess_list(self, data_list: list[Dict]) -> pd.DataFrame:
        """
        Convert list of dictionaries to preprocessed DataFrame.

        Args:
            data_list: List of asset data dictionaries

        Returns:
            Preprocessed DataFrame
        """
        df = pd.DataFrame(data_list)
        return self.preprocess(df)

    # =========================================================================
    # OS EOL ENRICHMENT
    # =========================================================================

    def enrich_os_eol(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Enrich assets with OS End-of-Life information.

        This enrichment should be called AFTER OS normalization has been performed.
        It looks up EOL data for the normalized OS and version, and adds:
        - os_eol_date: End of life date
        - os_support_end_date: End of standard support date
        - os_extended_support_date: End of extended support date (if available)
        - os_is_lts: Whether the OS is a Long-Term Support version
        - os_eol_status: Current EOL status (active, approaching_eol, end_of_life, unknown)
        - os_eol_checked_at: Timestamp when EOL data was checked

        Args:
            df: DataFrame with assets (must have os_normalized column)

        Returns:
            DataFrame with EOL enrichment added

        Note:
            If EOL enrichment is not initialized (no postgres_adapter provided),
            this method will return the DataFrame unchanged with a warning.
        """
        if self.eol_enricher is None:
            logger.warning("EOL enrichment not initialized - skipping EOL enrichment")
            # Add empty EOL columns to maintain schema consistency
            df['os_eol_date'] = None
            df['os_support_end_date'] = None
            df['os_extended_support_date'] = None
            df['os_is_lts'] = None
            df['os_eol_status'] = 'unknown'
            df['os_eol_checked_at'] = None
            return df

        start_time = time.time()
        logger.info(f"Enriching {len(df)} assets with OS EOL information...")

        # Perform EOL enrichment
        try:
            enriched_df = self.eol_enricher.enrich_dataframe(df)

            elapsed = time.time() - start_time
            logger.info(f"OS EOL enrichment completed in {elapsed:.2f}s")

            # Log enrichment statistics
            stats = self.eol_enricher.get_enrichment_stats()
            logger.info(
                f"EOL enrichment stats: {stats['with_eol_data']}/{stats['total_processed']} assets enriched "
                f"({stats.get('enrichment_rate', 0):.1f}% success rate)"
            )
            logger.info(
                f"EOL status distribution: "
                f"active={stats['active']}, "
                f"approaching_eol={stats['approaching_eol']}, "
                f"end_of_life={stats['end_of_life']}, "
                f"unknown={stats['unknown']}"
            )

            # Debug: Verify EOL columns exist and have data
            eol_cols = ['os_eol_status', 'os_eol_date', 'os_support_end_date', 'os_is_lts']
            present_cols = [col for col in eol_cols if col in enriched_df.columns]
            logger.info(f"DEBUG: EOL columns in enriched DataFrame: {present_cols}")
            for col in present_cols:
                non_null = enriched_df[col].notna().sum()
                logger.info(f"DEBUG:   {col}: {non_null} non-null, {len(enriched_df) - non_null} null")

            return enriched_df

        except Exception as e:
            logger.error(f"Error during EOL enrichment: {e}", exc_info=True)
            # Add empty EOL columns to maintain schema consistency
            df['os_eol_date'] = None
            df['os_support_end_date'] = None
            df['os_extended_support_date'] = None
            df['os_is_lts'] = None
            # 'unknown' is the constraint-valid (chk_os_eol_status) "couldn't
            # determine it" value. An out-of-set literal here would violate the
            # CHECK on assets.os_eol_status and abort the whole atomic run.
            df['os_eol_status'] = 'unknown'
            df['os_eol_checked_at'] = None
            return df

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    @staticmethod
    def _parse_timestamps(series: pd.Series) -> pd.Series:
        """Parse a Series of timestamps in mixed formats to datetime.

        Handles: ISO 8601 strings, human-readable dates, unix epoch seconds
        (int or string), unix epoch milliseconds, and values that are already
        datetime (e.g. from TIMESTAMPTZ columns).  All results are UTC-aware.
        """
        if series.empty:
            return pd.Series(pd.NaT, index=series.index, dtype='datetime64[ns, UTC]')

        # If the series is already datetime-typed (e.g. loaded from a
        # TIMESTAMPTZ column), just ensure it's UTC-aware and return.
        if pd.api.types.is_datetime64_any_dtype(series):
            result = series.copy()
            if result.dt.tz is None:
                result = result.dt.tz_localize('UTC')
            return result

        # Object-dtype series may contain datetime objects (e.g. after
        # pd.concat of batches where some have TIMESTAMPTZ data and
        # others have NULLs).  Convert directly via pd.to_datetime.
        if series.dtype == object:
            sample = series.dropna().head(5)
            if len(sample) > 0 and all(isinstance(v, datetime) for v in sample):
                return pd.to_datetime(series, errors='coerce', utc=True)

        raw = series

        # Separate numeric values (epoch) from string dates
        numeric = pd.to_numeric(raw, errors='coerce')
        is_numeric = numeric.notna()

        # Start with tz-aware NaT series — use Series (not array) for label-based indexing
        result = pd.Series(pd.NaT, index=series.index, dtype='datetime64[ns, UTC]')

        if is_numeric.any():
            # A bare YYYYMMDD calendar date (e.g. 20250615) is all-digits and
            # ~2e7, so without this it would parse as epoch SECONDS -> a 1970
            # date. Real epoch seconds for modern timestamps are ~1e9+ (10
            # digits), so an 8-digit value in the YYYYMMDD range that parses as
            # a valid %Y%m%d date is a calendar date, not epoch. (#681 B1)
            # A bare YYYYMMDD is always an integer; the (numeric % 1 == 0) guard
            # keeps a fractional in-range float (e.g. a sub-second epoch value
            # like 20250615.5) off this path -- it stays on the epoch path (its
            # pre-fix behavior) instead of aborting the whole-batch parse on the
            # int cast (pandas refuses to cast a non-integral float to int).
            is_ymd_candidate = (
                is_numeric
                & (numeric >= 19000101) & (numeric <= 99991231)
                & (numeric % 1 == 0)
            )
            ymd = pd.Series(pd.NaT, index=series.index, dtype='datetime64[ns, UTC]')
            if is_ymd_candidate.any():
                ymd.loc[is_ymd_candidate] = pd.to_datetime(
                    numeric[is_ymd_candidate].astype('int64').astype(str),
                    format='%Y%m%d', errors='coerce', utc=True,
                )
            is_ymd = ymd.notna()           # only values that parse as a valid date
            result.loc[is_ymd] = ymd[is_ymd]

            # Epoch handling for the remaining numeric values (YYYYMMDD excluded).
            is_ms = is_numeric & ~is_ymd & (numeric > 1e11)
            is_sec = is_numeric & ~is_ymd & ~is_ms
            if is_sec.any():
                result.loc[is_sec] = pd.to_datetime(numeric[is_sec], unit='s', errors='coerce', utc=True)
            if is_ms.any():
                result.loc[is_ms] = pd.to_datetime(numeric[is_ms], unit='ms', errors='coerce', utc=True)

        # Parse the rest as date strings (format='mixed' handles heterogeneous formats)
        is_string = ~is_numeric & raw.notna() & (raw.astype(str).str.strip() != '')
        if is_string.any():
            result.loc[is_string] = pd.to_datetime(raw[is_string], errors='coerce', utc=True, format='mixed')

        return result

    def get_enrichment_stats(self) -> Dict[str, Any]:
        """
        Get statistics about enrichment operations.

        Returns:
            Dictionary with enrichment statistics
        """
        stats = {
            'mac_vendor_cache_size': len(self._mac_vendor_cache),
            'ipam_location_cache_size': len(self._ipam_location_cache),
        }

        # Add OS normalizer stats if available
        if hasattr(self.os_normalizer, 'get_stats'):
            stats['os_normalization'] = self.os_normalizer.get_stats()

        # Add EOL enrichment stats if available
        if self.eol_enricher:
            stats['eol_enrichment'] = self.eol_enricher.get_enrichment_stats()

        return stats

    def clear_caches(self) -> None:
        """Clear all internal caches."""
        self._mac_vendor_cache.clear()
        self._ipam_location_cache.clear()
        _classify_ip.cache_clear()

        # Clear EOL cache if available
        if self.eol_enricher:
            self.eol_enricher.clear_cache()

        logger.info("Enrichment caches cleared")

    def get_cache_stats(self) -> Dict[str, int]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache sizes
        """
        return {
            'mac_vendor_cache': len(self._mac_vendor_cache),
            'ipam_location_cache': len(self._ipam_location_cache),
        }
