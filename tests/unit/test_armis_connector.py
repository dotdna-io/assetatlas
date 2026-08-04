# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Armis connector."""

import pytest
import requests
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.modules.integrations.connectors.armis_connector import (
    ArmisConnector,
    DEVICE_FIELDS,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
)


@pytest.fixture
def config():
    return {
        'tenant_url': 'https://test.armis.com',
        'api_key': 'test-secret-key-123',
        'page_size': 100,
    }


@pytest.fixture
def connector(config):
    return ArmisConnector(config)


# --- Config schema, metadata, constructor ---


class TestConfigSchema:
    def test_has_required_fields(self):
        schema = ArmisConnector.get_config_schema()
        assert 'tenant_url' in schema
        assert 'api_key' in schema
        assert 'asq_filter' in schema
        assert 'page_size' in schema

    def test_no_collection_mode_option(self):
        schema = ArmisConnector.get_config_schema()
        assert 'collection_mode' not in schema

    def test_api_key_is_sensitive(self):
        schema = ArmisConnector.get_config_schema()
        assert schema['api_key']['sensitive'] is True

    def test_page_size_validation(self):
        schema = ArmisConnector.get_config_schema()
        assert schema['page_size']['validation']['min'] == 1
        assert schema['page_size']['validation']['max'] == MAX_PAGE_SIZE

    def test_page_size_default(self):
        schema = ArmisConnector.get_config_schema()
        assert schema['page_size']['default'] == DEFAULT_PAGE_SIZE


class TestConnectorMetadata:
    def test_integration_type(self):
        assert ArmisConnector.integration_type == 'armis'

    def test_display_name(self):
        assert ArmisConnector.display_name == 'Armis Platform'

    def test_category(self):
        assert ArmisConnector.category == 'Asset Discovery'


class TestConstructor:
    def test_extracts_config_values(self, connector):
        assert connector.base_url == 'https://test.armis.com'
        assert connector.api_key == 'test-secret-key-123'
        assert connector.page_size == 100

    def test_strips_trailing_slash_from_url(self):
        c = ArmisConnector({
            'tenant_url': 'https://test.armis.com/',
            'api_key': 'key',
        })
        assert c.base_url == 'https://test.armis.com'

    def test_default_page_size(self):
        c = ArmisConnector({
            'tenant_url': 'https://test.armis.com',
            'api_key': 'key',
        })
        assert c.page_size == DEFAULT_PAGE_SIZE

    def test_page_size_clamped_to_max(self):
        c = ArmisConnector({
            'tenant_url': 'https://test.armis.com',
            'api_key': 'key',
            'page_size': 10000,
        })
        assert c.page_size == MAX_PAGE_SIZE

    def test_missing_api_key_raises(self):
        with pytest.raises(ValueError, match='API secret key is required'):
            ArmisConnector({'tenant_url': 'https://test.armis.com'})

    def test_missing_tenant_url_raises(self):
        with pytest.raises(ValueError, match='Tenant URL is required'):
            ArmisConnector({'api_key': 'key'})


# --- ASQ builder ---


class TestBuildAsq:
    def test_default(self, connector):
        assert connector._build_asq() == 'in:devices'

    def test_with_custom_filter(self, config):
        config['asq_filter'] = 'timeFrame:"7 Days"'
        c = ArmisConnector(config)
        assert c._build_asq() == 'in:devices timeFrame:"7 Days"'


# --- Authentication ---


class TestAuthentication:
    def test_authenticate_exchanges_key_for_token(self, connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'data': {
                'access_token': 'test-token-abc',
                'expiration_utc': '2026-03-17T12:30:00+00:00',
            },
            'success': True,
        }
        mock_response.raise_for_status = MagicMock()
        connector.session.post = MagicMock(return_value=mock_response)

        connector._authenticate()

        assert connector._access_token == 'test-token-abc'
        assert connector._token_expiry is not None

    def test_authenticate_handles_z_suffix_in_expiry(self, connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'data': {
                'access_token': 'token',
                'expiration_utc': '2026-03-17T12:30:00Z',
            },
            'success': True,
        }
        mock_response.raise_for_status = MagicMock()
        connector.session.post = MagicMock(return_value=mock_response)

        connector._authenticate()

        assert connector._token_expiry.tzinfo is not None

    def test_authenticate_raises_on_failure(self, connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'message': 'Invalid secret key',
            'success': False,
        }
        mock_response.raise_for_status = MagicMock()
        connector.session.post = MagicMock(return_value=mock_response)

        with pytest.raises(ValueError, match='Authentication failed'):
            connector._authenticate()

    def test_ensure_token_skips_if_valid(self, connector):
        connector._access_token = 'valid-token'
        connector._token_expiry = datetime.now(timezone.utc) + timedelta(minutes=10)
        connector._authenticate = MagicMock()

        connector._ensure_token()

        connector._authenticate.assert_not_called()

    def test_ensure_token_refreshes_if_near_expiry(self, connector):
        connector._access_token = 'expiring-token'
        connector._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=30)
        connector._authenticate = MagicMock()

        connector._ensure_token()

        connector._authenticate.assert_called_once()


