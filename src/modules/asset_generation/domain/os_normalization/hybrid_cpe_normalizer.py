#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Hybrid CPE-Compliant OS Normalizer

Multi-tier architecture for high-performance OS normalization with CPE compliance:
- Tier 1: Exact Match Cache (<0.1ms, 99% coverage)
- Tier 2: Pattern-Based CPE Generation (<1ms)
- Tier 3: CPE Validation (<0.5ms)
- Tier 4: Fallback to Rule-Based Normalizer

Outputs CPE 2.3 format for industry standard compliance.
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, asdict
import re

logger = logging.getLogger(__name__)

# Import the pattern-based generator components
from src.modules.asset_generation.domain.os_normalization.os_knowledge_base import (
    CPE_VENDOR_MAP,
    CPE_PRODUCT_MAP,
    OS_FAMILY_PATTERNS,
    VERSION_PATTERNS
)


@dataclass
class OSNormalizationResult:
    """Result from OS normalization."""
    # CPE components
    cpe: Optional[str]
    vendor: Optional[str]
    product: Optional[str]
    version: Optional[str]
    edition: Optional[str]
    os_family: Optional[str]

    # Legacy normalized format (for compatibility)
    os_normalized: Optional[str]

    # Metadata
    confidence: float
    method: str  # exact_match, pattern_based, validated, fallback
    latency_ms: float
    tier: int  # 1-4


