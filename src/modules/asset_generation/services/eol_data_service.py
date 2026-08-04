# SPDX-License-Identifier: AGPL-3.0-or-later
"""
End-of-Life Data Service

This service manages fetching, storing, and querying operating system end-of-life data
from authoritative sources like endoflife.date.

Key responsibilities:
- Fetch EOL data from endoflife.date API
- Store and update EOL data in PostgreSQL
- Provide lookup methods for OS EOL information
- Handle product name mapping and version normalization
"""

import logging
import re
import time
from datetime import datetime, date
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)


# ===========================================================================================================
# Product Mapping Constants
# ===========================================================================================================

# Maps AssetAtlas normalized OS names to endoflife.date product identifiers
OS_TO_EOL_PRODUCT_MAPPING = {
    # Windows
    "Windows 10": "windows",
    "Windows 11": "windows",
    "Windows 8": "windows",
    "Windows 8.1": "windows",
    "Windows 7": "windows",
    "Windows Vista": "windows",
    "Windows XP": "windows",

    # Windows Server (all editions and years map to windows-server)
    "Windows Server 2012": "windows-server",
    "Windows Server 2012 R2": "windows-server",
    "Windows Server 2012 Standard": "windows-server",
    "Windows Server 2012 Datacenter": "windows-server",
    "windows server 2012": "windows-server",
    "windows server 2012 standard": "windows-server",
    "windows server 2012 datacenter": "windows-server",

    "Windows Server 2016": "windows-server",
    "Windows Server 2016 Standard": "windows-server",
    "Windows Server 2016 Datacenter": "windows-server",
    "windows server 2016": "windows-server",
    "windows server 2016 standard": "windows-server",
    "windows server 2016 datacenter": "windows-server",

    "Windows Server 2019": "windows-server",
    "Windows Server 2019 Standard": "windows-server",
    "Windows Server 2019 Datacenter": "windows-server",
    "windows server 2019": "windows-server",
    "windows server 2019 standard": "windows-server",
    "windows server 2019 datacenter": "windows-server",

    "Windows Server 2022": "windows-server",
    "Windows Server 2022 Standard": "windows-server",
    "Windows Server 2022 Datacenter": "windows-server",
    "windows server 2022": "windows-server",
    "windows server 2022 standard": "windows-server",
    "windows server 2022 datacenter": "windows-server",

    "Windows Server 2025": "windows-server",

    # Ubuntu
    "Ubuntu": "ubuntu",
    "Ubuntu Server": "ubuntu",
    "Ubuntu Desktop": "ubuntu",

    # Red Hat
    "Red Hat Enterprise Linux": "rhel",
    "RHEL": "rhel",
    "Red Hat Linux": "rhel",

    # CentOS
    "CentOS": "centos",
    "CentOS Linux": "centos",
    "CentOS Stream": "centos-stream",

    # Debian
    "Debian": "debian",
    "Debian GNU/Linux": "debian",

    # Fedora
    "Fedora": "fedora",
    "Fedora Linux": "fedora",

    # Other Linux
    "Alpine Linux": "alpine",
    "Rocky Linux": "rocky-linux",
    "AlmaLinux": "almalinux",
    "Oracle Linux": "oracle-linux",
    "SUSE Linux Enterprise": "sles",
    "SLES": "sles",
    "openSUSE Leap": "opensuse-leap",

    # macOS
    "macOS": "macos",
    "Mac OS X": "mac-os-x",
    "OS X": "mac-os-x",

    # iOS (Apple Mobile OS)
    "iOS": "ios",
    "ios": "ios",
    "iPhone OS": "ios",

    # Android
    "Android": "android",
    "android": "android",
}

# Products to sync from endoflife.date (comprehensive list)
DEFAULT_PRODUCTS_TO_SYNC = [
    # Mobile OS (highest enrichment rates: iOS 21.3%, Android 20.5%)
    "ios",
    "android",

    # Windows (all versions map to single unified products)
    "windows",
    "windows-server",

    # Linux
    "ubuntu",
    "rhel",
    "centos",
    "centos-stream",
    "debian",
    "fedora",
    "alpine",
    "rocky-linux",
    "almalinux",
    "oracle-linux",
    "sles",
    "opensuse-leap",

    # macOS
    "macos",
    "mac-os-x",
]


