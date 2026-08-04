# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Validator - Data validation and quality checking for asset components.

This module is responsible for:
- Serial number validation and quality scoring
- IP address validation
- Data integrity checks
- Suspicious data detection
"""

import ipaddress
import logging
import re
import time
from functools import lru_cache
from typing import Any, Dict, Set, Tuple, TYPE_CHECKING

import pandas as pd

from src.modules.asset_generation.domain.manufacturer_normalizer import normalize_manufacturer, is_known_manufacturer
from src.modules.asset_generation.domain.sn_validation_enhanced import EnhancedSNValidator

# Annotation only. merge_denylist already depends on this module (its encoder
# imports mac_utils, which imports AssetValidator), so the runtime dependency
# stays one-way.
if TYPE_CHECKING:
    from src.modules.merge_denylist.matcher import DenylistMatcher

logger = logging.getLogger(__name__)

# Values that represent missing data after DataFrame columns are normalized
# to lowercase strings (pd.astype(str).str.lower()).  Used to filter out
# stringified None/NaN/NaT before frequency analysis in _build_exclusion_sets.
_NORMALIZED_NULLS = frozenset({'', 'none', 'nan', 'nat'})


class AssetValidator:
    """
    Validates asset data components and checks for suspicious/placeholder values.
    """

    def __init__(
        self,
        sn_overuse_threshold: int = 4,
        sn_migration_threshold: int = 2,
        consecutive_char_length_threshold: int = 4,
        device_kb=None,
    ):
        """
        Initialize validator with configurable thresholds.

        Args:
            sn_overuse_threshold: Number of hostnames before SN considered overused
            sn_migration_threshold: Threshold for potential device migration
            consecutive_char_length_threshold: Length for consecutive char detection
        """
        # Common placeholder serial numbers (multilingual support)
        self.common_sn_placeholders = frozenset({
            "unknown", "none", "n/a", "", "na", "null",
            "to be filled by o.e.m.", "system serial number", "default string",
            "00000000", "12345678", "123456789", "0", "not available",
            "oem", "o.e.m", "not specified", "chassis s/n", "no serial number",
            "serialnumber", "systemname", "virtual chassi", "vmware",
            "серійний номер відсутній",  # Ukrainian
            "no disponible",  # Spanish
            "nicht verfügbar"  # German
        })

        # Generic/placeholder hostnames that cannot distinguish between devices
        self.generic_hostnames = frozenset({
            'iphone', 'ipad', 'android', 'watch', 'pc', 'laptop', 'desktop',
            'computer', 'workstation', 'server', 'host', 'device',
            'unknown', 'none', 'default', 'test', 'temp', 'guest',
            'user', 'admin', 'root', 'localhost'
        })

        # Dynamic placeholder patterns — compiled once (#81)
        self._placeholder_hostname_patterns = [
            re.compile(r'^device[-_]\d+$', re.IGNORECASE),
            re.compile(r'^host[-_]?\d*$', re.IGNORECASE),
            re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE),  # UUID
            re.compile(r'^PC\d+$', re.IGNORECASE),
            re.compile(r'^DESKTOP-[A-Z0-9]{7}$', re.IGNORECASE),  # Windows default
            re.compile(r'^HTTP-[\w]+-[\w.-]+$', re.IGNORECASE),  # LDAP SPN entries (HTTP/host)
            re.compile(r'^sappcc-[\w]+-[\w.-]+$', re.IGNORECASE),  # SAP SPN entries from LDAP
            # Device-model hostnames from Entra — model names, not unique identifiers (#59 Item 9)
            re.compile(r'^poly(?:ccx|studio|tc|com)\w*$', re.IGNORECASE),  # PolyCCX500, PolyStudioX30, PolyTC8
            re.compile(r'^polypoly\w+$', re.IGNORECASE),  # PolyPolyStudioX30, PolyPolyTC8
            re.compile(r'^audiocodes?\w+$', re.IGNORECASE),  # AudioCodesC455HD
            re.compile(r'^yealink\w+$', re.IGNORECASE),  # YealinkMP56
            re.compile(r'^samsung[a-z]{2}-[a-z0-9]+$', re.IGNORECASE),  # samsungSM-A536B
            re.compile(r'^windows-phone$', re.IGNORECASE),
        ]

        # Invalid IP prefixes (loopback, APIPA/link-local)
        self.invalid_ip_prefixes = ('127.', '169.254.')

        # Invalid/placeholder IP addresses
        self.invalid_ips = frozenset({'0.0.0.0', '255.255.255.255', '::'})

        # Invalid/placeholder MAC addresses
        self.invalid_macs = frozenset({
            'ff:ff:ff:ff:ff:ff',  # Broadcast
            '00:00:00:00:00:00',  # Zero/placeholder
        })

        # Hostname quality constraints
        self.min_hostname_length = 5  # Hostnames must be at least 5 characters

        self.sn_overuse_threshold = sn_overuse_threshold
        self.sn_migration_threshold = sn_migration_threshold
        self.consecutive_char_length_threshold = consecutive_char_length_threshold

        # Pre-compile regex patterns for performance
        self._consecutive_char_pattern = re.compile(
            r'(.)\1{' + str(consecutive_char_length_threshold - 1) + r'}'
        )
        self._simple_sequences = frozenset({"1234", "12345", "123456", "1234567", "12345678"})

        # Date pattern in hostname (e.g., "5/8/2025", "5-8-2025")
        self._hostname_date_pattern = re.compile(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}')

        # Hostname must contain at least one letter
        self._hostname_has_letter_pattern = re.compile(r'[a-zA-Z]')

        # Intune mobile timestamp suffix (e.g., "_10/16/2025_7:43 PM")
        self._intune_timestamp_pattern = re.compile(
            r'_\d{1,2}/\d{1,2}/\d{4}_\d{1,2}:\d{2}\s*[AP]M$',
            re.IGNORECASE
        )

        # Initialize Enhanced SN Validator for improved serial number validation
        self.enhanced_sn_validator = EnhancedSNValidator(enable_vendor_validation=True)

        # Populated by create_asset_field(); used by service for exclusion reasons (#287)
        self.last_exclusion_sets = None

        # Identifier denylist, keyed on canonical component form. Loaded per-run
        # by the Stage-2 stage before create_asset_field; empty means "no
        # denylist". Anything supporting `in` belongs here: a DenylistMatcher in
        # the pipeline (exact entries plus MAC prefix/range and hostname/fqdn
        # wildcards, #991), a plain frozenset on Stage 2's fail-open path.
        self.denylist_components: "DenylistMatcher | frozenset[str]" = frozenset()

        self.device_kb = device_kb

        logger.info("AssetValidator initialized with EnhancedSNValidator")

    @staticmethod
    @lru_cache(maxsize=1024)
    def is_valid_ip_address(component_string: str) -> bool:
        """
        Check if a string is a valid IP address (IPv4 or IPv6) with caching.

        Args:
            component_string: String to validate

        Returns:
            True if valid IP address, False otherwise
        """
        if not component_string or not isinstance(component_string, str):
            return False

        try:
            ipaddress.ip_address(component_string)
            return True
        except ValueError:
            return False

    # Virtual MAC address prefixes (OUI - Organizationally Unique Identifier)
    VIRTUAL_MAC_PREFIXES = {
        # VMware
        '00:50:56': 'VMware',
        '00:0c:29': 'VMware',
        '00:05:69': 'VMware',
        '00:1c:14': 'VMware',

        # VirtualBox
        '08:00:27': 'VirtualBox',

        # Microsoft Hyper-V
        '00:15:5d': 'Hyper-V',
        '00:03:ff': 'Hyper-V',

        # KVM/QEMU
        '52:54:00': 'KVM/QEMU',
        '52:54:01': 'KVM/QEMU',

        # Xen
        '00:16:3e': 'Xen',

        # Proxmox VE — auto-assigned guest (VM/LXC) vNIC OUI. Globally-administered
        # (0xBC clears the locally-administered bit), so it escapes the LAA
        # heuristic and must be listed explicitly. Without this, a Proxmox host
        # over-merges with every guest sharing its bridge (see hypervisor-01).
        'bc:24:11': 'Proxmox',

        # Docker (2 octets only)
        '02:42': 'Docker',

        # Parallels
        '00:1c:42': 'Parallels',

        # Cloud providers
        '02:00:5e': 'AWS',
        '02:50:00': 'AWS',
        '02:ae:5f': 'AWS',
        '42:01': 'Google Cloud',  # 2 octets only

        # Docking stations / peripherals (shared across hosts)
        '8c:ae:4c': 'Plugable Dock',
        '9c:eb:e8': 'BizLink Dock',
        '00:0e:9a': 'BOE Display',

        # Network infrastructure (switches should not be identity components)
        '30:63:ea': 'Juniper',
        'a4:e1:1a': 'Juniper',
        'd0:81:c5': 'Juniper',
        '68:ed:57': 'Juniper',
        '58:e4:34': 'Juniper',
        '48:5a:0d': 'Juniper',
        'e4:5e:cc': 'Juniper',

        # Shared appliance / bridge MACs: globally-administered OUIs that
        # escape the locally-administered-bit heuristic and so must be listed
        # explicitly. They recur as a constant value across many unrelated
        # assets and pollute the persisted mac[] array. (#681 D3 / GP-1)
        'ac:de:48': 'Apple',  # Apple bridge (Thunderbolt/internet-sharing)
        '00:09:0f': 'Fortinet',  # Fortinet shared/HA MAC
    }

    @staticmethod
    @lru_cache(maxsize=2048)
    def is_virtual_mac(mac: str) -> tuple[bool, str]:
        """
        Check if MAC address belongs to virtual infrastructure with caching.

        Virtual MACs are from hypervisors (VMware, VirtualBox, Hyper-V, KVM, Xen),
        container platforms (Docker), or cloud providers (AWS, GCP, Azure).

        These MACs should not be persisted to mappings because:
        - VMs can be cloned with the same MAC
        - Containers get ephemeral MACs
        - Cloud instances can be recreated with same MACs

        Args:
            mac: MAC address (various formats supported: colon, dash, or no separator)

        Returns:
            Tuple of (is_virtual, vendor_name)
            - is_virtual: True if MAC is from virtual infrastructure
            - vendor_name: Name of hypervisor/platform (or 'Physical' if not virtual)
        """
        if not mac or not isinstance(mac, str):
            return (False, 'Physical')

        # Normalize MAC format: lowercase, use colons
        mac_normalized = mac.lower().replace('-', ':').strip()

        # Validate MAC format: must be 6 groups of 2 hex digits separated by colons
        # Format: XX:XX:XX:XX:XX:XX where X is hex digit [0-9a-f]
        parts = mac_normalized.split(':')
        if len(parts) != 6:
            # Not a valid MAC address format
            return (False, 'Physical')

        # Check each part is exactly 2 hex characters
        for part in parts:
            if len(part) != 2:
                return (False, 'Physical')
            try:
                int(part, 16)
            except ValueError:
                # Not a valid hex value
                return (False, 'Physical')

        # Check against known virtual prefixes
        for prefix, vendor in AssetValidator.VIRTUAL_MAC_PREFIXES.items():
            if mac_normalized.startswith(prefix.lower()):
                return (True, vendor)

        # Check locally administered bit (secondary indicator)
        # The 2nd bit of the first octet being set often indicates virtual/local MACs
        try:
            first_octet_str = mac_normalized.split(':')[0]
            if len(first_octet_str) == 2:
                first_octet = int(first_octet_str, 16)
                if first_octet & 0x02:  # Bit 1 (2nd bit) is set
                    # This could be virtual, but not certain
                    # Many virtual MACs have this bit set, but so do some physical
                    return (True, 'Locally Administered')
        except (ValueError, IndexError):
            pass

        # Physical MAC
        return (False, 'Physical')

    def is_valid_hostname(self, hostname: str) -> bool:
        """
        Validate hostname quality for asset identification.

        Filters out:
        - Too short hostnames (<=4 characters)
        - Generic/placeholder names (iphone, ipad, pc, laptop, etc.)
        - Hostnames without any letters (numeric only or special chars only)
        - Hostnames containing date patterns (5/8/2025, 5-8-2025, etc.)

        Args:
            hostname: Hostname to validate

        Returns:
            True if hostname is valid for asset identification, False otherwise
        """
        if not hostname or not isinstance(hostname, str):
            return False

        hostname_lower = hostname.lower().strip()

        # Too short (<=4 characters)
        if len(hostname_lower) < self.min_hostname_length:
            return False

        # Generic/placeholder hostname
        if hostname_lower in self.generic_hostnames:
            return False

        # Check dynamic placeholder patterns
        for pattern in self._placeholder_hostname_patterns:
            if pattern.match(hostname_lower):
                return False

        # Must contain at least one letter
        if not self._hostname_has_letter_pattern.search(hostname):
            return False

        # Contains date pattern (e.g., "5/8/2025", "5-8-2025")
        if self._hostname_date_pattern.search(hostname):
            return False

        return True

    def normalize_intune_hostname(self, hostname):
        """
        Strip Intune mobile timestamp suffixes from hostnames.

        Intune mobile devices produce hostnames like
        'Scott.Andrick_AndroidAOSP_10/16/2025_7:43 PM' where each sync
        appends a different timestamp. This normalizes them to a stable
        base hostname (e.g., 'Scott.Andrick_AndroidAOSP').

        Args:
            hostname: Hostname string, or None/empty.

        Returns:
            Normalized hostname with timestamp suffix removed, or the
            original value if no match or input is None/empty.
        """
        if not hostname or not isinstance(hostname, str):
            return hostname
        return self._intune_timestamp_pattern.sub('', hostname)

    def is_valid_ip(self, ip: str) -> bool:
        """
        Validate IP address quality for asset identification.

        Filters out:
        - Loopback addresses (127.x.x.x)
        - APIPA/Link-local addresses (169.254.x.x)
        - Invalid/placeholder IPs (0.0.0.0, 255.255.255.255, ::)

        Args:
            ip: IP address to validate

        Returns:
            True if IP is valid for asset identification, False otherwise
        """
        if not ip or not isinstance(ip, str):
            return False

        ip_str = str(ip).strip()

        # First check if it's a valid IP format
        if not self.is_valid_ip_address(ip_str):
            return False

        # Invalid/placeholder IPs
        if ip_str in self.invalid_ips:
            return False

        # Loopback or APIPA/link-local
        if ip_str.startswith(self.invalid_ip_prefixes):
            return False

        return True

    def filter_invalid_ips(self, ips):
        """Drop loopback/APIPA/placeholder IPs from a list, keeping valid ones.

        The persisted ip[] array hygiene counterpart to is_valid_ip (which the
        merge-key layer already uses). (#681 D3)
        """
        if not ips:
            return []
        return [ip for ip in ips if self.is_valid_ip(ip)]

    def is_valid_mac(self, mac: str) -> bool:
        """
        Validate MAC address format and quality for asset identification.

        Requires exactly 12 hex characters. Filters out:
        - Non-hex characters
        - Fewer or more than 12 hex chars
        - Broadcast MAC (ff:ff:ff:ff:ff:ff)
        - Zero/placeholder MAC (00:00:00:00:00:00)

        Args:
            mac: MAC address to validate

        Returns:
            True if MAC is valid for asset identification, False otherwise
        """
        if not mac or not isinstance(mac, str):
            return False

        # Strip separators and whitespace, extract only alphanumeric chars
        cleaned = ''.join(c for c in mac if c.isalnum()).lower()

        # Must be exactly 12 hex characters
        if len(cleaned) != 12:
            return False
        if not all(c in '0123456789abcdef' for c in cleaned):
            return False

        # Check against invalid/placeholder MACs (normalize to colon-separated)
        mac_normalized = ':'.join(cleaned[i:i+2] for i in range(0, 12, 2))
        if mac_normalized in self.invalid_macs:
            return False

        return True

    def has_sequential_numbers(self, s: str, length: int = None) -> bool:
        """
        Check for sequential numbers (e.g., "123456").

        Args:
            s: String to check
            length: Sequence length (default: consecutive_char_length_threshold)

        Returns:
            True if sequential numbers found
        """
        if length is None:
            length = self.consecutive_char_length_threshold

        if len(s) < length:
            return False

        for i in range(len(s) - length + 1):
            substring = s[i:i + length]
            if substring.isdigit():
                # Check if digits are sequential
                is_sequential = all(
                    int(substring[k]) == int(substring[0]) + k
                    for k in range(1, length)
                )
                if is_sequential:
                    return True

        return False

    def has_sequential_letters(self, s: str, length: int = None) -> bool:
        """
        Check for sequential letters (e.g., "abcdef").

        Args:
            s: String to check
            length: Sequence length (default: consecutive_char_length_threshold)

        Returns:
            True if sequential letters found
        """
        if length is None:
            length = self.consecutive_char_length_threshold

        s_lower = s.lower()
        if len(s_lower) < length:
            return False

        for i in range(len(s_lower) - length + 1):
            substring = s_lower[i:i + length]
            if substring.isalpha():
                # Check if letters are sequential
                is_sequential = all(
                    ord(substring[k]) == ord(substring[0]) + k
                    for k in range(1, length)
                )
                if is_sequential:
                    return True

        return False

    def is_sn_suspicious(
        self,
        sn_val: Any,
        sns_to_exclude_due_to_overuse: Set[str] = None
    ) -> bool:
        """
        Check if a serial number is suspicious using Enhanced SN Validator.

        Uses the research-validated EnhancedSNValidator which provides:
        - Vendor-specific format validation (Dell, HP, Apple, Cisco, Lenovo, etc.)
        - Virtual environment detection (VMware, AWS, Hyper-V, Docker, etc.)
        - Low entropy pattern detection
        - SMBIOS placeholder detection
        - All original AssetValidator checks (placeholders, sequences, etc.)

        Args:
            sn_val: Serial number value to check
            sns_to_exclude_due_to_overuse: Set of known overused SNs

        Returns:
            True if suspicious, False if likely legitimate
        """
        if sns_to_exclude_due_to_overuse is None:
            sns_to_exclude_due_to_overuse = set()

        # Check for null/empty
        if pd.isna(sn_val):
            return True

        sn_str = str(sn_val).strip()
        if not sn_str:
            return True

        # Check overused SNs (specific to this instance's usage patterns)
        if sn_str in sns_to_exclude_due_to_overuse:
            return True

        # Delegate to Enhanced SN Validator for comprehensive validation
        # (99.9% rejection accuracy, 17.5% more suspicious SNs detected)
        return self.enhanced_sn_validator.is_sn_suspicious(sn_str)

    def evaluate_sn_quality(
        self,
        sn_val: Any,
        hostname_count: int = 0
    ) -> Tuple[float, bool]:
        """
        Evaluate the quality/legitimacy of a serial number.

        Args:
            sn_val: Serial number value
            hostname_count: Number of hostnames associated with this SN

        Returns:
            Tuple of (quality_score, is_migration_candidate)
            - quality_score: 0-1, where 1 is highest quality
            - is_migration_candidate: True if might be legitimate migration
        """
        if pd.isna(sn_val) or not str(sn_val).strip():
            return (0.0, False)

        sn_str = str(sn_val).strip()
        sn_lower = sn_str.lower()

        # Start with perfect score
        score = 1.0

        # Check against placeholders
        if sn_lower in self.common_sn_placeholders:
            return (0.0, False)

        # Length check
        if len(sn_lower) < 4:
            return (0.1, False)
        elif len(sn_lower) < 6:
            score *= 0.7

        # Repeating character check
        if len(sn_lower) > 1 and len(set(sn_lower)) == 1:
            return (0.1, False)

        # Simple sequences
        if sn_lower.isdigit() and sn_lower in self._simple_sequences:
            return (0.1, False)

        # Pattern checks (reduce score if found)
        if self._consecutive_char_pattern.search(sn_str):
            score *= 0.3

        if self.has_sequential_numbers(sn_str):
            score *= 0.4

        if self.has_sequential_letters(sn_str):
            score *= 0.4

        # Evaluate based on hostname associations
        if hostname_count > self.sn_overuse_threshold:
            # Definitely overused
            return (score * 0.1, False)
        elif hostname_count > self.sn_migration_threshold:
            # Might be migration if SN quality is good
            is_migration = score > 0.6
            return (score * 0.7, is_migration)
        else:
            # Good SN with appropriate hostname count
            return (score, True)

    def validate_component(self, component_type: str, value: Any) -> bool:
        """
        Validate a component based on its type.

        Args:
            component_type: Type of component ('ip', 'mac', 'sn', 'hostname')
            value: Value to validate

        Returns:
            True if valid, False otherwise
        """
        if pd.isna(value) or not str(value).strip():
            return False

        value_str = str(value).strip()

        if component_type == 'ip':
            return self.is_valid_ip(value_str)
        elif component_type == 'sn':
            return not self.is_sn_suspicious(value)
        elif component_type == 'hostname':
            return self.is_valid_hostname(value_str)
        elif component_type == 'mac':
            return self.is_valid_mac(value_str)

        return False

    def get_validation_stats(self, df: pd.DataFrame, sn_column: str = 'sn') -> dict:
        """
        Get validation statistics for a dataset.

        Args:
            df: DataFrame to analyze
            sn_column: Name of serial number column

        Returns:
            Dictionary with validation statistics
        """
        total = len(df)
        suspicious_sns = df[sn_column].apply(
            lambda x: self.is_sn_suspicious(x)
        ).sum()

        return {
            'total_records': total,
            'suspicious_sns': int(suspicious_sns),
            'suspicious_sn_percentage': (suspicious_sns / total * 100) if total > 0 else 0,
            'valid_sns': total - suspicious_sns,
        }

    # =========================================================================
    # COMPONENT VALUE VALIDATION
    # =========================================================================

    @staticmethod
    def is_valid_component_value(value: str, component_type: str = 'generic') -> bool:
        """
        Validate if a component value is suitable for asset identification.

        Filters out values with:
        - Empty strings or only whitespace
        - Backslashes \
        - Forward slashes / (often dates or paths)
        - Less than/greater than < >
        - Parentheses with numbers (e.g., "iphone (3)")
        - Internal whitespace (spaces in the middle)
        - Special control characters

        Args:
            value: The component value to validate
            component_type: Type of component ('hostname', 'sn', 'mac', 'ip', 'fqdn')

        Returns:
            True if value is suitable for identification, False otherwise
        """
        if not value or not isinstance(value, str):
            return False

        value = value.strip()

        # Empty after strip
        if not value:
            return False

        # Check for problematic characters
        problematic_chars = ['\\', '/', '<', '>', '|']
        if any(char in value for char in problematic_chars):
            return False

        # Check for internal whitespace (spaces in the middle)
        if ' ' in value:
            return False

        # Check for parentheses with numbers pattern like "(3)" or "(49)"
        if re.search(r'\(\d+\)$', value):
            return False

        # Check for common placeholder patterns
        placeholder_patterns = [
            r'^unknown$',
            r'^default$',
            r'^none$',
            r'^n/?a$',
            r'^null$',
        ]

        value_lower = value.lower()
        for pattern in placeholder_patterns:
            if re.match(pattern, value_lower):
                return False

        # Component-specific validation
        if component_type == 'hostname' or component_type == 'fqdn':
            # Hostname/FQDN should not contain dates (pattern like "5/8/2025" already caught by /)
            # Should not be too short
            if len(value) < 3:
                return False

        elif component_type == 'sn':
            # Serial numbers should be meaningful length
            if len(value) < 4:
                return False

        elif component_type == 'mac':
            # MAC addresses should be reasonable length
            if len(value) < 12:
                return False

        return True

    # =========================================================================
    # ASSET FIELD GENERATION
    # =========================================================================

    def create_asset_field(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Create asset field with validated component identifiers.

        This is a critical preprocessing step that:
        1. Analyzes the dataset to identify problematic identifiers
        2. Builds exclusion sets for each identifier type
        3. Creates the "asset" field by concatenating valid identifiers
        4. The asset field is used by AssetIdentifier for ID assignment

        Args:
            df: DataFrame with raw asset data

        Returns:
            DataFrame with 'asset' column added containing pipe-separated identifiers
        """
        start_time = time.time()
        logger.info(f"Creating asset field for {len(df)} records...")

        # Ensure required columns exist
        required_cols = ['hostname', 'ip', 'mac', 'data_source', 'fqdn', 'sn']
        for col in required_cols:
            if col not in df.columns:
                df[col] = pd.NA

        # Replace empty strings with NA
        df.replace("", pd.NA, inplace=True)

        # Expand array-valued identifier columns into individual rows
        df = self._explode_array_identifiers(df)

        # Normalize Intune mobile hostnames by stripping timestamp suffixes
        if 'hostname' in df.columns:
            df['hostname'] = df['hostname'].apply(
                lambda h: self.normalize_intune_hostname(h) if pd.notna(h) else h
            )

        # Build exclusion sets through dataset analysis
        exclusion_sets = self._build_exclusion_sets(df)

        # Store for use by _determine_exclusion_reason (#287)
        self.last_exclusion_sets = exclusion_sets

        # Create asset field for each row
        def create_asset_for_row(row: pd.Series) -> str:
            """
            Create asset field for a single row.

            All components are normalized to lowercase for case-insensitive matching.
            """
            fields = []

            # Get manufacturer for hostname cross-reference (#26 Issue 4)
            manufacturer_val = row.get("manufacturer")
            manufacturer_lower = ""
            if pd.notna(manufacturer_val):
                manufacturer_lower = str(manufacturer_val).strip().lower()

            # Hostname - normalize to lowercase with validation
            hostname_val = row.get("hostname")
            hostname_str = ""
            if pd.notna(hostname_val):
                hostname_str = str(hostname_val).strip().lower()  # Normalize to lowercase
                # Skip hostname if it matches manufacturer name (#26 Issue 4)
                # Uses normalized comparison to catch variants like
                # "Samsung" vs "Samsung Electronics Co."
                is_manufacturer_hostname = False
                if manufacturer_lower:
                    mfr_normalized = normalize_manufacturer(manufacturer_lower)
                    hostname_normalized = normalize_manufacturer(hostname_str)
                    if mfr_normalized and hostname_normalized and mfr_normalized == hostname_normalized:
                        is_manufacturer_hostname = True
                # Also reject if hostname IS a known manufacturer alias globally
                # (even when no manufacturer field on this row)
                if not is_manufacturer_hostname and is_known_manufacturer(hostname_str):
                    is_manufacturer_hostname = True
                # Check if hostname is a known device model name
                is_model_hostname = (
                    self.device_kb is not None
                    and self.device_kb.is_known_model(hostname_str)
                )
                if (hostname_str and
                    not is_manufacturer_hostname and
                    not is_model_hostname and
                    self.is_valid_component_value(hostname_str, 'hostname') and
                    self.is_valid_hostname(hostname_val) and  # Quality validation
                    hostname_str not in exclusion_sets['hostnames']):
                    fields.append(hostname_str)

            # FQDN - normalize to lowercase with validation
            fqdn_val = row.get("fqdn")
            fqdn_str = ""
            if pd.notna(fqdn_val):
                fqdn_str = str(fqdn_val).strip().lower()  # Normalize to lowercase
                # Reject FQDN if its short name is a generic hostname
                # (e.g., iphone.primprod.net -> "iphone" is generic)
                # or contains a date pattern (e.g., device-3-15-2025.corp.com)
                fqdn_short_check = fqdn_str.split('.')[0] if fqdn_str else ''
                if fqdn_short_check in self.generic_hostnames:
                    fqdn_str = ''  # Suppress FQDN and its bridge
                elif self._hostname_date_pattern.search(fqdn_short_check):
                    fqdn_str = ''  # Suppress FQDN with date in short name
                elif (fqdn_str and
                    self.is_valid_component_value(fqdn_str, 'fqdn') and
                    fqdn_str != hostname_str and
                    fqdn_str not in exclusion_sets['fqdns']):
                    fields.append(fqdn_str)

            # Bridge: extract short hostname from FQDN and add as component
            # This allows merging records that report only FQDN with records
            # that report only the short hostname
            if fqdn_str:
                fqdn_short = fqdn_str.split('.')[0]
                if (fqdn_short and
                    fqdn_short != hostname_str and  # Not already added as hostname
                    fqdn_short not in exclusion_sets['hostnames'] and
                    self.is_valid_component_value(fqdn_short, 'hostname') and
                    self.is_valid_hostname(fqdn_short)):
                    fields.append(fqdn_short)

            # Serial Number - normalize to lowercase with validation
            sn_val = row.get("sn")
            if pd.notna(sn_val):
                sn_str = str(sn_val).strip().lower()
                if (self.is_valid_component_value(sn_str, 'sn') and
                    not self.is_sn_suspicious(sn_val, exclusion_sets['sns'])):
                    fields.append(f"sn:{sn_str}")

            # IP Address - keep as-is with validation
            ip_val = row.get("ip")
            if pd.notna(ip_val):
                ip_str = str(ip_val).strip()
                if (ip_str and
                    self.is_valid_component_value(ip_str, 'ip') and
                    self.is_valid_ip(ip_str) and  # Quality validation
                    ip_str not in exclusion_sets['ips']):
                    fields.append(ip_str)

            # MAC Address - normalize to lowercase with validation
            mac_val = row.get("mac")
            if pd.notna(mac_val):
                mac_str = str(mac_val).strip().lower()  # Normalize to lowercase
                if (mac_str and
                    self.is_valid_component_value(mac_str, 'mac') and
                    self.is_valid_mac(mac_val) and  # Quality validation
                    mac_str not in exclusion_sets['macs']):
                    fields.append(mac_str)

            # Remove duplicates while preserving order; drop denylisted components
            # (reused hostname / shared MAC / ambiguous serial marked untrusted).
            # `in` reaches DenylistMatcher, which resolves an exact entry in one
            # set lookup and a pattern in a handful more — this runs for every
            # component of every row (~440k probes per run), so it must not scale
            # with the number of entries.
            seen = set()
            final_fields = []
            for item in fields:
                if item in seen or item in self.denylist_components:
                    continue
                seen.add(item)
                final_fields.append(item)

            return "|".join(final_fields)

        # Apply to all rows
        df["asset"] = df.apply(create_asset_for_row, axis=1)

        elapsed = time.time() - start_time
        logger.info(
            f"Asset field creation completed in {elapsed:.2f}s "
            f"({len(df)/elapsed:.0f} records/sec)"
        )

        # Log exclusion statistics
        logger.info(
            f"Exclusion stats: "
            f"{len(exclusion_sets['ips'])} IPs, "
            f"{len(exclusion_sets['macs'])} MACs, "
            f"{len(exclusion_sets['sns'])} SNs, "
            f"{len(exclusion_sets['hostnames'])} hostnames, "
            f"{len(exclusion_sets['fqdns'])} FQDNs"
        )

        return df

    def _explode_array_identifiers(self, df):
        """Expand array-valued identifier columns into individual rows.

        After raw_asset_data migration to text[], identifier columns may contain
        Python lists. Explode them so each value gets its own row for correct
        normalization, exclusion set building, and UnionFind grouping.
        """
        identifier_cols = ['hostname', 'fqdn', 'ip', 'mac', 'sn']
        exploded = False
        for col in identifier_cols:
            if col in df.columns:
                has_lists = False
                def _clean(x):
                    nonlocal has_lists
                    if isinstance(x, list):
                        # Flatten nested arrays (e.g. [['hostname']] from
                        # connectors inserting arrays into text[] columns)
                        flat = []
                        for item in x:
                            if isinstance(item, list):
                                flat.extend(item)
                            else:
                                flat.append(item)
                        if not flat:
                            return None
                        has_lists = True
                        return flat
                    return x
                df[col] = df[col].apply(_clean)
                if has_lists:
                    df = df.explode(col)
                    exploded = True
        if exploded:
            # Sequential per-column explode produces a cross-product when
            # multiple identifier columns are arrays on the same row.
            # Deduplicate to reduce noise from exact duplicate rows.
            #
            # The cross-product to collapse always lives WITHIN a single source
            # record, where the source-identifying columns are constant. Key the
            # dedup on those too, so rows that share an identical
            # (hostname, fqdn, ip, mac, sn) tuple but come from DIFFERENT
            # integrations are NOT collapsed — otherwise the later-ordered row is
            # dropped and that integration's data_source is silently lost from the
            # asset (survival depended on raw_asset_data.id order). See
            # test_explode_dedup_preserves_source.
            id_cols_present = [c for c in identifier_cols if c in df.columns]
            dedup_cols = id_cols_present + [
                c for c in ('integration_id', 'data_source') if c in df.columns
            ]
            shadow = df[dedup_cols].apply(
                lambda col: col.map(lambda x: str(x) if isinstance(x, list) else x)
            )
            df = df[~shadow.duplicated()].reset_index(drop=True)
        return df

    def _build_exclusion_sets(self, df: pd.DataFrame) -> Dict[str, set]:
        """
        Build exclusion sets by analyzing the dataset for problematic identifiers.

        Components are normalized to lowercase for case-insensitive analysis.

        Args:
            df: DataFrame to analyze

        Returns:
            Dictionary with exclusion sets for each identifier type (all lowercase)
        """
        logger.info("Building exclusion sets from dataset analysis...")

        # Create a normalized copy for analysis
        df_normalized = df.copy()

        # Normalize relevant columns to lowercase
        for col in ['hostname', 'fqdn', 'mac', 'sn']:
            if col in df_normalized.columns:
                df_normalized[col] = df_normalized[col].astype(str).str.strip().str.lower()

        exclusion_sets = {
            'ips': set(),
            'macs': set(),
            'sns': set(),
            'hostnames': set(),
            'fqdns': set()
        }

        # 1. IPs with multiple hostnames
        df_ip_check = df_normalized.dropna(subset=['hostname', 'ip'])
        if not df_ip_check.empty:
            ip_host_counts = df_ip_check.groupby("ip")["hostname"].nunique()
            exclusion_sets['ips'] = set(
                ip_host_counts[ip_host_counts > 1].index
            )

        # 2. MACs with multiple hostnames (same data source)
        # MACs should be unique to a single hostname. If a MAC appears with multiple
        # hostnames, it's either shared/reassigned or indicates incorrect data.
        # Exclude these MACs from asset identification to prevent incorrect merges.
        # Must match AssetIdentifier.MAC_HOSTNAME_THRESHOLD
        MAC_HOSTNAME_THRESHOLD = 2

        df_mac_ds = df_normalized.dropna(subset=['hostname', 'mac', 'data_source'])
        if not df_mac_ds.empty:
            mac_host_counts_ds = df_mac_ds.groupby(
                ['mac', 'data_source']
            )['hostname'].nunique()
            macs_same_source_exclude = set(
                mac_host_counts_ds[mac_host_counts_ds > MAC_HOSTNAME_THRESHOLD].reset_index()['mac']
            )
        else:
            macs_same_source_exclude = set()

        # 3. MACs with multiple hostnames (global)
        # IMPROVEMENT: Smart exclusion based on context
        df_mac_check = df_normalized.dropna(subset=['hostname', 'mac'])
        if not df_mac_check.empty:
            mac_host_counts = df_mac_check.groupby("mac")["hostname"].nunique()
            # Start with MACs that have excessive hostname changes
            macs_global_exclude = set(
                mac_host_counts[mac_host_counts > MAC_HOSTNAME_THRESHOLD].index
            )

            # CRITICAL: Exclude MACs that appear with multiple different SNs
            # Different SNs = different physical devices, so MAC must be shared/reassigned
            df_mac_sn = df_normalized.dropna(subset=['mac', 'sn'])
            if not df_mac_sn.empty:
                mac_sn_counts = df_mac_sn.groupby("mac")["sn"].nunique()
                # MACs with multiple different SNs are always problematic
                macs_with_multiple_sns = set(
                    mac_sn_counts[mac_sn_counts > 1].index
                )
                macs_global_exclude = macs_global_exclude | macs_with_multiple_sns

                if len(macs_with_multiple_sns) > 0:
                    logger.warning(f"Excluding {len(macs_with_multiple_sns)} MACs that appear with multiple different SNs (shared/reassigned MACs)")

            # Cross-row MAC+SN exclusion: catch shared MACs (e.g., docking stations)
            # where MAC and SN never appear on the same row but the MAC bridges
            # hostnames that have different serial numbers from other sources.
            # Logic: MAC → {hostname_A, hostname_B}, hostname_A → SN_1,
            #        hostname_B → SN_2, SN_1 ≠ SN_2 → MAC is shared → exclude
            macs_with_multi_host = set(
                mac_host_counts[mac_host_counts > 1].index
            ) - _NORMALIZED_NULLS
            candidates = macs_with_multi_host - macs_global_exclude
            if candidates:
                _df_hn_sn = df_normalized[
                    df_normalized['hostname'].notna()
                    & df_normalized['sn'].notna()
                    & ~df_normalized['sn'].isin(_NORMALIZED_NULLS)
                ] if 'sn' in df_normalized.columns else pd.DataFrame()
                hostname_sn_map = (
                    _df_hn_sn.groupby('hostname')['sn'].apply(set).to_dict()
                    if not _df_hn_sn.empty else {}
                )
                macs_cross_row_exclude = set()
                mac_to_hosts = (
                    df_mac_check[df_mac_check['mac'].isin(candidates)]
                    .groupby('mac')['hostname'].apply(set).to_dict()
                )
                for mac, hostnames in mac_to_hosts.items():
                    sns_via_hostnames = set()
                    for hn in hostnames:
                        sns_via_hostnames.update(hostname_sn_map.get(hn, set()))
                    if len(sns_via_hostnames) > 1:
                        macs_cross_row_exclude.add(mac)
                macs_global_exclude = macs_global_exclude | macs_cross_row_exclude
                if macs_cross_row_exclude:
                    logger.warning(
                        f"Excluding {len(macs_cross_row_exclude)} MACs via cross-row "
                        f"analysis (bridge hostnames with different SNs)"
                    )
        else:
            macs_global_exclude = set()

        exclusion_sets['macs'] = macs_same_source_exclude | macs_global_exclude

        logger.info(f"MAC exclusion: {len(exclusion_sets['macs'])} MACs excluded (threshold: >{MAC_HOSTNAME_THRESHOLD} hostnames)")

        # 4. Overused SNs with quality evaluation
        df_sn_check = df_normalized.dropna(subset=['hostname', 'sn'])
        if not df_sn_check.empty:
            # Filter out placeholder SNs
            sn_lower = df_sn_check['sn'].astype(str).str.lower()
            valid_mask = (
                ~sn_lower.isin(self.common_sn_placeholders) &
                (sn_lower.str.len() >= 4)
            )
            df_sn_filtered = df_sn_check[valid_mask]

            if not df_sn_filtered.empty:
                sn_host_counts = df_sn_filtered.groupby("sn")["hostname"].nunique()

                # Use quality assessment to determine exclusions
                for sn, host_count in sn_host_counts.items():
                    quality_score, is_migration = self.evaluate_sn_quality(
                        sn, host_count
                    )
                    # Exclude low quality SNs or those with excessive hosts
                    if quality_score < 0.5 or host_count > self.sn_overuse_threshold:
                        exclusion_sets['sns'].add(sn)

        # 5. Hostnames/FQDNs with multiple valid SNs
        if not df_sn_check.empty:
            # Get valid SNs (not suspicious)
            valid_sns = [
                sn for sn in df_sn_check['sn'].unique()
                if not self.is_sn_suspicious(sn, exclusion_sets['sns'])
            ]

            if valid_sns:
                # Hostnames with multiple valid SNs
                df_valid_sn_hn = df_sn_check[df_sn_check['sn'].isin(valid_sns)]
                if not df_valid_sn_hn.empty:
                    hostname_sn_counts = df_valid_sn_hn.groupby(
                        "hostname"
                    )["sn"].nunique()

                    # BUGFIX (#8): Exclude hostnames with multiple SNs, BUT skip generic hostnames
                    # Generic hostnames (iPhone, iPad, Android, etc.) are EXPECTED to have multiple SNs
                    # because each device is different. These should be filtered by is_valid_hostname()
                    # instead, which already checks against the generic_hostnames list.
                    # Only add non-generic hostnames with multiple SNs to the exclusion set.
                    hostnames_with_multiple_sns = set(
                        hostname_sn_counts[hostname_sn_counts > 1].index
                    )

                    # Filter out generic hostnames from the exclusion set
                    exclusion_sets['hostnames'] = {
                        hn for hn in hostnames_with_multiple_sns
                        if hn not in self.generic_hostnames
                    }

                    # Log how many generic hostnames were skipped
                    generic_skipped = hostnames_with_multiple_sns & self.generic_hostnames
                    if generic_skipped:
                        logger.info(
                            f"Skipped {len(generic_skipped)} generic hostnames from exclusion set "
                            f"(will be filtered by is_valid_hostname() instead): {sorted(generic_skipped)}"
                        )

                # FQDNs with multiple valid SNs
                df_fqdn_check = df_normalized.dropna(subset=['fqdn', 'sn'])
                if not df_fqdn_check.empty:
                    df_valid_sn_fqdn = df_fqdn_check[
                        df_fqdn_check['sn'].isin(valid_sns)
                    ]
                    if not df_valid_sn_fqdn.empty:
                        fqdn_sn_counts = df_valid_sn_fqdn.groupby(
                            "fqdn"
                        )["sn"].nunique()
                        exclusion_sets['fqdns'] = set(
                            fqdn_sn_counts[fqdn_sn_counts > 1].index
                        )

        return exclusion_sets
