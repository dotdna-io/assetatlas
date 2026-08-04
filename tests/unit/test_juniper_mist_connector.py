# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for Juniper Mist connector."""

from unittest.mock import MagicMock, patch
import pytest
import requests

from src.modules.integrations.connectors.juniper_mist_connector import (
    JuniperMistConnector,
    MIST_REGIONS,
    MAX_PAGINATION_PAGES,
    DEFAULT_PAGE_SIZE,
    DEFAULT_RATE_LIMIT_WAIT,
    DEFAULT_CLIENT_DURATION,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    """Build a valid Mist connector config dict."""
    base = {
        'api_token': 'test-token-abc123',
        'org_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        'region': 'global',
        'collect_devices': True,
        'collect_clients': True,
        'page_size': 100,
    }
    base.update(overrides)
    return base


@pytest.fixture
def config():
    return _make_config()


@pytest.fixture
def connector(config):
    return JuniperMistConnector(config)


# ---------------------------------------------------------------------------
# TestConfigSchema
# ---------------------------------------------------------------------------

class TestConfigSchema:
    def test_config_schema_has_required_fields(self):
        schema = JuniperMistConnector.get_config_schema()
        assert 'api_token' in schema
        assert 'org_id' in schema
        assert 'region' in schema

    def test_api_token_is_sensitive(self):
        schema = JuniperMistConnector.get_config_schema()
        assert schema['api_token']['sensitive'] is True

    def test_org_id_is_optional(self):
        schema = JuniperMistConnector.get_config_schema()
        assert schema['org_id']['required'] is False

    def test_region_has_global_and_eu_options(self):
        schema = JuniperMistConnector.get_config_schema()
        values = [o['value'] for o in schema['region']['options']]
        assert 'global' in values
        assert 'eu' in values

    def test_page_size_has_validation(self):
        schema = JuniperMistConnector.get_config_schema()
        assert schema['page_size']['validation']['min'] > 0
        assert schema['page_size']['validation']['max'] <= 1000

    def test_collect_toggles_default_true(self):
        schema = JuniperMistConnector.get_config_schema()
        assert schema['collect_devices']['default'] is True
        assert schema['collect_clients']['default'] is True

    def test_client_duration_has_options(self):
        schema = JuniperMistConnector.get_config_schema()
        assert 'client_duration' in schema
        values = [o['value'] for o in schema['client_duration']['options']]
        assert '7d' in values
        assert '30d' in values


# ---------------------------------------------------------------------------
# TestConstructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_valid_config_creates_instance(self, connector):
        assert connector.integration_type == 'juniper_mist'
        assert connector.org_id == 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

    def test_base_url_resolved_from_region(self, connector):
        assert connector.base_url == MIST_REGIONS['global']

    def test_eu_region_resolves_eu_url(self):
        c = JuniperMistConnector(_make_config(region='eu'))
        assert c.base_url == MIST_REGIONS['eu']

    def test_missing_api_token_raises(self):
        with pytest.raises(ValueError, match='API token'):
            JuniperMistConnector(_make_config(api_token=''))

    def test_empty_org_id_allowed(self):
        c = JuniperMistConnector(_make_config(org_id=''))
        assert c.org_id == ''

    def test_no_org_id_key_allowed(self):
        cfg = _make_config()
        del cfg['org_id']
        c = JuniperMistConnector(cfg)
        assert c.org_id == ''

    def test_page_size_clamped_to_max(self):
        c = JuniperMistConnector(_make_config(page_size=5000))
        assert c.page_size <= 1000

    def test_default_page_size(self):
        c = JuniperMistConnector(_make_config(page_size=None))
        assert c.page_size == DEFAULT_PAGE_SIZE


# ---------------------------------------------------------------------------
# TestRequest
# ---------------------------------------------------------------------------

class TestRequest:
    def test_request_sends_token_header(self, connector):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'data': []}
        mock_resp.headers = {}
        connector.session.request = MagicMock(return_value=mock_resp)

        connector._request('GET', '/test')

        call_kwargs = connector.session.request.call_args
        assert call_kwargs[1]['headers']['Authorization'] == 'Token test-token-abc123'

    def test_request_uses_correct_base_url(self, connector):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_resp.headers = {}
        connector.session.request = MagicMock(return_value=mock_resp)

        connector._request('GET', '/orgs/test')

        call_args = connector.session.request.call_args
        assert call_args[0][1] == f'{MIST_REGIONS["global"]}/orgs/test'

    def test_request_raises_on_http_error(self, connector):
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 500
        mock_resp.headers = {}
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        connector.session.request = MagicMock(return_value=mock_resp)

        with pytest.raises(requests.HTTPError):
            connector._request('GET', '/test')

    def test_rate_limit_tracked_from_headers(self, connector):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_resp.headers = {'X-RateLimit-Remaining': '42'}
        connector.session.request = MagicMock(return_value=mock_resp)

        connector._request('GET', '/test')

        assert connector._rate_limit_remaining == 42

    @patch('time.sleep')
    def test_rate_limit_triggers_wait(self, mock_sleep, connector):
        connector._rate_limit_remaining = 5  # below threshold

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_resp.headers = {}
        connector.session.request = MagicMock(return_value=mock_resp)

        connector._request('GET', '/test')

        mock_sleep.assert_called_once_with(DEFAULT_RATE_LIMIT_WAIT)


