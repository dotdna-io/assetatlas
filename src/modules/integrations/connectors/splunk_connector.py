# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Splunk Enterprise/Cloud Connector

Collects asset data from Splunk via SPL (Search Processing Language) searches.
Uses Basic Authentication and asynchronous search job workflow:
create job -> poll status -> retrieve results.

Auth: BasicAuth (username/password).
Pagination: offset-based on search job results endpoint.
"""

from typing import Dict, List, Iterator, Any, Optional
import time

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import BasicAuth, ConfigSchema


@ConnectorRegistry.register
class SplunkConnector(BaseConnector):
    """
    Splunk Enterprise/Cloud Connector

    Fetches asset data from Splunk via SPL searches.
    Uses asynchronous search job workflow: create job, poll status, retrieve results.
    """

    # Connector Metadata
    integration_type = "splunk"
    display_name = "Splunk Enterprise"
    category = "SIEM"
    description = "Collect asset data from Splunk via SPL searches"
    logo_url = "/logos/splunk.png"
    connector_role = "transport"

    # Search states
    SEARCH_STATES_RUNNING = ['QUEUED', 'PARSING', 'RUNNING', 'FINALIZING']
    SEARCH_STATES_DONE = ['DONE']
    SEARCH_STATES_FAILED = [
        'INTERNAL_CANCEL', 'USER_CANCEL', 'BAD_INPUT_CANCEL', 'QUIT', 'FAILED',
    ]

    def __init__(self, config: Dict[str, Any]):
        # Extract configuration before super().__init__
        self.base_url = config.get('url', '').rstrip('/')
        self.search_query = config.get('search_query')
        self.earliest_time = config.get('earliest_time', '-24h')
        self.latest_time = config.get('latest_time', 'now')
        self.max_count = int(config.get('max_count', 10000))
        self.poll_interval = int(config.get('poll_interval', 2))
        self.poll_timeout = int(config.get('poll_timeout', 300))
        self.verify_ssl = config.get('verify_ssl', True)
        self.page_size = int(config.get('page_size', 1000))

        if not self.base_url:
            raise ValueError("Splunk URL is required")
        if not config.get('username'):
            raise ValueError("Splunk username is required")
        if not config.get('password'):
            raise ValueError("Splunk password is required")
        if not self.search_query:
            raise ValueError("Splunk search query is required")

        super().__init__(config)

        # Configure SSL verification on the auth session
        self.auth.session.verify = self.verify_ssl
        if not self.verify_ssl:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
    # Config schema
    # ------------------------------------------------------------------

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return (
            ConfigSchema()
            .add_url(
                'url', 'Splunk URL',
                required=True,
                placeholder='https://splunk.example.com:8089',
                description='Splunk instance URL (e.g., https://splunk.example.com:8089)',
            )
            .add_string(
                'username', 'Username',
                required=True,
                placeholder='admin',
                description='Splunk username for authentication',
            )
            .add_password(
                'password', 'Password',
                required=True,
                description='Splunk password (will be encrypted)',
            )
            .add_boolean(
                'verify_ssl', 'Verify SSL Certificate',
                default=True,
                description='Verify SSL/TLS certificates (disable for self-signed certificates)',
            )
            .add_string(
                'search_query', 'SPL Search Query',
                required=True,
                placeholder='search index=assets | table host ip mac serial os',
                description='SPL query to retrieve asset data',
            )
            .add_string(
                'earliest_time', 'Earliest Time',
                required=False,
                placeholder='-24h',
                description='Search time range start (e.g., -24h, -7d, 2024-01-01)',
            )
            .add_string(
                'latest_time', 'Latest Time',
                required=False,
                placeholder='now',
                description='Search time range end (e.g., now, -1h)',
            )
            .add_number(
                'max_count', 'Max Results',
                default=10000,
                min_val=1,
                max_val=50000,
                description='Maximum number of results to retrieve',
            )
            .add_number(
                'poll_interval', 'Poll Interval (seconds)',
                default=2,
                min_val=1,
                max_val=30,
                description='Seconds between status checks while waiting for search completion',
            )
            .add_number(
                'poll_timeout', 'Poll Timeout (seconds)',
                default=300,
                min_val=30,
                max_val=3600,
                description='Maximum seconds to wait for search to complete',
            )
            .build()
        )

    # ------------------------------------------------------------------
    # test_connection
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        try:
            self.logger.info("Testing connection to Splunk...")

            test_query = "search index=* | head 1"

            search_data = {
                'search': test_query,
                'earliest_time': '-1h',
                'latest_time': 'now',
                'output_mode': 'json',
            }

            response = self.auth.request(
                '/services/search/jobs',
                method='POST',
                data=search_data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            )

            response_data = response.json()
            sid = response_data.get('sid')

            if not sid:
                return {
                    'success': False,
                    'message': 'Failed to create search job - no search ID returned',
                    'error': 'No SID in response',
                }

            time.sleep(1)

            status_response = self.auth.request(
                f'/services/search/jobs/{sid}',
                params={'output_mode': 'json'},
            )
            status_data = status_response.json()

            dispatch_state = (
                status_data.get('entry', [{}])[0]
                .get('content', {})
                .get('dispatchState', 'UNKNOWN')
            )

            return {
                'success': True,
                'message': (
                    f'Successfully connected to Splunk. '
                    f'Test search created (state: {dispatch_state}).'
                ),
                'metadata': {
                    'base_url': self.base_url,
                    'test_search_sid': sid,
                    'dispatch_state': dispatch_state,
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
    # _collect_records (replaces fetch_data)
    # ------------------------------------------------------------------

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        # Step 1: Create search job
        sid = self._create_search_job()
        self.logger.info(f"Search job created with SID: {sid}")

        # Step 2: Poll for completion
        self._wait_for_search_completion(sid)
        self.logger.info(f"Search job completed: {sid}")

        # Step 3: Retrieve results with pagination
        offset = 0
        page_size = self.page_size

        while True:
            results = self._get_search_results(sid, offset, page_size)
            if not results:
                break

            for result in results:
                result['_search_id'] = sid
                yield result

            if len(results) < page_size:
                break

            offset += page_size

    # ------------------------------------------------------------------
    # Internal: search job workflow
    # ------------------------------------------------------------------

    def _create_search_job(self) -> str:
        query = self.search_query.strip()
        if not query.startswith('search') and not query.startswith('|'):
            query = f'search {query}'

        search_data = {
            'search': query,
            'earliest_time': self.earliest_time,
            'latest_time': self.latest_time,
            'max_count': self.max_count,
            'output_mode': 'json',
        }

        response = self.auth.request(
            '/services/search/jobs',
            method='POST',
            data=search_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )

        response_data = response.json()
        sid = response_data.get('sid')

        if not sid:
            raise ValueError("Failed to create search job - no search ID returned")

        return sid

    def _wait_for_search_completion(self, sid: str):
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed > self.poll_timeout:
                raise TimeoutError(
                    f"Search job {sid} did not complete within {self.poll_timeout} seconds"
                )

            response = self.auth.request(
                f'/services/search/jobs/{sid}',
                params={'output_mode': 'json'},
            )

            status_data = response.json()
            entry = status_data.get('entry', [{}])[0]
            content = entry.get('content', {})
            dispatch_state = content.get('dispatchState', 'UNKNOWN')

            if dispatch_state in self.SEARCH_STATES_DONE:
                return

            if dispatch_state in self.SEARCH_STATES_FAILED:
                raise RuntimeError(
                    f"Search job {sid} failed with state: {dispatch_state}"
                )

            time.sleep(self.poll_interval)

    def _get_search_results(
        self, sid: str, offset: int = 0, count: int = 1000,
    ) -> List[Dict[str, Any]]:
        response = self.auth.request(
            f'/services/search/jobs/{sid}/results',
            params={
                'output_mode': 'json',
                'offset': offset,
                'count': count,
            },
        )

        data = response.json()
        return data.get('results', [])

    # ------------------------------------------------------------------
    # get_schema
    # ------------------------------------------------------------------

    def get_schema(self) -> Dict[str, Any]:
        common_fields = [
            {'name': 'host', 'path': 'host', 'type': 'string', 'description': 'Hostname', 'required': False},
            {'name': 'ip', 'path': 'ip', 'type': 'string', 'description': 'IP address', 'required': False},
            {'name': 'mac', 'path': 'mac', 'type': 'string', 'description': 'MAC address', 'required': False},
            {'name': 'serial', 'path': 'serial', 'type': 'string', 'description': 'Serial number', 'required': False},
            {'name': 'os', 'path': 'os', 'type': 'string', 'description': 'Operating system', 'required': False},
            {'name': 'os_version', 'path': 'os_version', 'type': 'string', 'description': 'OS version', 'required': False},
            {'name': 'asset_type', 'path': 'asset_type', 'type': 'string', 'description': 'Asset type', 'required': False},
            {'name': 'location', 'path': 'location', 'type': 'string', 'description': 'Location', 'required': False},
            {'name': '_time', 'path': '_time', 'type': 'datetime', 'description': 'Event timestamp', 'required': False},
            {'name': '_raw', 'path': '_raw', 'type': 'string', 'description': 'Raw event data', 'required': False},
        ]

        return {
            'source': 'splunk',
            'version': '1.0',
            'fields': common_fields,
            'sample_record_count': 3,
            'notes': 'Fields returned depend on your SPL search query. Map fields as needed.',
        }