class HybridCPENormalizer:
    """
    Hybrid OS normalizer with CPE compliance.

    Implements 4-tier architecture for optimal performance and accuracy.
    """

    _DARWIN_TO_MACOS = {
        20: '11',   # Big Sur
        21: '12',   # Monterey
        22: '13',   # Ventura
        23: '14',   # Sonoma
        24: '15',   # Sequoia
        25: '16',   # Tahoe
    }

    _PRODUCT_DISPLAY_NAMES = {
        'enterprise_linux': 'red hat enterprise linux',
        'mac_os_x': 'macos',
    }

    def __init__(self, cache_dir: Optional[Path] = None):
        """
        Initialize the hybrid normalizer.

        Args:
            cache_dir: Directory containing cache files (defaults to this file's directory)
        """
        if cache_dir is None:
            cache_dir = Path(__file__).parent

        self.cache_dir = cache_dir

        # Load resources
        self._load_exact_match_cache()
        self._load_cpe_dictionary()

        # Statistics
        self.stats = {
            'tier_1_hits': 0,  # Exact match cache
            'tier_2_hits': 0,  # Pattern-based
            'tier_3_hits': 0,  # Validated
            'tier_4_hits': 0,  # Fallback
            'rejected_hits': 0,  # Garbage rejected before tier processing
            'total_requests': 0,
            'avg_latency_ms': 0.0
        }

        # Family to product mapping (from generator)
        self.family_to_product = {
            'windows_desktop': 'windows_10',
            'windows_server': 'windows_server_2019',
            'windows_embedded': 'windows_ce',
            'windows_nt_legacy': 'windows_nt',
            'windows_2000': 'windows_2000',
            'windows_2003_server': 'windows_server_2003',
            'windows_vista': 'windows_vista',
            'windows_ancient': 'windows_3',
            'linux_rhel': 'enterprise_linux',
            'linux_ubuntu': 'ubuntu_linux',
            'linux_centos': 'centos',
            'linux_debian': 'debian_linux',
            'linux_amazon': 'amazon_linux',
            'linux_oracle': 'linux',
            'linux_rocky': 'rocky_linux',
            'linux_almalinux': 'almalinux',
            'linux_suse': 'suse_linux_enterprise_server',
            'linux_fedora': 'fedora',
            'linux_arch': 'arch_linux',
            'linux_mint': 'linux_mint',
            'linux_generic': 'linux',  # Generic Linux
            'macos': 'mac_os_x',
            'mobile_ios': 'apple ios',  # Apple iOS (not iphone_os)
            'mobile_android': 'android',
            'mobile_windows': 'windows_mobile',
            'mobile_symbian': 'symbian',
            'mobile_blackberry': 'blackberry',
            'mobile_harmonyos': 'harmonyos',
            'mobile_windows_holographic': 'windows_holographic',
            'unix_solaris': 'solaris',
            'unix_aix': 'aix',
            'unix_hpux': 'hp-ux',
            'bsd_freebsd': 'freebsd',
            'bsd_openbsd': 'openbsd',
            'bsd_netbsd': 'netbsd',
            'bsd': 'freebsd',
            'network_cisco_ios': 'cisco_ios',  # Cisco IOS (distinct from Apple iOS)
            'network_cisco_iosxe': 'ios_xe',
            'network_cisco_iosxr': 'ios_xr',
            'network_cisco_nxos': 'nx-os',
            'network_cisco_asa': 'adaptive_security_appliance_software',
            'network_juniper_junos': 'junos',
            'network_arista_eos': 'eos',
            'network_paloalto_panos': 'pan-os',
            'network_fortinet_fortios': 'fortios',
            'network_checkpoint_gaia': 'gaia_os',
            'network_citrix_netscaler': 'netscaler',
            'embedded_vxworks': 'vxworks',
            'embedded_qnx': 'qnx',
            'embedded_freertos': 'freertos',
            'embedded_zephyr': 'zephyr',
            'embedded_threadx': 'threadx',
            'embedded_tinyos': 'tinyos',
            'embedded_contiki': 'contiki',
            'embedded_riot': 'riot',
            'virtualization_vmware': 'esxi',
            'embedded_generic': 'embedded_os',
            'firmware': 'firmware',
            'windows_generic': 'windows',
            'unknown_os': 'unknown',
            'management_ilom': 'oracle_ilom',
            'mobile_tizen': 'tizen',
            'embedded_axis': 'axis_os',
            'thinclient_thinos_extra': 'thinos',
            'collaboration_os': 'collaboration_os',
            'unknown_ngos': 'unknown',
        }

    def _load_exact_match_cache(self):
        """Load exact match cache for Tier 1 lookups."""
        cache_file = self.cache_dir / 'exact_match_cache_compact.json'

        if not cache_file.exists():
            logger.warning(f"Exact match cache not found: {cache_file}")
            self.exact_match_cache = {}
            return

        with open(cache_file, 'r') as f:
            self.exact_match_cache = json.load(f)

        logger.info(f"Loaded exact match cache: {len(self.exact_match_cache)} entries")

    def _load_cpe_dictionary(self):
        """Load CPE dictionary for Tier 3 validation."""
        dict_file = self.cache_dir / 'cpe_vendor_product_index.json'

        if not dict_file.exists():
            logger.warning(f"CPE dictionary not found: {dict_file}")
            self.cpe_dictionary = {}
            return

        with open(dict_file, 'r') as f:
            self.cpe_dictionary = json.load(f)

        logger.info(f"Loaded CPE dictionary: {len(self.cpe_dictionary)} vendor-product pairs")

    # =========================================================================
    # Tier 1: Exact Match Cache
    # =========================================================================

    def _tier1_exact_match(self, os_string: str) -> Optional[OSNormalizationResult]:
        """Tier 1: Exact match cache lookup."""
        if os_string in self.exact_match_cache:
            entry = self.exact_match_cache[os_string]
            os_family, _ = self._detect_os_family(os_string)
            return OSNormalizationResult(
                cpe=entry['cpe'],
                vendor=entry['vendor'],
                product=entry['product'],
                version=entry['version'],
                edition=entry['edition'],
                os_family=os_family,
                os_normalized=self._cpe_to_legacy_format(entry),
                confidence=entry['confidence'],
                method='exact_match',
                latency_ms=0.0,
                tier=1
            )
        return None

    # =========================================================================
    # Tier 2: Pattern-Based CPE Generation
    # =========================================================================

    def _detect_os_family(self, os_string: str) -> Tuple[Optional[str], Optional[str]]:
        """Detect OS family from string."""
        text = os_string.lower()

        for family, config in OS_FAMILY_PATTERNS.items():
            # Check exclude patterns first
            if any(re.search(pattern, text) for pattern in config.get('exclude', [])):
                continue

            # Check match patterns
            if any(re.search(pattern, text) for pattern in config['patterns']):
                return family, config.get('category')

        return None, None

    def _get_windows_version_from_build(self, os_string: str) -> Optional[str]:
        """Map Windows build numbers to versions."""
        text = os_string.lower()

        # Extract build number (format: 10.0.XXXXX)
        # Must have "10.0." prefix to be a valid build number
        build_match = re.search(r'10\.0\.(\d{5})', text)
        if not build_match:
            return None

        build = int(build_match.group(1))
        is_server = 'server' in text

        # Windows Server builds first (when the OS string says 'server'),
        # most-specific (highest build) first, so they are not swallowed by the
        # generic Windows 10/11 desktop ranges below. Build numbers: Server
        # 2025=26100, 2022=20348, 2019=17763, 2016=14393 -- all share the
        # 10.0.* kernel line, which is why ordering matters. (#681 A9a)
        if is_server:
            if build >= 26100:
                return 'windows_server_2025'
            if build >= 20348:
                return 'windows_server_2022'
            if build >= 17763:
                return 'windows_server_2019'
            if build >= 14393:
                return 'windows_server_2016'
            # Older 10.0.* server builds fall through to the desktop mapping.

        # Desktop Windows
        if build >= 22000:
            return 'windows_11'
        if build >= 10240:
            return 'windows_10'

        return None

    def _get_windows_nt_version(self, os_string: str) -> Optional[str]:
        """Map Windows NT version numbers to product names."""
        text = os_string.lower()

        # Match NT version format: 6.0, 6.1, 6.2, 6.3, 10.0
        # Allow version to be followed by dot or space (not just word boundary)
        nt_match = re.search(r'\b(6\.[0-3]|10\.0)(?:\.|$|\s)', text)
        if not nt_match:
            return None

        nt_version = nt_match.group(1)

        # Check if it's a server version
        is_server = 'server' in text

        # Map NT versions
        if nt_version == '10.0':
            # 10.0 could be Windows 10 or Server 2016/2019
            # Use build number if available
            build_version = self._get_windows_version_from_build(os_string)
            if build_version:
                return build_version
            return 'windows_server_2016' if is_server else 'windows_10'
        elif nt_version == '6.3':
            return 'windows_server_2012' if is_server else 'windows_8.1'
        elif nt_version == '6.2':
            return 'windows_server_2012' if is_server else 'windows_8'
        elif nt_version == '6.1':
            return 'windows_server_2008' if is_server else 'windows_7'
        elif nt_version == '6.0':
            return 'windows_server_2008' if is_server else 'windows_vista'

        return None

    def _refine_product_from_version(self, os_family: str, os_string: str, base_product: str) -> str:
        """Refine CPE product based on detected version."""
        text = os_string.lower()

        # Windows desktop versions
        if os_family == 'windows_desktop':
            # First try build number detection
            build_version = self._get_windows_version_from_build(os_string)
            if build_version and build_version.startswith('windows_') and not build_version.startswith('windows_server_'):
                return build_version

            # Try NT version detection
            nt_version = self._get_windows_nt_version(os_string)
            if nt_version and nt_version.startswith('windows_') and not nt_version.startswith('windows_server_'):
                return nt_version

            # Fallback to string matching
            if '11' in text:
                return 'windows_11'
            elif '10' in text:
                return 'windows_10'
            elif '8.1' in text:
                return 'windows_8.1'
            elif '8' in text:
                return 'windows_8'
            elif '7' in text:
                return 'windows_7'
            elif 'vista' in text:
                return 'windows_vista'

        # Windows server versions (check in descending order to avoid substring matches)
        elif os_family == 'windows_server':
            # First try build number detection
            build_version = self._get_windows_version_from_build(os_string)
            if build_version and build_version.startswith('windows_server_'):
                return build_version

            # Try NT version detection
            nt_version = self._get_windows_nt_version(os_string)
            if nt_version and nt_version.startswith('windows_server_'):
                return nt_version

            # Fallback to string matching (check in descending order)
            if '2025' in text:
                return 'windows_server_2025'
            elif '2022' in text:
                return 'windows_server_2022'
            elif '2019' in text:
                return 'windows_server_2019'
            elif '2016' in text:
                return 'windows_server_2016'
            elif '2012' in text:
                return 'windows_server_2012'
            elif '2008' in text:
                return 'windows_server_2008'
            elif '2003' in text:
                return 'windows_server_2003'

        return base_product

    def _extract_version(self, os_string: str, os_family: Optional[str] = None) -> Optional[str]:
        """Extract version from OS string."""
        text = os_string.lower()

        # Darwin kernel version -> macOS version mapping
        if os_family == 'macos':
            darwin_match = re.search(r'darwin\s+(\d+)', text)
            if darwin_match:
                darwin_major = int(darwin_match.group(1))
                macos_version = self._DARWIN_TO_MACOS.get(darwin_major)
                if macos_version:
                    return macos_version
                # Unknown Darwin major — don't let kernel number leak as macOS version
                return None

        # Amazon Linux: truncate build dates to YYYY.N or N.N
        if os_family == 'linux_amazon':
            amzn_match = re.search(r'(\d{4}\.\d+)\.\d{8,}', text)
            if amzn_match:
                return amzn_match.group(1)
            amzn_match2 = re.search(r'(\d+\.\d+)\.\d{8,}', text)
            if amzn_match2:
                return amzn_match2.group(1)

        # Ubuntu: only accept YY.MM release versions (e.g. 22.04, 20.04).
        # Anything else (like 3.13.0-24-generic) is a kernel version.
        # Matches "20.04" in "20.04.5 LTS" but not "3.13" in "3.13.0-24".
        # Kernel versions have .Z-build suffix; Ubuntu point releases have .N alone.
        if os_family == 'linux_ubuntu':
            ubuntu_match = re.search(r'\b(\d{1,2}\.\d{2})(?:\.\d+)?(?!\.\d)(?!-)\b', text)
            return ubuntu_match.group(1) if ubuntu_match else None

        # Generic Linux: no distro identified, so any version found is almost
        # certainly a kernel version (2.6, 5.4.0, etc.), not a product version.
        if os_family == 'linux_generic':
            return None

        # Android: strip trailing ".0" from integer versions (12.0 -> 12)
        # and reject implausible versions (< 4 is pre-2013)
        if os_family == 'mobile_android':
            match = re.search(r'\b(\d+(?:\.\d+)*)\b', text)
            if not match:
                return None
            version_str = match.group(1)
            major = int(version_str.split('.')[0])
            if major < 4:
                return None
            if re.fullmatch(r'\d+\.0', version_str):
                return version_str.split('.')[0]
            return version_str

        # Citrix NetScaler carries its version as an "NS<maj>.<min>" token
        # (e.g. "netscaler ns13.1"); the generic extractor would otherwise grab
        # the trailing "1" (no word boundary before "13" in "ns13.1"). (#681 D2)
        if os_family == 'network_citrix_netscaler':
            m = re.search(r'ns\s*(\d+\.\d+)', text) or re.search(r'\b(\d+\.\d+)\b', text)
            return m.group(1) if m else None

        patterns = [
            r'\b(\d+\.\d+\.\d+\.\d+)\b',  # 10.0.19045.1234
            r'\b(\d+\.\d+\.\d+)\b',        # 16.1.1
            r'\b(\d+\.\d+)\b',              # 8.8
            r'\b(\d+)\b',                   # 11
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)

        return None

    def _extract_edition(self, os_string: str) -> Optional[str]:
        """Extract edition from OS string."""
        text = os_string.lower()

        editions = {
            'enterprise': 'enterprise',
            'professional': 'professional',
            'pro': 'professional',
            'home': 'home',
            'education': 'education',
            'ltsc': 'ltsc',
            'ltsb': 'ltsb',
            'standard': 'standard',
            'datacenter': 'datacenter',
        }

        for keyword, edition in editions.items():
            if keyword in text:
                return edition

        return None

    def _normalize_vendor(self, vendor: Optional[str]) -> str:
        """Normalize vendor name to CPE format."""
        if not vendor:
            return '*'

        vendor_lower = vendor.lower().strip()
        return CPE_VENDOR_MAP.get(vendor_lower, re.sub(r'[^a-z0-9_]', '_', vendor_lower))

    def _tier2_pattern_based(
        self,
        os_string: str,
        manufacturer: Optional[str] = None
    ) -> Optional[OSNormalizationResult]:
        """Tier 2: Pattern-based CPE generation."""
        # Detect OS family
        os_family, category = self._detect_os_family(os_string)
        if not os_family:
            return None

        # Map to CPE product
        base_product = self.family_to_product.get(os_family)
        if not base_product:
            return None

        # Refine product
        product = self._refine_product_from_version(os_family, os_string, base_product)

        # Determine vendor
        vendor = self._normalize_vendor(manufacturer) if manufacturer else '*'
        if vendor == '*':
            # Infer from OS family
            vendor_inference = {
                'windows_desktop': 'microsoft',
                'windows_server': 'microsoft',
                'windows_embedded': 'microsoft',
                'windows_nt_legacy': 'microsoft',
                'windows_2000': 'microsoft',
                'windows_2003_server': 'microsoft',
                'windows_ancient': 'microsoft',
                'linux_rhel': 'redhat',
                'linux_ubuntu': 'canonical',
                'linux_centos': 'centos',
                'linux_debian': 'debian',
                'linux_amazon': 'amazon',
                'linux_oracle': 'oracle',
                'linux_rocky': 'rocky',
                'linux_almalinux': 'almalinux',
                'linux_suse': 'suse',
                'linux_fedora': 'fedora',
                'linux_arch': 'arch',
                'linux_mint': 'linuxmint',
                'linux_generic': 'linux',  # Generic Linux - unknown vendor
                'macos': 'apple',
                'mobile_ios': 'apple',
                'mobile_android': 'google',
                'mobile_windows': 'microsoft',
                'mobile_symbian': 'symbian',
                'mobile_blackberry': 'blackberry',
                'mobile_harmonyos': 'huawei',
                'mobile_windows_holographic': 'microsoft',
                'unix_solaris': 'oracle',
                'unix_aix': 'ibm',
                'unix_hpux': 'hp',
                'bsd_freebsd': 'freebsd',
                'bsd_openbsd': 'openbsd',
                'bsd_netbsd': 'netbsd',
                'bsd': 'freebsd',
                'network_cisco_ios': 'cisco',
                'network_cisco_iosxe': 'cisco',
                'network_cisco_iosxr': 'cisco',
                'network_cisco_nxos': 'cisco',
                'network_cisco_asa': 'cisco',
                'network_juniper_junos': 'juniper',
                'network_arista_eos': 'arista',
                'network_paloalto_panos': 'palo_alto_networks',
                'network_fortinet_fortios': 'fortinet',
                'network_checkpoint_gaia': 'checkpoint',
                'network_citrix_netscaler': 'citrix',
                'embedded_vxworks': 'windriver',
                'embedded_qnx': 'blackberry',
                'embedded_freertos': 'freertos',
                'embedded_zephyr': 'zephyr',
                'embedded_threadx': 'microsoft',
                'embedded_tinyos': 'tinyos',
                'embedded_contiki': 'contiki',
                'embedded_riot': 'riot',
                'virtualization_vmware': 'vmware',
                'windows_generic': 'microsoft',
                'unknown_os': '*',
                'management_ilom': 'oracle',
                'mobile_tizen': 'samsung',
                'embedded_axis': 'axis',
                'thinclient_thinos_extra': 'dell',
                'collaboration_os': 'cisco',
                'unknown_ngos': '*',
            }
            vendor = vendor_inference.get(os_family, '*')

        # Extract version and edition
        version = self._extract_version(os_string, os_family) or '*'
        edition = self._extract_edition(os_string) or '*'

        # Generate CPE
        cpe = f"cpe:2.3:o:{vendor}:{product}:{version}:*:{edition}:*:*:*:*:*"

        # Validate CPE format
        if not self._validate_cpe_format(cpe):
            return None

        # Calculate confidence
        confidence = 0.7
        if version != '*':
            confidence += 0.15
        if edition != '*':
            confidence += 0.10
        if manufacturer:
            confidence += 0.05

        return OSNormalizationResult(
            cpe=cpe,
            vendor=vendor,
            product=product,
            version=version if version != '-' else None,
            edition=edition if edition != '*' else None,
            os_family=os_family,
            os_normalized=self._cpe_to_legacy_format({
                'vendor': vendor,
                'product': product,
                'version': version,
                'edition': edition
            }),
            confidence=min(confidence, 1.0),
            method='pattern_based',
            latency_ms=0.0,
            tier=2
        )

    # =========================================================================
    # Tier 3: CPE Validation
    # =========================================================================

    def _validate_cpe_format(self, cpe: str) -> bool:
        """Validate CPE 2.3 format."""
        parts = cpe.split(':')
        if len(parts) != 13:
            return False
        if parts[0] != 'cpe' or parts[1] != '2.3' or parts[2] != 'o':
            return False
        # Allow '*' for vendor in special cases (unknown OS)
        # But product should never be empty or '-'
        if parts[3] == '' or parts[4] in ('', '-'):
            return False
        return True

    def _tier3_validate_cpe(self, result: OSNormalizationResult) -> OSNormalizationResult:
        """Tier 3: Validate CPE against dictionary."""
        if not result or not result.cpe:
            return result

        # Check if vendor:product exists in dictionary
        key = f"{result.vendor}:{result.product}"
        if key in self.cpe_dictionary:
            result.method = 'validated'
            result.tier = 3
            result.confidence = min(result.confidence + 0.05, 1.0)

        return result

    # =========================================================================
    # Tier 4: Fallback
    # =========================================================================

    def _tier4_fallback(self, os_string: str) -> OSNormalizationResult:
        """Tier 4: Fallback to simple normalization."""
        # Simple normalization: lowercase, remove extra spaces
        normalized = re.sub(r'\s+', ' ', os_string.lower().strip())

        return OSNormalizationResult(
            cpe=None,
            vendor=None,
            product=None,
            version=None,
            edition=None,
            os_family=None,
            os_normalized=normalized,
            confidence=0.5,
            method='fallback',
            latency_ms=0.0,
            tier=4
        )

    # =========================================================================
    # Pre-normalization: Garbage Rejection & Input Cleanup
    # =========================================================================

    # Android dessert code names → version numbers
    _ANDROID_DESSERT_MAP = {
        'cupcake': '1.5', 'donut': '1.6', 'eclair': '2.0', 'froyo': '2.2',
        'gingerbread': '3', 'honeycomb': '3', 'ice cream sandwich': '4',
        'jelly bean': '4.1', 'kitkat': '4.4', 'lollipop': '5',
        'marshmallow': '6', 'nougat': '7', 'oreo': '8', 'pie': '9',
    }

    # Android API levels (>20) → user-facing version numbers
    _ANDROID_API_MAP = {
        '21': '5', '22': '5.1', '23': '6', '24': '7', '25': '7.1',
        '26': '8', '27': '8.1', '28': '9', '29': '10', '30': '11',
        '31': '12', '32': '12', '33': '13', '34': '14', '35': '15',
    }

    # OS name variants → canonical form
    _CANONICAL_OS_NAMES = {
        'rokuos': 'roku os',
        'windowsce': 'windows ce',
        'smartcastos': 'smartcast os',
        'synologydsm': 'synology dsm',
        'thinos': 'thin os',
        'chromeos': 'chrome os',
        'fireos': 'fire os',
        'porteusos': 'porteus os',
        'webos': 'web os',
    }

    _GARBAGE_VENDOR_NAMES = frozenset({
        'microsoft', 'cisco device', 'hp laserjet printer',
        'hp ethernet multi-environment', 'axis camera',
    })

    _GARBAGE_VALUES = frozenset({
        '0.0.0.0', 'unknown 1.0', 'other',
    })

    def _reject_garbage(self, os_string: str) -> bool:
        """Reject strings that are not operating systems."""
        s = os_string.strip().lower()

        if re.match(r'^\d+\.\d+\.\d+\.\d+$', s):
            return True

        if re.match(r'^\d+\.\d+$', s) and not any(
            kw in s for kw in ('android', 'ios', 'windows')
        ):
            return True

        if s in self._GARBAGE_VENDOR_NAMES:
            return True

        if s in self._GARBAGE_VALUES:
            return True

        if re.match(r'windows server \d{4}\s+\d{4}', s):
            return True

        return False

    def _preprocess_android(self, os_string: str) -> str:
        """Convert Android dessert names and API levels to standard form."""
        s = os_string.strip().lower()

        # Bare dessert name → "android <version>"
        if s in self._ANDROID_DESSERT_MAP:
            return f"android {self._ANDROID_DESSERT_MAP[s]}"

        # "android NN" where NN > 20 → API level, map to version
        m = re.match(r'^android\s+(\d+)$', s)
        if m and int(m.group(1)) > 20:
            api_level = m.group(1)
            if api_level in self._ANDROID_API_MAP:
                return f"android {self._ANDROID_API_MAP[api_level]}"

        return os_string

    def _canonicalize_os_name(
        self, result: OSNormalizationResult
    ) -> OSNormalizationResult:
        """Apply canonical OS name mappings to the result."""
        if result and result.os_normalized:
            key = result.os_normalized.strip().lower()
            canonical = self._CANONICAL_OS_NAMES.get(key)
            if canonical:
                result.os_normalized = canonical
        return result

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _resolve_ambiguous_versions(self, os_string: str) -> str:
        """Resolve ambiguous version strings like '2016 or 2019' → '2019'.

        Handles patterns: 'X or Y', 'X/Y', 'X / Y' where X and Y are
        version-like numbers. Keeps the higher (more recent) version.
        """
        # Match "NUMBER or NUMBER" or "NUMBER/NUMBER" patterns
        # Negative lookbehind for R/r: don't split "R2/7" (service release indicator #59 Item 7)
        match = re.search(r'(?<![Rr])(\d+)\s*(?:or|/)\s*(\d+)', os_string, re.IGNORECASE)
        if match:
            v1, v2 = int(match.group(1)), int(match.group(2))
            keep = str(max(v1, v2))
            # Replace the entire "X or Y" / "X/Y" span with just the winner
            os_string = os_string[:match.start()] + keep + os_string[match.end():]
        return os_string

    def _cpe_to_legacy_format(self, cpe_info: Dict) -> str:
        """Convert CPE info to legacy normalized format for compatibility."""
        parts = []

        product_raw = cpe_info.get('product', '')
        product = self._PRODUCT_DISPLAY_NAMES.get(product_raw.lower(), product_raw.replace('_', ' '))
        if product:
            parts.append(product)

        version = cpe_info.get('version')
        # Only append version if it's not already in the product name
        # e.g., "windows_10" already contains "10", don't duplicate it
        if version and version not in ('-', '*'):
            product_lower = product.lower()
            version_str = str(version)

            # For Windows NT versions, the product name already encodes the marketing version.
            # NT 6.x: windows_7=6.1, windows_8=6.2, windows_8.1=6.3
            # NT 10.0.x: windows_10 and windows_11 both use 10.0.xxxxx build strings
            # Don't append NT version numbers to keep normalized output clean.
            if 'windows' in product_lower and (
                version_str.startswith('6.') or
                re.match(r'^10\.0\.\d{4,}', version_str)
            ):
                pass
            else:
                # Extract major version from both product and version string
                # For "windows 10" and "10.0.17134.2208", both have major version "10"
                product_version_match = re.search(r'\b(\d+)(?:\.\d+)*\b', product_lower)
                version_major_match = re.search(r'^(\d+)(?:\.\d+)*', version_str)

                # Check if major versions match
                if product_version_match and version_major_match:
                    product_major = product_version_match.group(1)
                    version_major = version_major_match.group(1)
                    # If major versions match, don't append the full version
                    if product_major == version_major:
                        # Skip appending version - it's already represented in product name
                        pass
                    else:
                        parts.append(version_str)
                # If version string is not substring of product, append it
                elif version_str not in product_lower:
                    parts.append(version_str)

        edition = cpe_info.get('edition')
        if edition and edition != '*':
            # Skip if edition word is already in product name (#26 Issue 2c)
            if edition.lower() not in product.lower():
                parts.append(edition)

        return ' '.join(parts).strip()

    # =========================================================================
    # Main Normalization Method
    # =========================================================================

    def normalize(
        self,
        os_string: str,
        manufacturer: Optional[str] = None
    ) -> OSNormalizationResult:
        """
        Normalize OS string to CPE format.

        Args:
            os_string: Raw OS string to normalize
            manufacturer: Optional manufacturer/vendor information

        Returns:
            OSNormalizationResult with CPE and metadata
        """
        start_time = time.time()
        self.stats['total_requests'] += 1

        # Preprocess: resolve ambiguous version strings like "2016 or 2019"
        os_string = self._resolve_ambiguous_versions(os_string)

        # Reject known garbage before any tier processing
        if self._reject_garbage(os_string):
            result = self._tier4_fallback(os_string)
            result.os_normalized = None
            result.confidence = 0.0
            result.method = 'rejected'
            self.stats['rejected_hits'] += 1
            result.latency_ms = (time.time() - start_time) * 1000
            return result

        # Preprocess Android dessert names and API levels
        os_string = self._preprocess_android(os_string)

        # Tier 1: Exact match cache
        result = self._tier1_exact_match(os_string)
        if result:
            self.stats['tier_1_hits'] += 1
            result.latency_ms = (time.time() - start_time) * 1000
            return self._canonicalize_os_name(result)

        # Tier 2: Pattern-based generation
        result = self._tier2_pattern_based(os_string, manufacturer)
        if result:
            # Tier 3: Validate against CPE dictionary
            result = self._tier3_validate_cpe(result)

            if result.tier == 3:
                self.stats['tier_3_hits'] += 1
            else:
                self.stats['tier_2_hits'] += 1

            result.latency_ms = (time.time() - start_time) * 1000
            return self._canonicalize_os_name(result)

        # Tier 4: Fallback
        result = self._tier4_fallback(os_string)
        self.stats['tier_4_hits'] += 1
        result.latency_ms = (time.time() - start_time) * 1000

        return self._canonicalize_os_name(result)

    def get_statistics(self) -> Dict:
        """Get normalizer performance statistics."""
        total = self.stats['total_requests']
        if total == 0:
            return self.stats

        return {
            **self.stats,
            'tier_1_percentage': (self.stats['tier_1_hits'] / total) * 100,
            'tier_2_percentage': (self.stats['tier_2_hits'] / total) * 100,
            'tier_3_percentage': (self.stats['tier_3_hits'] / total) * 100,
            'tier_4_percentage': (self.stats['tier_4_hits'] / total) * 100,
            'rejected_percentage': (self.stats['rejected_hits'] / total) * 100,
        }

    def print_statistics(self):
        """Print normalizer performance statistics."""
        stats = self.get_statistics()

        print("\nHybrid CPE Normalizer Statistics")
        print("=" * 60)
        print(f"Total requests: {stats['total_requests']:,}")
        print(f"\nTier 1 (Exact Match):  {stats['tier_1_hits']:6,} ({stats.get('tier_1_percentage', 0):.2f}%)")
        print(f"Tier 2 (Pattern-Based): {stats['tier_2_hits']:6,} ({stats.get('tier_2_percentage', 0):.2f}%)")
        print(f"Tier 3 (Validated):     {stats['tier_3_hits']:6,} ({stats.get('tier_3_percentage', 0):.2f}%)")
        print(f"Tier 4 (Fallback):      {stats['tier_4_hits']:6,} ({stats.get('tier_4_percentage', 0):.2f}%)")
        print(f"Rejected (Garbage):     {stats['rejected_hits']:6,} ({stats.get('rejected_percentage', 0):.2f}%)")