# ---------------------------------------------------------------------------
# TestOrgDiscovery
# ---------------------------------------------------------------------------

class TestOrgDiscovery:
    def test_discover_orgs_user_token(self, connector):
        """User-level token: /self/orgs returns org list directly."""
        orgs = [
            {'id': 'org-1', 'name': 'Org One'},
            {'id': 'org-2', 'name': 'Org Two'},
        ]
        connector._request = MagicMock(return_value=orgs)

        result = connector._discover_orgs()

        assert len(result) == 2
        connector._request.assert_called_once_with('GET', '/self/orgs')

    def test_discover_orgs_org_token_fallback(self, connector):
        """Org-level token: /self/orgs 404s, falls back to /self privileges."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        http_error = requests.HTTPError(response=mock_response)

        self_data = {
            'privileges': [
                {'scope': 'org', 'org_id': 'org-1', 'org_name': 'Org One'},
                {'scope': 'org', 'org_id': 'org-2', 'org_name': 'Org Two'},
            ]
        }
        connector._request = MagicMock(side_effect=[http_error, self_data])

        result = connector._discover_orgs()

        assert len(result) == 2
        assert result[0] == {'id': 'org-1', 'name': 'Org One'}
        assert connector._request.call_count == 2

    def test_discover_orgs_org_token_deduplicates(self, connector):
        """Duplicate org_ids in privileges are deduplicated."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        http_error = requests.HTTPError(response=mock_response)

        self_data = {
            'privileges': [
                {'scope': 'org', 'org_id': 'org-1', 'org_name': 'Org One'},
                {'scope': 'org', 'org_id': 'org-1', 'org_name': 'Org One'},
            ]
        }
        connector._request = MagicMock(side_effect=[http_error, self_data])

        result = connector._discover_orgs()

        assert len(result) == 1

    def test_discover_orgs_returns_empty_on_non_list(self, connector):
        connector._request = MagicMock(return_value={'error': 'nope'})

        result = connector._discover_orgs()

        assert result == []

    def test_get_org_ids_with_explicit_org_id(self, connector):
        result = connector._get_org_ids()

        assert result == ['aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee']

    def test_get_org_ids_discovers_when_no_org_id(self):
        c = JuniperMistConnector(_make_config(org_id=''))
        c._discover_orgs = MagicMock(return_value=[
            {'id': 'org-1', 'name': 'Org One'},
            {'id': 'org-2', 'name': 'Org Two'},
        ])

        result = c._get_org_ids()

        assert result == ['org-1', 'org-2']
        c._discover_orgs.assert_called_once()

    def test_get_org_ids_returns_empty_when_no_orgs_discovered(self):
        c = JuniperMistConnector(_make_config(org_id=''))
        c._discover_orgs = MagicMock(return_value=[])

        result = c._get_org_ids()

        assert result == []

    def test_get_org_ids_skips_orgs_without_id(self):
        c = JuniperMistConnector(_make_config(org_id=''))
        c._discover_orgs = MagicMock(return_value=[
            {'id': 'org-1', 'name': 'Good'},
            {'name': 'No ID'},
        ])

        result = c._get_org_ids()

        assert result == ['org-1']


# ---------------------------------------------------------------------------
# TestConnectionTest
# ---------------------------------------------------------------------------