# --- Request layer ---


class TestRequest:
    def test_request_adds_auth_header(self, connector):
        connector._access_token = 'my-token'
        connector._token_expiry = datetime.now(timezone.utc) + timedelta(minutes=10)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'success': True}
        connector.session.request = MagicMock(return_value=mock_response)

        connector._request('GET', '/api/v1/search/')

        call_kwargs = connector.session.request.call_args[1]
        assert call_kwargs['headers']['Authorization'] == 'my-token'

    def test_request_retries_on_401(self, connector):
        connector._access_token = 'old-token'
        connector._token_expiry = datetime.now(timezone.utc) + timedelta(minutes=10)

        unauthorized = MagicMock()
        unauthorized.status_code = 401

        success = MagicMock()
        success.status_code = 200
        success.json.return_value = {'success': True}

        connector.session.request = MagicMock(side_effect=[unauthorized, success])
        connector._authenticate = MagicMock(
            side_effect=lambda: setattr(connector, '_access_token', 'new-token')
        )

        connector._request('GET', '/api/v1/search/')

        assert connector._authenticate.call_count == 1
        assert connector.session.request.call_count == 2


# --- Record flattening ---


class TestFlattenRecord:
    def test_flattens_site_object(self):
        record = {
            'id': 1,
            'name': 'laptop-1',
            'site': {'name': 'New York', 'location': 'NY Office'},
        }
        flat = ArmisConnector._flatten_record(record)
        assert flat['siteName'] == 'New York'
        assert flat['siteLocation'] == 'NY Office'
        assert 'site' not in flat

    def test_flattens_sensor_object(self):
        record = {
            'id': 1,
            'sensor': {'name': 'collector-1', 'type': 'Access Point'},
        }
        flat = ArmisConnector._flatten_record(record)
        assert flat['sensorName'] == 'collector-1'
        assert flat['sensorType'] == 'Access Point'

    def test_flattens_data_sources_list(self):
        record = {
            'id': 1,
            'dataSources': [
                {'name': 'CrowdStrike', 'types': ['EDR']},
                {'name': 'Meraki', 'types': ['WLC']},
            ],
        }
        flat = ArmisConnector._flatten_record(record)
        assert flat['dataSources'] == 'CrowdStrike, Meraki'

    def test_flattens_tags_list(self):
        record = {'id': 1, 'tags': ['VIP', 'Production', 'Critical']}
        flat = ArmisConnector._flatten_record(record)
        assert flat['tags'] == 'VIP, Production, Critical'

    def test_flattens_ipv6_list(self):
        record = {'id': 1, 'ipv6': ['fe80::1', '2001:db8::1']}
        flat = ArmisConnector._flatten_record(record)
        assert flat['ipv6'] == 'fe80::1, 2001:db8::1'

    def test_flattens_custom_properties(self):
        record = {
            'id': 1,
            'customProperties': {'department': 'IT', 'owner': 'alice'},
        }
        flat = ArmisConnector._flatten_record(record)
        assert flat['cp_department'] == 'IT'
        assert flat['cp_owner'] == 'alice'
        assert 'customProperties' not in flat

    def test_preserves_scalar_values(self):
        record = {
            'id': 42,
            'name': 'server-1',
            'ipAddress': '10.0.0.1',
            'riskLevel': 75,
            'operatingSystem': 'Windows',
        }
        flat = ArmisConnector._flatten_record(record)
        assert flat['id'] == 42
        assert flat['name'] == 'server-1'
        assert flat['ipAddress'] == '10.0.0.1'
        assert flat['riskLevel'] == 75

    def test_handles_null_site(self):
        record = {'id': 1, 'site': None}
        flat = ArmisConnector._flatten_record(record)
        assert flat['site'] is None

    def test_handles_empty_data_sources(self):
        record = {'id': 1, 'dataSources': []}
        flat = ArmisConnector._flatten_record(record)
        assert flat['dataSources'] == ''


# --- Test connection ---