# =============================================================================
# CLI for Testing
# =============================================================================

def main():
    """Test the hybrid normalizer."""
    print("Hybrid CPE Normalizer - Test Interface")
    print("=" * 60)

    # Initialize normalizer
    normalizer = HybridCPENormalizer()

    # Test cases
    test_cases = [
        ("Windows 11 Enterprise", "Dell Inc."),
        ("Windows 10 Pro", "Lenovo"),
        ("RHEL 8.8", "Amazon EC2"),
        ("cisco ios", "Cisco"),
        ("Ubuntu 20.04", None),
        ("Unknown OS 12.3", None),
    ]

    print("\nTest Cases:")
    print("-" * 80)

    for os_string, manufacturer in test_cases:
        result = normalizer.normalize(os_string, manufacturer)

        print(f"\nOS: {os_string}")
        if manufacturer:
            print(f"Manufacturer: {manufacturer}")
        print(f"CPE: {result.cpe}")
        print(f"Vendor: {result.vendor} | Product: {result.product}")
        print(f"Version: {result.version} | Edition: {result.edition}")
        print(f"Tier: {result.tier} | Method: {result.method} | Confidence: {result.confidence:.2f} | Latency: {result.latency_ms:.3f}ms")

    # Print statistics
    normalizer.print_statistics()


if __name__ == '__main__':
    main()