@dataclass
class EOLData:
    """Represents EOL data for a specific OS version."""
    product_name: str
    cycle: str
    release_label: Optional[str] = None
    codename: Optional[str] = None
    release_date: Optional[date] = None
    eol_date: Optional[date] = None
    support_end_date: Optional[date] = None
    extended_support_date: Optional[date] = None
    latest_version: Optional[str] = None
    latest_release_date: Optional[date] = None
    lts: bool = False
    link: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None
    last_synced: Optional[datetime] = None


class EOLDataService:
    """
    Service for managing OS End-of-Life data from endoflife.date.

    Provides methods to:
    - Fetch EOL data from endoflife.date API
    - Store and update EOL data in PostgreSQL
    - Look up EOL information for specific OS versions
    - Map AssetAtlas OS names to endoflife.date products
    """

    def __init__(self, postgres_adapter):
        """
        Initialize EOL Data Service.

        Args:
            postgres_adapter: Database adapter for PostgreSQL operations
        """
        self.db = postgres_adapter
        self.api_base_url = "https://endoflife.date/api"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'AssetAtlas/1.0 (IT Asset Management System)',
            'Accept': 'application/json'
        })

        logger.info("EOLDataService initialized")

    # =========================================================================
    # API Data Fetching
    # =========================================================================

    def fetch_product_eol_data(self, product: str) -> List[Dict[str, Any]]:
        """
        Fetch EOL data for a product from endoflife.date API.

        Retries up to 3 times on transient errors (5xx, timeouts, connection
        errors) with exponential backoff. Returns empty list on 404 (product
        not found) or after all retries exhausted.

        Args:
            product: Product identifier (e.g., "windows", "ubuntu", "macos")

        Returns:
            List of EOL data dictionaries for all versions/cycles.
            Empty list if product not found or all attempts fail.
        """
        url = f"{self.api_base_url}/{product}.json"
        max_attempts = 3
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(
                    f"Fetching EOL data for product: {product} from {url}"
                    + (f" (attempt {attempt})" if attempt > 1 else "")
                )
                response = self.session.get(url, timeout=30)
                response.raise_for_status()

                data = response.json()
                logger.info(f"Successfully fetched {len(data)} cycles for product: {product}")
                return data

            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    logger.warning(f"Product not found in endoflife.date: {product}")
                    return []
                last_error = e
                logger.warning(
                    f"HTTP {e.response.status_code if e.response else '?'} "
                    f"fetching EOL data for {product} (attempt {attempt}/{max_attempts})"
                )
            except requests.exceptions.RequestException as e:
                last_error = e
                logger.warning(
                    f"Request error fetching EOL data for {product} "
                    f"(attempt {attempt}/{max_attempts}): {e}"
                )

            if attempt < max_attempts:
                backoff = 2 ** (attempt - 1)  # 1s, 2s
                time.sleep(backoff)

        logger.error(
            f"All {max_attempts} attempts failed for EOL data for {product}: {last_error}"
        )
        return []

    def fetch_all_products(self) -> List[str]:
        """
        Fetch list of all available products from endoflife.date API.

        Returns:
            List of product identifiers
        """
        url = f"{self.api_base_url}/all.json"
        logger.info(f"Fetching all products from {url}")

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            products = response.json()
            logger.info(f"Successfully fetched {len(products)} products from endoflife.date")
            return products

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching product list: {e}")
            raise

    # =========================================================================
    # Data Parsing and Transformation
    # =========================================================================

    def _parse_date(self, date_str: Any) -> Optional[date]:
        """
        Parse date string from API response.

        Args:
            date_str: Date string in various formats or boolean False

        Returns:
            date object or None
        """
        if not date_str or date_str is False:
            return None

        if isinstance(date_str, date):
            return date_str

        if not isinstance(date_str, str):
            return None

        try:
            # Try ISO format (YYYY-MM-DD)
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            try:
                # Try alternative format (YYYY-MM)
                return datetime.strptime(date_str + "-01", "%Y-%m-%d").date()
            except ValueError:
                logger.warning(f"Unable to parse date: {date_str}")
                return None

    def _transform_api_data(self, product: str, api_data: Dict[str, Any]) -> EOLData:
        """
        Transform API response data to EOLData object.

        Args:
            product: Product identifier
            api_data: Raw API response data

        Returns:
            EOLData object
        """
        # Extract cycle (version identifier)
        cycle = str(api_data.get('cycle', ''))

        # Extract dates
        release_date = self._parse_date(api_data.get('releaseDate'))
        eol_date = self._parse_date(api_data.get('eol'))
        support_end_date = self._parse_date(api_data.get('support'))
        extended_support_date = self._parse_date(api_data.get('extendedSupport'))
        latest_release_date = self._parse_date(api_data.get('latestReleaseDate'))

        # Extract metadata
        release_label = api_data.get('releaseLabel', api_data.get('cycle'))
        codename = api_data.get('codename')
        latest_version = api_data.get('latest')
        lts = bool(api_data.get('lts', False))
        link = api_data.get('link')

        return EOLData(
            product_name=product,
            cycle=cycle,
            release_label=release_label,
            codename=codename,
            release_date=release_date,
            eol_date=eol_date,
            support_end_date=support_end_date,
            extended_support_date=extended_support_date,
            latest_version=latest_version,
            latest_release_date=latest_release_date,
            lts=lts,
            link=link,
            raw_data=api_data
        )

    # =========================================================================
    # Database Operations
    # =========================================================================

    def store_eol_data(self, eol_data: EOLData) -> None:
        """
        Store or update EOL data in database.

        Args:
            eol_data: EOLData object to store
        """
        import json

        query = """
        INSERT INTO os_eol_data (
            product_name, cycle, release_label, codename,
            release_date, eol_date, support_end_date, extended_support_date,
            latest_version, latest_release_date, lts, link,
            raw_data, last_synced
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, NOW()
        )
        ON CONFLICT (product_name, cycle)
        DO UPDATE SET
            release_label = EXCLUDED.release_label,
            codename = EXCLUDED.codename,
            release_date = EXCLUDED.release_date,
            eol_date = EXCLUDED.eol_date,
            support_end_date = EXCLUDED.support_end_date,
            extended_support_date = EXCLUDED.extended_support_date,
            latest_version = EXCLUDED.latest_version,
            latest_release_date = EXCLUDED.latest_release_date,
            lts = EXCLUDED.lts,
            link = EXCLUDED.link,
            raw_data = EXCLUDED.raw_data,
            last_synced = NOW()
        """

        raw_data_json = json.dumps(eol_data.raw_data) if eol_data.raw_data else None

        params = (
            eol_data.product_name,
            eol_data.cycle,
            eol_data.release_label,
            eol_data.codename,
            eol_data.release_date,
            eol_data.eol_date,
            eol_data.support_end_date,
            eol_data.extended_support_date,
            eol_data.latest_version,
            eol_data.latest_release_date,
            eol_data.lts,
            eol_data.link,
            raw_data_json
        )

        # Use fetch=False for INSERT/UPDATE operations
        self.db.execute_query(query, params, fetch=False)

    def sync_product(self, product: str) -> Tuple[int, int]:
        """
        Sync EOL data for a specific product.

        Args:
            product: Product identifier (e.g., "windows", "ubuntu")

        Returns:
            Tuple of (stored_count, error_count)
        """
        logger.info(f"Syncing EOL data for product: {product}")

        try:
            # Fetch data from API
            api_data_list = self.fetch_product_eol_data(product)

            stored_count = 0
            error_count = 0

            # Process and store each cycle
            for api_data in api_data_list:
                try:
                    eol_data = self._transform_api_data(product, api_data)
                    self.store_eol_data(eol_data)
                    stored_count += 1
                except Exception as e:
                    logger.error(f"Error storing EOL data for {product} cycle {api_data.get('cycle')}: {e}")
                    error_count += 1

            logger.info(f"Synced {product}: {stored_count} stored, {error_count} errors")
            return (stored_count, error_count)

        except Exception as e:
            logger.error(f"Error syncing product {product}: {e}")
            return (0, 1)

    def sync_all_products(self, product_list: Optional[List[str]] = None) -> Dict[str, Tuple[int, int]]:
        """
        Sync EOL data for multiple products.

        Args:
            product_list: List of products to sync (defaults to DEFAULT_PRODUCTS_TO_SYNC)

        Returns:
            Dictionary mapping product name to (stored_count, error_count)
        """
        products = product_list or DEFAULT_PRODUCTS_TO_SYNC
        logger.info(f"Syncing EOL data for {len(products)} products")

        results = {}
        for product in products:
            results[product] = self.sync_product(product)

        # Summary
        total_stored = sum(r[0] for r in results.values())
        total_errors = sum(r[1] for r in results.values())
        logger.info(f"Sync complete: {total_stored} records stored, {total_errors} errors across {len(products)} products")

        return results

    # =========================================================================
    # Lookup and Mapping Operations
    # =========================================================================

    def map_os_to_product(self, os_name: str) -> Optional[str]:
        """
        Map AssetAtlas OS name to endoflife.date product identifier.

        Args:
            os_name: Normalized OS name from AssetAtlas

        Returns:
            Product identifier for endoflife.date API, or None if no mapping exists
        """
        if not os_name:
            return None

        # Direct lookup
        product = OS_TO_EOL_PRODUCT_MAPPING.get(os_name)
        if product:
            return product

        # Fuzzy matching for partial names
        os_name_lower = os_name.lower()

        # iOS variants (must come before checking for "ios" to avoid false positives)
        if os_name_lower.startswith('ios ') or os_name_lower.startswith('apple ios'):
            return 'ios'

        # Android variants
        if os_name_lower.startswith('android'):
            return 'android'

        # Windows variants
        if 'windows server' in os_name_lower:
            # All Windows Server versions map to single 'windows-server' product
            return 'windows-server'
        elif 'windows' in os_name_lower:
            return 'windows'

        # Linux variants
        if 'ubuntu' in os_name_lower:
            return 'ubuntu'
        elif 'red hat' in os_name_lower or 'rhel' in os_name_lower:
            return 'rhel'
        elif 'centos stream' in os_name_lower:
            return 'centos-stream'
        elif 'centos' in os_name_lower:
            return 'centos'
        elif 'debian' in os_name_lower:
            return 'debian'
        elif 'fedora' in os_name_lower:
            return 'fedora'
        elif 'alpine' in os_name_lower:
            return 'alpine'
        elif 'rocky' in os_name_lower:
            return 'rocky-linux'
        elif 'alma' in os_name_lower:
            return 'almalinux'
        elif 'oracle linux' in os_name_lower:
            return 'oracle-linux'
        elif 'suse' in os_name_lower or 'sles' in os_name_lower:
            return 'sles'

        # macOS variants
        if 'macos' in os_name_lower or 'mac os x' in os_name_lower or 'os x' in os_name_lower:
            return 'macos' if 'macos' in os_name_lower else 'mac-os-x'

        logger.debug(f"No product mapping found for OS: {os_name}")
        return None

    def normalize_version_for_lookup(self, product: str, os_version: str) -> Optional[str]:
        """
        Normalize OS version string for EOL database lookup.

        Args:
            product: Product identifier
            os_version: Raw version string

        Returns:
            Normalized cycle identifier for database lookup, or None
        """
        if not os_version:
            return None

        version = os_version.strip()

        # Windows Server version normalization (must come before desktop)
        if product == 'windows-server':
            # Extract server year (2003, 2008, 2012, 2016, 2019, 2022, 2025)
            year_match = re.search(r'\b(200[038]|201[269]|202[25])\b', version)
            if year_match:
                year = year_match.group(1)
                # Check for R2 variant
                if re.search(r'\bR2\b', version, re.IGNORECASE):
                    return f"{year}-r2"
                # Check for SP variant
                sp_match = re.search(r'\bSP(\d)\b', version, re.IGNORECASE)
                if sp_match:
                    return f"{year}-sp{sp_match.group(1)}"
                return year
            return None

        # Windows desktop version normalization (covers 'windows', 'windows-10', 'windows-11', etc.)
        elif product.startswith('windows'):
            # Extract major version (10, 11, etc.)
            version_match = re.search(r'\b(10|11|8\.1|8|7|Vista|XP)\b', version, re.IGNORECASE)
            if version_match:
                major_version = version_match.group(1)

                # Check for feature updates (21H2, 22H2, etc.)
                feature_match = re.search(r'\b(\d{2}H[12])\b', version, re.IGNORECASE)
                if feature_match:
                    feature = feature_match.group(1).lower()
                    return f"{major_version.lower()}-{feature}"

                # Map Windows build numbers to feature updates
                build_match = re.search(r'\b10\.0\.(\d{5})', version)
                if build_match:
                    build_num = int(build_match.group(1))
                    # Windows 10 build number mappings
                    build_to_feature = {
                        19041: '2004', 19042: '20h2', 19043: '21h1',
                        19044: '21h2', 19045: '22h2',  # 19045 is Windows 10 22H2
                        22000: '21h2',  # Windows 11 21H2
                        22621: '22h2', 22622: '22h2',  # Windows 11 22H2
                        22631: '23h2',  # Windows 11 23H2
                        26100: '24h2'   # Windows 11 24H2
                    }
                    # Find closest build number (allows for minor variations)
                    for build, feature in sorted(build_to_feature.items(), reverse=True):
                        if build_num >= build:
                            return f"{major_version.lower()}-{feature}"

                return None  # Bare '10' or '11' has no matching cycle

        # Ubuntu version normalization (extract X.Y format)
        elif product == 'ubuntu':
            ubuntu_match = re.search(r'\b(\d+\.\d{2})\b', version)
            if ubuntu_match:
                return ubuntu_match.group(1)

        # RHEL version normalization (extract major version)
        elif product == 'rhel':
            rhel_match = re.search(r'\b(\d+)(?:\.\d+)?\b', version)
            if rhel_match:
                return rhel_match.group(1)

        # macOS version normalization (extract major version number or codename)
        elif product == 'macos':
            # Try numeric version (e.g., "13", "14")
            macos_match = re.search(r'\b(\d+)(?:\.\d+)*\b', version)
            if macos_match:
                return macos_match.group(1)

            # Try codename matching (Ventura, Monterey, etc.)
            codenames = ['sequoia', 'sonoma', 'ventura', 'monterey', 'big sur', 'catalina', 'mojave']
            version_lower = version.lower()
            for codename in codenames:
                if re.search(rf'\b{re.escape(codename)}\b', version_lower):
                    # Query database for this codename
                    results = self.db.execute_query(
                        "SELECT cycle FROM os_eol_data WHERE product_name = %s AND LOWER(codename) = %s LIMIT 1",
                        (product, codename),
                        fetch=True
                    )
                    if results:
                        return results[0]['cycle']

        # iOS/iPadOS version normalization (#63): try keyword-prefixed match first
        elif product == 'ios':
            # Try "iOS 17.2" or "iPadOS 16.1" pattern first
            ios_kw_match = re.search(
                r'(?:ios|ipados)\s+(\d+)(?:\.\d+)*', version, re.IGNORECASE
            )
            if ios_kw_match:
                return ios_kw_match.group(1)
            # Fallback: first number in string
            ios_match = re.search(r'\b(\d+)(?:\.\d+)*\b', version)
            if ios_match:
                return ios_match.group(1)

        # Android version normalization (#63): try keyword-prefixed match first
        elif product == 'android':
            # Try "Android 13" or "Android 14.0" pattern first
            android_kw_match = re.search(
                r'(?:android)\s+(\d+)(?:\.\d+)*', version, re.IGNORECASE
            )
            if android_kw_match:
                return android_kw_match.group(1)
            # Fallback: first number in string
            android_match = re.search(r'\b(\d+)(?:\.\d+)*\b', version)
            if android_match:
                return android_match.group(1)

        # Default: return cleaned version string
        cleaned = re.sub(r'[^\d\.]', '', version)
        return cleaned if cleaned else None

    def lookup_eol_data(self, os_name: str, os_version: str) -> Optional[EOLData]:
        """
        Look up EOL data for a specific OS and version.

        Args:
            os_name: AssetAtlas normalized OS name
            os_version: OS version string

        Returns:
            EOLData object or None if not found
        """
        # Map OS name to product
        product = self.map_os_to_product(os_name)
        if not product:
            logger.debug(f"No product mapping for OS: {os_name}")
            return None

        # For Windows desktop, combine os_name and os_version to get full context
        # Example: os_name="Windows 10", os_version="22H2" -> "Windows 10 22H2"
        # For Windows Server, os_name already contains the year (e.g. "windows server 2019 standard")
        # so use os_name alone to avoid duplicating the year
        if product == 'windows-server':
            version_string = os_name
        elif product.startswith('windows'):
            version_string = f"{os_name} {os_version}"
        # For iOS and Android, use os_name if os_version is not provided
        # Example: os_name="ios 18.6.2" contains the version
        elif product in ['ios', 'android'] and not os_version:
            version_string = os_name  # Version is embedded in os_name
        else:
            version_string = os_version

        # Normalize version
        cycle = self.normalize_version_for_lookup(product, version_string)
        if not cycle:
            # For Windows desktop with bare major version (e.g. "10" or "11" without
            # feature update), fall back to the latest non-LTS/IoT cycle for that major
            # version. This gives the best general EOL answer when we don't know the
            # exact feature update.
            if product.startswith('windows') and product != 'windows-server':
                major_match = re.search(r'\b(10|11)\b', version_string)
                if major_match:
                    major = major_match.group(1)
                    fallback_query = """
                    SELECT
                        product_name, cycle, release_label, codename,
                        release_date, eol_date, support_end_date, extended_support_date,
                        latest_version, latest_release_date, lts, link, raw_data,
                        last_synced
                    FROM os_eol_data
                    WHERE product_name = %s
                      AND cycle LIKE %s
                      AND cycle NOT LIKE '%%lts'
                      AND cycle NOT LIKE '%%iot%%'
                    ORDER BY eol_date DESC
                    LIMIT 1
                    """
                    results = self.db.execute_query(
                        fallback_query, (product, f"{major}-%"), fetch=True
                    )
                    if results:
                        result = results[0]
                        logger.debug(
                            f"Fallback EOL match for {os_name}: "
                            f"cycle={result['cycle']}, eol_date={result['eol_date']}"
                        )
                        return self._build_eol_data(result)
            logger.debug(f"Could not normalize version: {os_version} for product: {product}")
            return None

        # Query database
        query = """
        SELECT
            product_name, cycle, release_label, codename,
            release_date, eol_date, support_end_date, extended_support_date,
            latest_version, latest_release_date, lts, link, raw_data,
            last_synced
        FROM os_eol_data
        WHERE product_name = %s AND cycle = %s
        LIMIT 1
        """

        results = self.db.execute_query(query, (product, cycle), fetch=True)

        if not results and product.startswith('windows'):
            # Windows cycles in endoflife.date have edition suffixes like
            # "11-24h2-e" (enterprise) and "11-24h2-w" (workstation).
            # When we only know the feature update (e.g. "11-24h2"), pick the
            # workstation variant as the conservative (earliest) EOL date,
            # excluding LTS and IoT variants.
            suffix_query = """
            SELECT
                product_name, cycle, release_label, codename,
                release_date, eol_date, support_end_date, extended_support_date,
                latest_version, latest_release_date, lts, link, raw_data,
                last_synced
            FROM os_eol_data
            WHERE product_name = %s
              AND cycle LIKE %s
              AND cycle NOT LIKE '%%lts'
              AND cycle NOT LIKE '%%iot%%'
            ORDER BY eol_date ASC
            LIMIT 1
            """
            results = self.db.execute_query(
                suffix_query, (product, f"{cycle}-%"), fetch=True
            )
            if results:
                logger.debug(
                    f"Suffix match for {product} cycle={cycle}: "
                    f"matched {results[0]['cycle']}, eol={results[0]['eol_date']}"
                )

        if not results:
            logger.debug(f"No EOL data found for product={product}, cycle={cycle}")
            return None

        return self._build_eol_data(results[0])

    def _build_eol_data(self, result: Dict[str, Any]) -> EOLData:
        """Convert a database row dict into an EOLData object."""
        return EOLData(
            product_name=result['product_name'],
            cycle=result['cycle'],
            release_label=result['release_label'],
            codename=result['codename'],
            release_date=result['release_date'],
            eol_date=result['eol_date'],
            support_end_date=result['support_end_date'],
            extended_support_date=result['extended_support_date'],
            latest_version=result['latest_version'],
            latest_release_date=result['latest_release_date'],
            lts=result['lts'],
            link=result['link'],
            raw_data=result['raw_data'],  # PostgreSQL JSONB is already deserialized
            last_synced=result.get('last_synced')
        )

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def get_sync_status(self) -> Dict[str, Any]:
        """
        Get synchronization status from database.

        Returns:
            Dictionary with sync statistics
        """
        query = """
        SELECT
            COUNT(*) as total_records,
            COUNT(DISTINCT product_name) as total_products,
            MIN(last_synced) as oldest_sync,
            MAX(last_synced) as latest_sync
        FROM os_eol_data
        """

        results = self.db.execute_query(query, fetch=True)
        return results[0] if results else {}

    def get_products_in_database(self) -> List[str]:
        """
        Get list of products currently in database.

        Returns:
            List of product names
        """
        query = "SELECT DISTINCT product_name FROM os_eol_data ORDER BY product_name"
        results = self.db.execute_query(query, fetch=True)
        return [r['product_name'] for r in results] if results else []

    def clear_product_data(self, product: str) -> int:
        """
        Clear all EOL data for a specific product.

        Args:
            product: Product identifier

        Returns:
            Number of records deleted
        """
        query = "DELETE FROM os_eol_data WHERE product_name = %s"
        self.db.execute_query(query, (product,), fetch=False)
        logger.info(f"Cleared EOL data for product: {product}")
        # Note: We can't easily get the count of deleted rows from execute_query with fetch=False
        return 0  # Return 0 as placeholder