class TestTestConnection:
    def test_success_reports_device_count(self, connector):
        connector._request = MagicMock(return_value={
            'data': {'total': 4415, 'count': 1, 'results': [{}]},
            'success': True,
        })

        result = connector.test_connection()

        assert result['success'] is True
        assert '4,415' in result['message']
        assert result['metadata']['total_devices'] == 4415

    def test_auth_failure(self, connector):
        mock_response = MagicMock()
        mock_response.status_code = 401
        exc = requests.HTTPError(response=mock_response)
        connector._request = MagicMock(side_effect=exc)

        result = connector.test_connection()

        assert result['success'] is False
        assert 'Unauthorized' in result['message']

    def test_bad_asq_returns_helpful_message(self, connector):
        mock_response = MagicMock()
        mock_response.status_code = 400
        exc = requests.HTTPError(response=mock_response)
        connector._request = MagicMock(side_effect=exc)

        result = connector.test_connection()

        assert result['success'] is False
        assert 'ASQ' in result['message']


# --- fetch_data ---


class TestFetchData:
    def _setup_connector(self, connector):
        connector._access_token = 'test-token'
        connector._token_expiry = datetime.now(timezone.utc) + timedelta(minutes=10)
        return connector

    def test_yields_flattened_records_with_metadata(self, connector):
        connector = self._setup_connector(connector)
        connector._request = MagicMock(return_value={
            'data': {
                'count': 1,
                'next': None,
                'results': [{
                    'id': 1,
                    'name': 'device-1',
                    'type': 'Laptop',
                    'site': {'name': 'HQ', 'location': 'Chicago'},
                    'tags': ['VIP'],
                }],
                'total': 1,
            },
            'success': True,
        })

        records = list(connector.fetch_data('batch-123'))

        assert len(records) == 1
        assert records[0]['id'] == 1
        assert records[0]['siteName'] == 'HQ'
        assert records[0]['tags'] == 'VIP'
        assert records[0]['_batch_id'] == 'batch-123'
        assert records[0]['_source'] == 'armis'

    def test_paginates_until_next_is_null(self, connector):
        connector = self._setup_connector(connector)
        page1 = {
            'data': {
                'count': 2, 'next': 2,
                'results': [{'id': 1}, {'id': 2}], 'total': 3,
            },
            'success': True,
        }
        page2 = {
            'data': {
                'count': 1, 'next': None,
                'results': [{'id': 3}], 'total': 3,
            },
            'success': True,
        }
        connector._request = MagicMock(side_effect=[page1, page2])

        records = list(connector.fetch_data('batch-123'))

        assert len(records) == 3
        assert connector._request.call_count == 2
        second_call_params = connector._request.call_args_list[1][1]['params']
        assert second_call_params['from'] == '2'

    def test_passes_device_fields_and_orderby(self, connector):
        connector = self._setup_connector(connector)
        connector._request = MagicMock(return_value={
            'data': {'count': 0, 'next': None, 'results': [], 'total': 0},
            'success': True,
        })

        list(connector.fetch_data('batch-123'))

        call_params = connector._request.call_args[1]['params']
        assert call_params['orderBy'] == 'id'
        assert call_params['fields'] == DEVICE_FIELDS
        assert 'name' in call_params['fields']
        assert 'ipAddress' in call_params['fields']
        assert 'macAddress' in call_params['fields']

    def test_uses_configured_asq(self, config):
        config['asq_filter'] = 'type:"Laptops"'
        c = ArmisConnector(config)
        c._access_token = 'token'
        c._token_expiry = datetime.now(timezone.utc) + timedelta(minutes=10)
        c._request = MagicMock(return_value={
            'data': {'count': 0, 'next': None, 'results': [], 'total': 0},
            'success': True,
        })

        list(c.fetch_data('batch-123'))

        call_params = c._request.call_args[1]['params']
        assert 'type:"Laptops"' in call_params['aql']


# --- Schema ---


class TestGetSchema:
    def test_returns_schema_with_source(self, connector):
        schema = connector.get_schema()
        assert schema['source'] == 'armis'

    def test_has_flattened_site_fields(self, connector):
        schema = connector.get_schema()
        names = [f['name'] for f in schema['fields']]
        assert 'siteName' in names
        assert 'siteLocation' in names
        assert 'site' not in names

    def test_has_flattened_sensor_fields(self, connector):
        schema = connector.get_schema()
        names = [f['name'] for f in schema['fields']]
        assert 'sensorName' in names
        assert 'sensorType' in names

    def test_has_core_device_fields(self, connector):
        schema = connector.get_schema()
        names = [f['name'] for f in schema['fields']]
        for field in ['id', 'name', 'ipAddress', 'macAddress', 'operatingSystem',
                      'manufacturer', 'model', 'serialNumber', 'type', 'category',
                      'riskLevel', 'firstSeen', 'lastSeen', 'tags']:
            assert field in names, f'Missing field: {field}'
