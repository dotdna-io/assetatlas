#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Qualys Cloud Platform Connector

Collects asset inventory data from Qualys via the Host List API
(/api/2.0/fo/asset/host/?action=list). Uses Basic Authentication
with a mandatory X-Requested-With header.

XML-only responses. URL-based pagination (not offset-based).
Rate-limit aware via X-RateLimit-* response headers.
"""

from typing import Dict, List, Iterator, Any, Optional
from datetime import datetime
import xml.etree.ElementTree as ET
import time
import requests

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import BasicAuth, LinkPaginator, ConfigSchema


# Platform URL mapping — Qualys is deployed across regional platforms.
# Users must select the correct platform for their subscription.
PLATFORM_URLS = {
    'US1': 'https://qualysapi.qualys.com',
    'US2': 'https://qualysapi.qg2.apps.qualys.com',
    'US3': 'https://qualysapi.qg3.apps.qualys.com',
    'US4': 'https://qualysapi.qg4.apps.qualys.com',
    'EU1': 'https://qualysapi.qualys.eu',
    'EU2': 'https://qualysapi.qg2.apps.qualys.eu',
    'IN1': 'https://qualysapi.qg1.apps.qualys.in',
    'CA1': 'https://qualysapi.qg1.apps.qualys.ca',
    'AE1': 'https://qualysapi.qg1.apps.qualys.ae',
}

# Pagination indicators in the <WARNING><TEXT> element
PAGINATION_INDICATORS = ['truncated', 'limit exceeded', 'use url to get next batch']

# Max pages before stopping (safety cap: 100 * 10000 = 1M records)
MAX_PAGINATION_PAGES = 100

# Rate limit threshold — sleep proactively when remaining <= this value
RATE_LIMIT_THRESHOLD = 5

# Default wait time (seconds) when rate limit info unavailable
DEFAULT_RATE_LIMIT_WAIT = 60

# API request timeout (seconds) — Qualys recommends 300s for large responses
API_TIMEOUT = 300


@ConnectorRegistry.register
class QualysConnector(BaseConnector):
    """
    Qualys Cloud Platform Connector

    Fetches asset inventory from Qualys via the Host List API.
    Requires API credentials with Manager role for full host visibility.
    """

    # Connector Metadata
    integration_type = "qualys_assets"
    display_name = "Qualys"
    category = "Vulnerability Management"
    description = "Collect asset inventory data from Qualys Cloud Platform"
    logo_url = "/logos/qualys.png"

    def __init__(self, config: Dict[str, Any]):
        self.truncation_limit = int(config.get('truncation_limit', 10000))
        self.include_cloud_metadata = config.get('include_cloud_metadata', True)
        self.include_tags = config.get('include_tags', True)

        # Resolve platform URL before super().__init__ calls _build_auth
        platform = config.get('platform', 'US1')
        if platform == 'CUSTOM':
            base_url = config.get('api_url', '').rstrip('/')
            if not base_url:
                raise ValueError("Custom API URL is required when platform is set to CUSTOM")
        else:
            base_url = PLATFORM_URLS.get(platform)
            if not base_url:
                raise ValueError(f"Unknown platform: {platform}. Valid: {', '.join(PLATFORM_URLS.keys())}")

        self.base_url = base_url
        self.platform = platform

        # Validate required fields before auth is built
        if not config.get('username'):
            raise ValueError("Qualys username is required")
        if not config.get('password'):
            raise ValueError("Qualys password is required")

        super().__init__(config)

        # Inject required Qualys header — all API calls must include this
        self.auth.session.headers['X-Requested-With'] = 'AssetAtlas'

        # Rate limit tracking state
        self._rate_limit_remaining = 300  # conservative default
        self._rate_limit_to_wait = 0

    # ------------------------------------------------------------------
    # SDK hooks
    # ------------------------------------------------------------------

    def _build_auth(self):
        return BasicAuth(
            config={
                'username': self.config.get('username'),
                'password': self.config.get('password'),
                'base_url': self.base_url,
            },
            logger=self.logger,
        )

    # ------------------------------------------------------------------
    # Config schema (class method — no instance needed)
    # ------------------------------------------------------------------

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return (
            ConfigSchema()
            .add_select(
                'platform', 'Qualys Platform',
                options=[
                    {'value': 'US1', 'label': 'US Platform 1 (qualysapi.qualys.com)'},
                    {'value': 'US2', 'label': 'US Platform 2'},
                    {'value': 'US3', 'label': 'US Platform 3'},
                    {'value': 'US4', 'label': 'US Platform 4'},
                    {'value': 'EU1', 'label': 'EU Platform 1 (qualysapi.qualys.eu)'},
                    {'value': 'EU2', 'label': 'EU Platform 2'},
                    {'value': 'IN1', 'label': 'India Platform 1'},
                    {'value': 'CA1', 'label': 'Canada Platform 1'},
                    {'value': 'AE1', 'label': 'UAE Platform 1'},
                    {'value': 'CUSTOM', 'label': 'Custom URL (Private Cloud)'},
                ],
                default='US1',
                required=True,
                description='Select your Qualys platform region (Help > About in Qualys UI)',
            )
            .add_url(
                'api_url', 'Custom API URL',
                required=False,
                placeholder='https://qualysapi.example.com',
                description='Only required if Platform is set to Custom URL',
            )
            .add_string(
                'username', 'Username',
                required=True,
                placeholder='api_user',
                description='Qualys API username (service account recommended)',
            )
            .add_password(
                'password', 'Password',
                required=True,
                description='Qualys API password (will be encrypted)',
            )
            .add_number(
                'truncation_limit', 'Page Size',
                required=False,
                default=10000,
                min_val=100,
                max_val=100000,
                description='Number of host records per API request (max 10000 recommended)',
            )
            .add_boolean(
                'include_cloud_metadata', 'Include Cloud Metadata',
                default=True,
                description='Fetch EC2/Azure/GCP cloud instance metadata',
            )
            .add_boolean(
                'include_tags', 'Include Asset Tags',
                default=True,
                description='Include Qualys asset tags in the output',
            )
            .build()
        )

    # ------------------------------------------------------------------
    # test_connection
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        try:
            self.logger.info(f"Testing connection to Qualys ({self.platform})...")

            # Fetch 1 host to validate credentials and count
            response = self.auth.request(
                '/api/2.0/fo/asset/host/',
                params={
                    'action': 'list',
                    'truncation_limit': '1',
                },
                timeout=API_TIMEOUT,
            )

            try:
                root = ET.fromstring(response.content)
            except ET.ParseError:
                return {
                    'success': False,
                    'message': 'Qualys returned an unexpected non-XML response',
                    'error': response.text[:500],
                }

            # Check for API error response — Qualys wraps errors in <SIMPLE_RETURN>
            # The root tag may be SIMPLE_RETURN itself (error) or HOST_LIST_OUTPUT (success).
            if root.tag == 'SIMPLE_RETURN':
                error = root.find('RESPONSE')
            else:
                error = root.find('.//SIMPLE_RETURN/RESPONSE')
            if error is not None:
                error_text = error.findtext('TEXT', 'Unknown error')
                return {
                    'success': False,
                    'message': f'Qualys API error: {error_text}',
                    'error': error_text,
                }

            # Count hosts in response (may be 0 or 1)
            host_list = root.find('.//HOST_LIST')
            host_count = len(host_list.findall('HOST')) if host_list is not None else 0

            # Check for truncation warning (indicates more hosts exist)
            warning = root.find('.//WARNING')
            if warning is not None:
                warning_text = warning.findtext('TEXT', '')
                if any(ind in warning_text.lower() for ind in PAGINATION_INDICATORS):
                    message = 'Successfully connected to Qualys. Hosts available for collection.'
                else:
                    message = f'Successfully connected to Qualys. Found {host_count} host(s).'
            else:
                message = f'Successfully connected to Qualys. Found {host_count} host(s).'

            return {
                'success': True,
                'message': message,
                'metadata': {
                    'platform': self.platform,
                    'base_url': self.base_url,
                    'api_endpoint': '/api/2.0/fo/asset/host/',
                },
            }

        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return {
                'success': False,
                'message': f'Connection failed: {str(e)}',
                'error': str(e),
            }

    # ------------------------------------------------------------------
    # _collect_records
    # ------------------------------------------------------------------

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        self.logger.info(f"Starting Qualys asset fetch (batch: {batch_id})")

        paginator = LinkPaginator(max_pages=MAX_PAGINATION_PAGES)

        for page_records in paginator.pages(self._fetch_page):
            yield from page_records

        self.logger.info("Completed Qualys asset fetch")

    # ------------------------------------------------------------------
    # Internal: page fetching with rate limits
    # ------------------------------------------------------------------

    def _fetch_page(self, url: Optional[str]) -> tuple:
        """Fetch one page of hosts. Returns (records, next_url)."""
        if url:
            # Pagination request — use full URL directly via rate-limited GET
            self.logger.info("Fetching host page (pagination)...")
            response = self._rate_limited_request(url)
        else:
            # Initial request with parameters
            self.logger.info("Fetching host page (initial)...")
            params = {
                'action': 'list',
                'details': 'All/AGs',
                'show_asset_id': '1',
                'truncation_limit': str(self.truncation_limit),
            }
            if self.include_tags:
                params['show_tags'] = '1'
            if self.include_cloud_metadata:
                params['host_metadata'] = 'all'

            response = self.auth.request(
                '/api/2.0/fo/asset/host/',
                params=params,
                timeout=API_TIMEOUT,
            )
            self._update_rate_limits(response)

        root = ET.fromstring(response.content)

        records = []
        host_list = root.find('.//HOST_LIST')
        if host_list is not None:
            for host_element in host_list.findall('HOST'):
                try:
                    records.append(self._parse_host_element(host_element))
                except Exception as e:
                    self.logger.error(f"Error parsing host element: {e}")

        next_url = self._extract_pagination_url(root)
        return records, next_url

    def _rate_limited_request(self, url: str) -> 'requests.Response':
        """Make a request to a full URL with rate-limit tracking and retry."""
        max_retries = 3

        for attempt in range(max_retries):
            # Proactive rate limit wait
            if self._rate_limit_remaining <= RATE_LIMIT_THRESHOLD:
                wait_time = self._rate_limit_to_wait or DEFAULT_RATE_LIMIT_WAIT
                self.logger.warning(
                    f"Approaching rate limit ({self._rate_limit_remaining} remaining). "
                    f"Waiting {wait_time}s..."
                )
                time.sleep(wait_time)

            response = self.auth.session.get(url, timeout=API_TIMEOUT)
            self._update_rate_limits(response)

            if response.status_code == 409:
                # Distinguish rate limit (1965) vs concurrency limit (1960)
                error_code = ''
                try:
                    error_root = ET.fromstring(response.content)
                    error_code = error_root.findtext('.//RESPONSE/CODE', '')
                except ET.ParseError:
                    pass

                if error_code == '1960':
                    wait_time = 10  # concurrency limit — short fixed wait
                else:
                    wait_time = self._rate_limit_to_wait or DEFAULT_RATE_LIMIT_WAIT

                self.logger.warning(
                    f"Qualys 409 (code {error_code or 'unknown'}). "
                    f"Waiting {wait_time}s (attempt {attempt + 1}/{max_retries})..."
                )
                time.sleep(wait_time)
                continue

            response.raise_for_status()
            return response

        raise requests.HTTPError(
            f"Qualys API returned {response.status_code} after {max_retries} retries",
            response=response,
        )

    def _update_rate_limits(self, response) -> None:
        """Read rate limit headers from response and update internal state."""
        headers = response.headers
        try:
            if 'X-RateLimit-Remaining' in headers:
                self._rate_limit_remaining = int(headers['X-RateLimit-Remaining'])
            if 'X-RateLimit-ToWait-Sec' in headers:
                self._rate_limit_to_wait = int(headers['X-RateLimit-ToWait-Sec'])
        except (ValueError, TypeError):
            pass

    def _extract_pagination_url(self, root: ET.Element) -> Optional[str]:
        """Extract pagination URL from XML <WARNING> element, if present."""
        warning = root.find('.//WARNING')
        if warning is None:
            return None

        warning_text = warning.findtext('TEXT', '')
        warning_url = warning.findtext('URL')

        has_pagination = any(
            indicator in warning_text.lower()
            for indicator in PAGINATION_INDICATORS
        )

        if has_pagination and warning_url:
            return warning_url

        return None

    # ------------------------------------------------------------------
    # Internal: XML → dict parsing
    # ------------------------------------------------------------------

    def _parse_host_element(self, element: ET.Element) -> Dict[str, Any]:
        """Parse a single <HOST> XML element into a flat dictionary."""
        record = {
            'id': element.findtext('ID', ''),
            'asset_id': element.findtext('ASSET_ID'),
            'ip_address': element.findtext('IP', ''),
            'hostname': self._get_dns_hostname(element),
            'dns_name': element.findtext('DNS'),
            'netbios_name': element.findtext('NETBIOS'),
            'operating_system': element.findtext('OS'),
            'tracking_method': element.findtext('TRACKING_METHOD'),
            'serial_number': element.findtext('SERIAL_NUMBER'),
            'hardware_uuid': element.findtext('HARDWARE_UUID'),
            'qualys_host_id': element.findtext('QG_HOSTID'),
            'agent_status': element.findtext('AGENT_STATUS'),
            'cloud_provider': element.findtext('CLOUD_AGENT_RUNNING_ON'),
        }

        # DNS data (nested)
        dns_data = element.find('DNS_DATA')
        if dns_data is not None:
            record['fqdn'] = dns_data.findtext('FQDN')
            record['domain'] = dns_data.findtext('DOMAIN')
            # Use DNS_DATA hostname if top-level hostname is empty
            if not record['hostname']:
                record['hostname'] = dns_data.findtext('HOSTNAME')
        else:
            record['fqdn'] = None
            record['domain'] = None

        # Timestamps
        record['last_scan_datetime'] = self._parse_datetime(element.findtext('LAST_SCAN_DATETIME'))
        record['last_vm_scanned_date'] = self._parse_datetime(element.findtext('LAST_VM_SCANNED_DATE'))
        record['last_boot'] = self._parse_datetime(element.findtext('LAST_BOOT'))
        record['first_found_date'] = self._parse_datetime(element.findtext('FIRST_FOUND_DATE'))
        record['last_activity'] = self._parse_datetime(element.findtext('LAST_ACTIVITY'))

        # Asset groups (comma-separated IDs)
        ag_text = element.findtext('ASSET_GROUP_IDS', '')
        record['asset_groups'] = [g.strip() for g in ag_text.split(',') if g.strip()] if ag_text else []

        # Tags (nested <TAGS><TAG><NAME>)
        tags_el = element.find('TAGS')
        if tags_el is not None:
            record['tags'] = [t.findtext('NAME', '') for t in tags_el.findall('TAG') if t.findtext('NAME', '')]
        else:
            record['tags'] = []

        # Scores (defensive — Qualys may return non-numeric values)
        trurisk = element.findtext('TRURISK_SCORE')
        try:
            record['trurisk_score'] = int(trurisk) if trurisk else None
        except (ValueError, TypeError):
            record['trurisk_score'] = None
        criticality = element.findtext('ASSET_CRITICALITY_SCORE')
        try:
            record['criticality_score'] = int(criticality) if criticality else None
        except (ValueError, TypeError):
            record['criticality_score'] = None

        # Cloud metadata (nested <METADATA><EC2|AZURE|GCP>)
        record['cloud_metadata'] = self._parse_cloud_metadata(element)

        return record

    @staticmethod
    def _get_dns_hostname(element: ET.Element) -> Optional[str]:
        """Extract hostname, preferring DNS_DATA over top-level HOSTNAME."""
        dns_data = element.find('DNS_DATA')
        if dns_data is not None:
            hostname = dns_data.findtext('HOSTNAME')
            if hostname:
                return hostname
        return element.findtext('HOSTNAME')

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> Optional[str]:
        """Parse a Qualys datetime string and return ISO format, or None."""
        if not value:
            return None
        try:
            dt = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
            return dt.isoformat()
        except (ValueError, TypeError):
            return value  # return raw string if parse fails

    @staticmethod
    def _parse_cloud_metadata(element: ET.Element) -> Optional[Dict[str, Any]]:
        """Parse cloud metadata from <METADATA> element into a flat dict."""
        metadata_el = element.find('METADATA')
        if metadata_el is None:
            return None

        cloud_data = {}
        for provider_el in metadata_el:
            provider_name = provider_el.tag  # EC2, AZURE, GCP
            provider_data = {}
            for attr_el in provider_el.findall('ATTRIBUTE'):
                name = attr_el.findtext('NAME', '')
                value = attr_el.findtext('VALUE', '')
                if name:
                    provider_data[name] = value
            if provider_data:
                cloud_data[provider_name] = provider_data

        return cloud_data if cloud_data else None

    # ------------------------------------------------------------------
    # get_schema
    # ------------------------------------------------------------------

    def get_schema(self) -> Dict[str, Any]:
        fields = [
            {'name': 'id', 'path': 'id', 'type': 'string', 'description': 'Qualys host ID', 'required': True},
            {'name': 'asset_id', 'path': 'asset_id', 'type': 'string', 'description': 'Qualys asset ID'},
            {'name': 'ip_address', 'path': 'ip_address', 'type': 'string', 'description': 'IP address'},
            {'name': 'hostname', 'path': 'hostname', 'type': 'string', 'description': 'Hostname'},
            {'name': 'dns_name', 'path': 'dns_name', 'type': 'string', 'description': 'DNS name'},
            {'name': 'fqdn', 'path': 'fqdn', 'type': 'string', 'description': 'Fully qualified domain name'},
            {'name': 'domain', 'path': 'domain', 'type': 'string', 'description': 'Domain name'},
            {'name': 'netbios_name', 'path': 'netbios_name', 'type': 'string', 'description': 'NetBIOS name'},
            {'name': 'operating_system', 'path': 'operating_system', 'type': 'string', 'description': 'Operating system'},
            {'name': 'tracking_method', 'path': 'tracking_method', 'type': 'string', 'description': 'Host tracking method (IP, Agent, DNS, NetBIOS)'},
            {'name': 'serial_number', 'path': 'serial_number', 'type': 'string', 'description': 'Hardware serial number'},
            {'name': 'hardware_uuid', 'path': 'hardware_uuid', 'type': 'string', 'description': 'Hardware UUID'},
            {'name': 'qualys_host_id', 'path': 'qualys_host_id', 'type': 'string', 'description': 'Cloud Agent host ID'},
            {'name': 'last_scan_datetime', 'path': 'last_scan_datetime', 'type': 'datetime', 'description': 'Last vulnerability scan'},
            {'name': 'last_vm_scanned_date', 'path': 'last_vm_scanned_date', 'type': 'datetime', 'description': 'Last VM scan date'},
            {'name': 'last_boot', 'path': 'last_boot', 'type': 'datetime', 'description': 'Last boot time'},
            {'name': 'first_found_date', 'path': 'first_found_date', 'type': 'datetime', 'description': 'First discovery date'},
            {'name': 'last_activity', 'path': 'last_activity', 'type': 'datetime', 'description': 'Last activity'},
            {'name': 'agent_status', 'path': 'agent_status', 'type': 'string', 'description': 'Cloud Agent status'},
            {'name': 'cloud_provider', 'path': 'cloud_provider', 'type': 'string', 'description': 'Cloud provider'},
            {'name': 'asset_groups', 'path': 'asset_groups', 'type': 'object', 'description': 'Asset group memberships'},
            {'name': 'tags', 'path': 'tags', 'type': 'object', 'description': 'Qualys asset tags'},
            {'name': 'trurisk_score', 'path': 'trurisk_score', 'type': 'number', 'description': 'TruRisk score (0-1000)'},
            {'name': 'criticality_score', 'path': 'criticality_score', 'type': 'number', 'description': 'Asset criticality score (1-5)'},
            {'name': 'cloud_metadata', 'path': 'cloud_metadata', 'type': 'object', 'description': 'Cloud instance metadata (EC2/Azure/GCP)'},
        ]
        return {
            'source': self.integration_type,
            'version': '1.0',
            'fields': fields,
            'sample_record_count': 3,
        }