class TestConnectionTest:
    def test_success_with_org_id_returns_org_name(self, connector):
        connector._request = MagicMock(return_value={
            'name': 'Acme Corp',
            'id': connector.org_id,
        })

        result = connector.test_connection()

        assert result['success'] is True
        assert 'Acme Corp' in result['message']
        connector._request.assert_called_once_with(
            'GET', f'/orgs/{connector.org_id}'
        )

    def test_success_without_org_id_discovers_orgs(self):
        c = JuniperMistConnector(_make_config(org_id=''))
        c._discover_orgs = MagicMock(return_value=[
            {'id': 'org-1', 'name': 'Org One'},
            {'id': 'org-2', 'name': 'Org Two'},
        ])

        result = c.test_connection()

        assert result['success'] is True
        assert '2 org' in result['message']
        assert result['metadata']['org_count'] == 2

    def test_no_orgs_discovered_returns_failure(self):
        c = JuniperMistConnector(_make_config(org_id=''))
        c._discover_orgs = MagicMock(return_value=[])

        result = c.test_connection()

        assert result['success'] is False
        assert 'no organizations' in result['message'].lower()

    def test_401_returns_auth_failure(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        connector._request = MagicMock(
            side_effect=requests.HTTPError(response=mock_resp)
        )

        result = connector.test_connection()

        assert result['success'] is False
        assert 'Invalid' in result['message'] or 'token' in result['message'].lower()

    def test_403_returns_permission_error(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        connector._request = MagicMock(
            side_effect=requests.HTTPError(response=mock_resp)
        )

        result = connector.test_connection()

        assert result['success'] is False
        assert 'permission' in result['message'].lower()

    def test_404_returns_org_not_found(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        connector._request = MagicMock(
            side_effect=requests.HTTPError(response=mock_resp)
        )

        result = connector.test_connection()

        assert result['success'] is False
        assert 'not found' in result['message'].lower()

    def test_generic_exception_returns_failure(self, connector):
        connector._request = MagicMock(
            side_effect=ConnectionError('timeout')
        )

        result = connector.test_connection()

        assert result['success'] is False


# ---------------------------------------------------------------------------
# TestDeviceCollection
# ---------------------------------------------------------------------------

class TestDeviceCollection:
    def test_fetch_inventory_paginates(self, connector):
        page1 = [
            {'id': 'd1', 'serial': 'SN1', 'model': 'AP45', 'mac': 'aa:bb:cc:00:00:01', 'type': 'ap', 'site_id': 's1'},
            {'id': 'd2', 'serial': 'SN2', 'model': 'EX4100', 'mac': 'aa:bb:cc:00:00:02', 'type': 'switch', 'site_id': 's1'},
        ]
        page2 = [
            {'id': 'd3', 'serial': 'SN3', 'model': 'SRX300', 'mac': 'aa:bb:cc:00:00:03', 'type': 'gateway', 'site_id': 's2'},
        ]
        connector.page_size = 2
        connector._request = MagicMock(side_effect=[page1, page2])

        result = list(connector._fetch_inventory('org-1'))

        assert len(result) == 3
        assert connector._request.call_count == 2

    def test_fetch_inventory_stops_on_empty(self, connector):
        connector._request = MagicMock(side_effect=[[{'id': 'd1'}], []])

        result = list(connector._fetch_inventory('org-1'))

        assert len(result) == 1

    def test_fetch_device_stats_paginates(self, connector):
        page1 = [
            {'id': 'd1', 'name': 'AP-Lobby', 'ip': '10.0.1.1', 'status': 'connected', 'last_seen': 1710000000},
        ]
        connector._request = MagicMock(side_effect=[page1, []])

        result = list(connector._fetch_device_stats('org-1'))

        assert len(result) == 1
        assert result[0]['name'] == 'AP-Lobby'

    def test_fetch_devices_merges_inventory_and_stats(self, connector):
        inventory = [
            {'id': 'd1', 'serial': 'SN1', 'model': 'AP45', 'mac': 'aa:bb:cc:00:00:01', 'type': 'ap', 'site_id': 's1'},
            {'id': 'd2', 'serial': 'SN2', 'model': 'EX4100', 'mac': 'aa:bb:cc:00:00:02', 'type': 'switch', 'site_id': 's1'},
        ]
        stats = [
            {'id': 'd1', 'name': 'AP-Lobby', 'ip': '10.0.1.1', 'status': 'connected', 'mac': 'aa:bb:cc:00:00:01', 'last_seen': 1710000000, 'uptime': 86400, 'site_name': 'HQ', 'num_clients': 5, 'version': '0.14'},
        ]
        connector._fetch_inventory = MagicMock(return_value=iter(inventory))
        connector._fetch_device_stats = MagicMock(return_value=iter(stats))

        result = list(connector._fetch_devices('org-1'))

        # d1 merged: has both inventory (serial, model) and stats (ip, status)
        d1 = next(r for r in result if r['id'] == 'd1')
        assert d1['serial'] == 'SN1'
        assert d1['ip'] == '10.0.1.1'
        assert d1['status'] == 'connected'
        assert d1['_record_type'] == 'device'

        # d2 inventory-only: no stats match
        d2 = next(r for r in result if r['id'] == 'd2')
        assert d2['serial'] == 'SN2'
        assert d2['_record_type'] == 'device'

    def test_fetch_devices_stats_only_device_still_yielded(self, connector):
        """Device in stats but not inventory — yield stats-only."""
        inventory = []
        stats = [
            {'id': 'd9', 'name': 'Unknown-AP', 'ip': '10.0.1.9', 'status': 'connected', 'mac': 'ff:ff:ff:00:00:09', 'last_seen': 1710000000, 'uptime': 100, 'site_name': 'Branch', 'num_clients': 0, 'version': '0.14'},
        ]
        connector._fetch_inventory = MagicMock(return_value=iter(inventory))
        connector._fetch_device_stats = MagicMock(return_value=iter(stats))

        result = list(connector._fetch_devices('org-1'))

        assert len(result) == 1
        assert result[0]['id'] == 'd9'
        assert result[0]['_record_type'] == 'device'

    def test_pagination_respects_max_pages(self, connector):
        """Safety cap prevents infinite loops."""
        connector.page_size = 1
        # Return 1 result per page forever
        connector._request = MagicMock(return_value=[{'id': 'dx'}])

        list(connector._fetch_inventory('org-1'))

        assert connector._request.call_count == MAX_PAGINATION_PAGES


# ---------------------------------------------------------------------------
# TestClientCollection
# ---------------------------------------------------------------------------

class TestClientCollection:
    def test_fetch_sites_returns_site_list(self, connector):
        sites = [
            {'id': 's1', 'name': 'HQ'},
            {'id': 's2', 'name': 'Branch'},
        ]
        connector._request = MagicMock(return_value=sites)

        result = list(connector._fetch_sites('org-1'))

        assert len(result) == 2
        assert result[0]['name'] == 'HQ'

    def test_fetch_clients_iterates_all_sites(self, connector):
        sites = [
            {'id': 's1', 'name': 'HQ'},
            {'id': 's2', 'name': 'Branch'},
        ]
        clients_s1 = [
            {'mac': 'cc:00:00:00:00:01', 'hostname': 'laptop1', 'ip': '10.0.1.10', 'ssid': 'Corp', 'last_seen': 1710000000},
        ]
        clients_s2 = [
            {'mac': 'cc:00:00:00:00:02', 'hostname': 'laptop2', 'ip': '10.0.2.10', 'ssid': 'Guest', 'last_seen': 1710000000},
        ]
        connector._fetch_sites = MagicMock(return_value=iter(sites))

        def fake_paginate(path, params=None):
            if 's1' in path:
                return iter(clients_s1)
            elif 's2' in path:
                return iter(clients_s2)
            return iter([])

        connector._paginate = MagicMock(side_effect=fake_paginate)

        result = list(connector._fetch_clients('org-1'))

        assert len(result) == 2
        assert all(r['_record_type'] == 'client' for r in result)
        # site_name injected
        assert result[0]['site_name'] == 'HQ'
        assert result[1]['site_name'] == 'Branch'

    def test_fetch_clients_includes_name_from_hostname(self, connector):
        """Client records include 'name' field mirroring 'hostname' for field mapping."""
        sites = [{'id': 's1', 'name': 'HQ'}]
        clients = [
            {'mac': 'cc:00:00:00:00:01', 'hostname': 'laptop1', 'ip': '10.0.1.10', 'ssid': 'Corp', 'last_seen': 1710000000},
        ]
        connector._fetch_sites = MagicMock(return_value=iter(sites))
        connector._paginate = MagicMock(return_value=iter(clients))

        result = list(connector._fetch_clients('org-1'))

        assert result[0]['name'] == 'laptop1'
        assert result[0]['hostname'] == 'laptop1'

    def test_fetch_clients_uses_default_duration(self, connector):
        connector._fetch_sites = MagicMock(return_value=iter([{'id': 's1', 'name': 'HQ'}]))
        connector._paginate = MagicMock(return_value=iter([]))

        list(connector._fetch_clients('org-1'))

        call_args = connector._paginate.call_args
        assert call_args[1]['params']['duration'] == DEFAULT_CLIENT_DURATION

    def test_fetch_clients_uses_custom_duration(self):
        c = JuniperMistConnector(_make_config(client_duration='30d'))
        c._fetch_sites = MagicMock(return_value=iter([{'id': 's1', 'name': 'HQ'}]))
        c._paginate = MagicMock(return_value=iter([]))

        list(c._fetch_clients('org-1'))

        call_args = c._paginate.call_args
        assert call_args[1]['params']['duration'] == '30d'

    def test_fetch_clients_skips_empty_sites(self, connector):
        sites = [{'id': 's1', 'name': 'Empty Site'}]
        connector._fetch_sites = MagicMock(return_value=iter(sites))
        connector._paginate = MagicMock(return_value=iter([]))

        result = list(connector._fetch_clients('org-1'))

        assert len(result) == 0

    def test_zero_sites_yields_nothing(self, connector):
        connector._fetch_sites = MagicMock(return_value=iter([]))

        result = list(connector._fetch_clients('org-1'))

        assert len(result) == 0

    def test_zero_sites_logs_warning(self, connector):
        connector._fetch_sites = MagicMock(return_value=iter([]))
        connector.logger = MagicMock()

        list(connector._fetch_clients('org-1'))

        connector.logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# TestFetchData
# ---------------------------------------------------------------------------

class TestFetchData:
    def test_yields_devices_and_clients(self, connector):
        devices = [
            {'id': 'd1', 'name': 'AP1', '_record_type': 'device'},
        ]
        clients = [
            {'mac': 'cc:00:00:00:00:01', 'hostname': 'laptop1', '_record_type': 'client'},
        ]
        connector._fetch_devices = MagicMock(return_value=iter(devices))
        connector._fetch_clients = MagicMock(return_value=iter(clients))
        connector._should_include_record = MagicMock(return_value=True)

        result = list(connector.fetch_data('batch-001'))

        assert len(result) == 2
        assert result[0]['_record_type'] == 'device'
        assert result[1]['_record_type'] == 'client'

    def test_adds_metadata_to_records(self, connector):
        connector._fetch_devices = MagicMock(return_value=iter([
            {'id': 'd1', '_record_type': 'device'},
        ]))
        connector._fetch_clients = MagicMock(return_value=iter([]))
        connector._should_include_record = MagicMock(return_value=True)

        result = list(connector.fetch_data('batch-001'))

        assert result[0]['_batch_id'] == 'batch-001'
        assert result[0]['_source'] == 'juniper_mist'
        assert '_collected_at' in result[0]

    def test_skips_devices_when_disabled(self):
        c = JuniperMistConnector({
            'api_token': 'test-token-abc123',
            'org_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
            'region': 'global',
            'collect_devices': False,
            'collect_clients': True,
            'page_size': 100,
        })
        c._fetch_devices = MagicMock()
        c._fetch_clients = MagicMock(return_value=iter([]))
        c._should_include_record = MagicMock(return_value=True)

        list(c.fetch_data('batch-001'))

        c._fetch_devices.assert_not_called()

    def test_skips_clients_when_disabled(self):
        c = JuniperMistConnector({
            'api_token': 'test-token-abc123',
            'org_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
            'region': 'global',
            'collect_devices': True,
            'collect_clients': False,
            'page_size': 100,
        })
        c._fetch_devices = MagicMock(return_value=iter([]))
        c._fetch_clients = MagicMock()
        c._should_include_record = MagicMock(return_value=True)

        list(c.fetch_data('batch-001'))

        c._fetch_clients.assert_not_called()

    def test_respects_data_filters(self, connector):
        connector._fetch_devices = MagicMock(return_value=iter([
            {'id': 'd1', '_record_type': 'device'},
            {'id': 'd2', '_record_type': 'device'},
        ]))
        connector._fetch_clients = MagicMock(return_value=iter([]))
        connector._should_include_record = MagicMock(side_effect=[True, False])

        result = list(connector.fetch_data('batch-001'))

        assert len(result) == 1

    def test_logs_execution_and_metrics(self, connector):
        connector._fetch_devices = MagicMock(return_value=iter([
            {'id': 'd1', '_record_type': 'device'},
        ]))
        connector._fetch_clients = MagicMock(return_value=iter([
            {'mac': 'cc:00:00:00:00:01', '_record_type': 'client'},
        ]))
        connector._should_include_record = MagicMock(return_value=True)
        connector.log_execution_start = MagicMock(return_value=1000.0)
        connector.log_execution_end = MagicMock()
        connector.log_sync_metrics = MagicMock()

        list(connector.fetch_data('batch-001'))

        connector.log_execution_start.assert_called_once()
        connector.log_execution_end.assert_called_once()
        connector.log_sync_metrics.assert_called_once()
        # Verify metrics: 2 total records
        metrics_call = connector.log_sync_metrics.call_args
        assert metrics_call[0][1] == 2  # records_fetched

    def test_exception_logs_failure(self, connector):
        connector._fetch_devices = MagicMock(side_effect=requests.HTTPError('boom'))
        connector._fetch_clients = MagicMock(return_value=iter([]))
        connector.log_execution_start = MagicMock(return_value=1000.0)
        connector.log_execution_end = MagicMock()

        with pytest.raises(requests.HTTPError):
            list(connector.fetch_data('batch-001'))

        connector.log_execution_end.assert_called_once()
        assert connector.log_execution_end.call_args[1].get('success') is False \
            or connector.log_execution_end.call_args[0][2] is False

    def test_multi_org_fetch_collects_from_all_orgs(self):
        """When org_id is empty, fetch_data iterates all discovered orgs."""
        c = JuniperMistConnector(_make_config(org_id=''))
        c._discover_orgs = MagicMock(return_value=[
            {'id': 'org-1', 'name': 'Org One'},
            {'id': 'org-2', 'name': 'Org Two'},
        ])

        devices_org1 = [{'id': 'd1', '_record_type': 'device'}]
        devices_org2 = [{'id': 'd2', '_record_type': 'device'}]

        def fake_fetch_devices(org_id):
            if org_id == 'org-1':
                return iter(devices_org1)
            return iter(devices_org2)

        c._fetch_devices = MagicMock(side_effect=fake_fetch_devices)
        c._fetch_clients = MagicMock(return_value=iter([]))
        c._should_include_record = MagicMock(return_value=True)
        c.collect_clients = False

        result = list(c.fetch_data('batch-001'))

        assert len(result) == 2
        assert c._fetch_devices.call_count == 2
        c._fetch_devices.assert_any_call('org-1')
        c._fetch_devices.assert_any_call('org-2')

    def test_no_orgs_discovered_yields_nothing(self):
        c = JuniperMistConnector(_make_config(org_id=''))
        c._discover_orgs = MagicMock(return_value=[])
        c._fetch_devices = MagicMock()
        c._fetch_clients = MagicMock()

        result = list(c.fetch_data('batch-001'))

        assert len(result) == 0
        c._fetch_devices.assert_not_called()
        c._fetch_clients.assert_not_called()


# ---------------------------------------------------------------------------
# TestSchema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_schema_has_required_structure(self, connector):
        schema = connector.get_schema()
        assert 'fields' in schema
        assert 'source' in schema
        assert schema['source'] == 'juniper_mist'

    def test_schema_includes_device_fields(self, connector):
        schema = connector.get_schema()
        names = {f['name'] for f in schema['fields']}
        for field in ['id', 'name', 'type', 'model', 'serial', 'mac', 'ip', 'version', 'status', 'last_seen']:
            assert field in names, f'Missing device field: {field}'

    def test_schema_includes_client_fields(self, connector):
        schema = connector.get_schema()
        names = {f['name'] for f in schema['fields']}
        for field in ['hostname', 'ssid', 'band', 'ap_mac', 'ap_name', 'manufacture', 'os']:
            assert field in names, f'Missing client field: {field}'

    def test_each_field_has_name_path_type(self, connector):
        schema = connector.get_schema()
        for field in schema['fields']:
            assert 'name' in field
            assert 'path' in field
            assert 'type' in field
