# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for Azure Resource Graph connector."""

from unittest.mock import MagicMock
import pytest
import requests

from src.modules.integrations.connectors.azure_resource_graph_connector import (
    AzureResourceGraphConnector,
    API_ENDPOINT,
    DEFAULT_QUERY_PRESET,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_PAGINATION_PAGES,
    QUERY_PRESETS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    """Build a valid connector config dict."""
    base = {
        'tenant_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        'client_id': 'bbbbbbbb-cccc-dddd-eeee-ffffffffffff',
        'client_secret': 'test-secret-value',
        'subscription_ids': '',
        'resource_query': '',
        'page_size': 1000,
    }
    base.update(overrides)
    return base


@pytest.fixture
def config():
    return _make_config()


@pytest.fixture
def connector(config):
    return AzureResourceGraphConnector(config)


# ---------------------------------------------------------------------------
# TestConfigSchema
# ---------------------------------------------------------------------------

class TestConfigSchema:
    def test_has_required_auth_fields(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        assert 'tenant_id' in schema
        assert 'client_id' in schema
        assert 'client_secret' in schema

    def test_client_secret_is_sensitive(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        assert schema['client_secret']['sensitive'] is True
        assert schema['client_secret']['type'] == 'password'

    def test_tenant_id_has_uuid_validation(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        assert 'validation' in schema['tenant_id']
        assert 'pattern' in schema['tenant_id']['validation']

    def test_subscription_ids_is_optional(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        assert schema['subscription_ids']['required'] is False

    def test_query_preset_has_all_options(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        assert 'query_preset' in schema
        values = [o['value'] for o in schema['query_preset']['options']]
        assert 'virtual_machines' in values
        assert 'virtual_machines_basic' in values
        assert 'infrastructure' in values
        assert 'all_resources' in values

    def test_resource_query_is_optional(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        assert schema['resource_query']['required'] is False

    def test_page_size_has_validation(self):
        schema = AzureResourceGraphConnector.get_config_schema()
        assert schema['page_size']['validation']['min'] == 1
        assert schema['page_size']['validation']['max'] == MAX_PAGE_SIZE


# ---------------------------------------------------------------------------
# TestConstructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_valid_config_creates_instance(self, connector):
        assert connector.integration_type == 'azure_resource_graph'
        assert connector.tenant_id == 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

    def test_metadata_correct(self, connector):
        assert connector.display_name == 'Azure Resource Graph'
        assert connector.category == 'Cloud Platform'

    def test_missing_tenant_id_raises(self):
        with pytest.raises(ValueError, match='Tenant ID'):
            AzureResourceGraphConnector(_make_config(tenant_id=''))

    def test_missing_client_id_raises(self):
        with pytest.raises(ValueError, match='Client ID'):
            AzureResourceGraphConnector(_make_config(client_id=''))

    def test_missing_client_secret_raises(self):
        with pytest.raises(ValueError, match='Client Secret'):
            AzureResourceGraphConnector(_make_config(client_secret=''))

    def test_default_preset_is_virtual_machines(self, connector):
        assert connector.query_preset == 'virtual_machines'
        assert connector.resource_query == QUERY_PRESETS['virtual_machines']

    def test_vm_query_contains_ip_join(self, connector):
        """VM preset should join NICs and public IPs."""
        query = connector.resource_query
        assert 'networkinterfaces' in query.lower()
        assert 'publicipaddresses' in query.lower()
        assert 'privateIPAddress' in query
        assert 'publicIpAddress' in query
        assert 'join kind=leftouter' in query

    def test_infrastructure_preset(self):
        c = AzureResourceGraphConnector(
            _make_config(query_preset='infrastructure')
        )
        query = c.resource_query
        assert 'storageaccounts' in query
        assert 'networksecuritygroups' in query
        assert 'desktopvirtualization' in query

    def test_all_resources_preset(self):
        c = AzureResourceGraphConnector(
            _make_config(query_preset='all_resources')
        )
        assert c.resource_query == QUERY_PRESETS['all_resources']

    def test_custom_query_overrides_preset(self):
        custom = "Resources | where type =~ 'microsoft.compute/virtualmachines'"
        c = AzureResourceGraphConnector(_make_config(resource_query=custom))
        assert c.resource_query == custom
        assert c.query_preset == 'custom'

    def test_custom_query_schema_includes_all_fields(self):
        """Custom query should show all possible fields in schema."""
        custom = "Resources | project id, name"
        c = AzureResourceGraphConnector(_make_config(resource_query=custom))
        schema = c.get_schema()
        names = {f['name'] for f in schema['fields']}
        # Should include both VM and raw fields
        assert 'computerName' in names
        assert 'properties' in names

    def test_blank_custom_query_falls_back_to_preset(self, connector):
        assert connector.resource_query == QUERY_PRESETS[DEFAULT_QUERY_PRESET]

    def test_subscription_ids_parsed_from_csv(self):
        c = AzureResourceGraphConnector(
            _make_config(subscription_ids='sub-1, sub-2, sub-3')
        )
        assert c.subscription_ids == ['sub-1', 'sub-2', 'sub-3']

    def test_subscription_ids_empty_when_blank(self, connector):
        assert connector.subscription_ids == []

    def test_subscription_ids_accepts_list(self):
        c = AzureResourceGraphConnector(
            _make_config(subscription_ids=['sub-1', 'sub-2'])
        )
        assert c.subscription_ids == ['sub-1', 'sub-2']

    def test_page_size_clamped_to_max(self):
        c = AzureResourceGraphConnector(_make_config(page_size=5000))
        assert c.page_size == MAX_PAGE_SIZE

    def test_page_size_minimum_is_one(self):
        c = AzureResourceGraphConnector(_make_config(page_size=0))
        assert c.page_size == 1

    def test_default_page_size(self):
        c = AzureResourceGraphConnector(_make_config(page_size=None))
        assert c.page_size == DEFAULT_PAGE_SIZE

    def test_oauth2_scope_is_management_azure(self, connector):
        assert connector.auth.scope == 'https://management.azure.com/.default'


# ---------------------------------------------------------------------------
# TestQueryResourceGraph
# ---------------------------------------------------------------------------

class TestQueryResourceGraph:
    def _mock_auth_request(self, connector, responses):
        """Set up mock auth.request to return a sequence of responses."""
        mock_responses = []
        for resp_data in responses:
            mock_resp = MagicMock()
            mock_resp.json.return_value = resp_data
            mock_responses.append(mock_resp)
        connector.auth.request = MagicMock(side_effect=mock_responses)

    def test_single_page_returns_all_records(self, connector):
        self._mock_auth_request(connector, [
            {
                'totalRecords': 2,
                'count': 2,
                'data': [
                    {'id': '/sub/rg/vm1', 'name': 'vm1', 'type': 'microsoft.compute/virtualmachines'},
                    {'id': '/sub/rg/vm2', 'name': 'vm2', 'type': 'microsoft.compute/virtualmachines'},
                ],
            }
        ])

        result = list(connector._query_resource_graph('Resources'))

        assert len(result) == 2
        assert result[0]['name'] == 'vm1'

    def test_multi_page_follows_skip_token(self, connector):
        self._mock_auth_request(connector, [
            {
                'totalRecords': 3,
                'count': 2,
                'data': [
                    {'id': '/sub/rg/vm1', 'name': 'vm1'},
                    {'id': '/sub/rg/vm2', 'name': 'vm2'},
                ],
                '$skipToken': 'token-page-2',
            },
            {
                'totalRecords': 3,
                'count': 1,
                'data': [
                    {'id': '/sub/rg/vm3', 'name': 'vm3'},
                ],
            },
        ])

        result = list(connector._query_resource_graph('Resources'))

        assert len(result) == 3
        assert connector.auth.request.call_count == 2

        # Verify second call includes $skipToken
        second_call = connector.auth.request.call_args_list[1]
        body = second_call[1]['json']
        assert body['options']['$skipToken'] == 'token-page-2'

    def test_empty_data_stops_pagination(self, connector):
        self._mock_auth_request(connector, [
            {'totalRecords': 0, 'count': 0, 'data': []},
        ])

        result = list(connector._query_resource_graph('Resources'))

        assert len(result) == 0
        assert connector.auth.request.call_count == 1

    def test_subscription_ids_included_in_body(self, connector):
        self._mock_auth_request(connector, [
            {'totalRecords': 0, 'count': 0, 'data': []},
        ])

        list(connector._query_resource_graph('Resources', ['sub-1', 'sub-2']))

        body = connector.auth.request.call_args[1]['json']
        assert body['subscriptions'] == ['sub-1', 'sub-2']

    def test_no_subscriptions_when_none(self, connector):
        self._mock_auth_request(connector, [
            {'totalRecords': 0, 'count': 0, 'data': []},
        ])

        list(connector._query_resource_graph('Resources'))

        body = connector.auth.request.call_args[1]['json']
        assert 'subscriptions' not in body

    def test_uses_object_array_format(self, connector):
        self._mock_auth_request(connector, [
            {'totalRecords': 0, 'count': 0, 'data': []},
        ])

        list(connector._query_resource_graph('Resources'))

        body = connector.auth.request.call_args[1]['json']
        assert body['options']['resultFormat'] == 'objectArray'

    def test_posts_to_correct_endpoint(self, connector):
        self._mock_auth_request(connector, [
            {'totalRecords': 0, 'count': 0, 'data': []},
        ])

        list(connector._query_resource_graph('Resources'))

        call_args = connector.auth.request.call_args
        assert call_args[0][0] == API_ENDPOINT
        assert call_args[1]['method'] == 'POST'

    def test_page_size_in_request(self, connector):
        connector.page_size = 500
        self._mock_auth_request(connector, [
            {'totalRecords': 0, 'count': 0, 'data': []},
        ])

        list(connector._query_resource_graph('Resources'))

        body = connector.auth.request.call_args[1]['json']
        assert body['options']['$top'] == 500

    def test_pagination_safety_cap(self, connector):
        """Ensure we don't loop forever if $skipToken never clears."""
        # Return data with $skipToken on every page
        page = {
            'totalRecords': 1,
            'count': 1,
            'data': [{'id': 'r1'}],
            '$skipToken': 'infinite-token',
        }
        connector.auth.request = MagicMock(
            return_value=MagicMock(json=MagicMock(return_value=page))
        )

        result = list(connector._query_resource_graph('Resources'))

        assert connector.auth.request.call_count == MAX_PAGINATION_PAGES
        assert len(result) == MAX_PAGINATION_PAGES

    def test_skip_token_cleared_between_queries(self, connector):
        """First page should NOT have $skipToken."""
        self._mock_auth_request(connector, [
            {'totalRecords': 0, 'count': 0, 'data': []},
        ])

        list(connector._query_resource_graph('Resources'))

        body = connector.auth.request.call_args[1]['json']
        assert '$skipToken' not in body.get('options', {})


# ---------------------------------------------------------------------------
# TestConnectionTest
# ---------------------------------------------------------------------------

class TestConnectionTest:
    def _mock_test_response(self, connector, total_records=0):
        """Set up mock for a successful test_connection."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'totalRecords': total_records,
            'count': min(total_records, 1),
            'data': [{'id': 'r1'}] if total_records else [],
        }
        connector.auth.authenticate = MagicMock()
        connector.auth.request = MagicMock(return_value=mock_resp)

    def test_success_returns_resource_count(self, connector):
        self._mock_test_response(connector, total_records=1247)

        result = connector.test_connection()

        assert result['success'] is True
        assert '1,247' in result['message']
        assert result['metadata']['resource_count'] == 1247

    def test_success_with_subscriptions(self, connector):
        connector.subscription_ids = ['sub-1']
        self._mock_test_response(connector, total_records=50)

        result = connector.test_connection()

        assert result['success'] is True
        body = connector.auth.request.call_args[1]['json']
        assert body['subscriptions'] == ['sub-1']

    def test_uses_configured_query(self, connector):
        """test_connection should use the actual configured query."""
        self._mock_test_response(connector, total_records=10)

        connector.test_connection()

        body = connector.auth.request.call_args[1]['json']
        assert body['query'] == connector.resource_query
        assert body['options']['$top'] == 1

    def test_401_returns_auth_failure(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        connector.auth.authenticate = MagicMock()
        connector.auth.request = MagicMock(
            side_effect=requests.HTTPError(response=mock_resp)
        )

        result = connector.test_connection()

        assert result['success'] is False
        assert 'Authentication failed' in result['message']

    def test_403_returns_permission_error(self, connector):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        connector.auth.authenticate = MagicMock()
        connector.auth.request = MagicMock(
            side_effect=requests.HTTPError(response=mock_resp)
        )

        result = connector.test_connection()

        assert result['success'] is False
        assert 'permission' in result['message'].lower()

    def test_generic_error_returns_failure(self, connector):
        connector.auth.authenticate = MagicMock(
            side_effect=ConnectionError('DNS resolution failed')
        )

        result = connector.test_connection()

        assert result['success'] is False
        assert 'Connection failed' in result['message']

    def test_empty_data_returns_zero_count(self, connector):
        self._mock_test_response(connector, total_records=0)

        result = connector.test_connection()

        assert result['success'] is True
        assert result['metadata']['resource_count'] == 0


# ---------------------------------------------------------------------------
# TestFetchData
# ---------------------------------------------------------------------------

class TestFetchData:
    def _setup_fetch(self, connector, resources):
        """Mock _query_resource_graph to yield given resources."""
        connector.auth.authenticate = MagicMock()
        connector._query_resource_graph = MagicMock(return_value=iter(resources))
        connector._should_include_record = MagicMock(return_value=True)

    def test_yields_resources_with_metadata(self, connector):
        resources = [
            {'id': '/sub/rg/vm1', 'name': 'vm1', 'type': 'microsoft.compute/virtualmachines'},
        ]
        self._setup_fetch(connector, resources)

        result = list(connector.fetch_data('batch-001'))

        assert len(result) == 1
        assert result[0]['_batch_id'] == 'batch-001'
        assert result[0]['_source'] == 'azure_resource_graph'
        assert '_collected_at' in result[0]

    def test_passes_subscription_filter(self, connector):
        connector.subscription_ids = ['sub-1', 'sub-2']
        self._setup_fetch(connector, [])

        list(connector.fetch_data('batch-001'))

        call_args = connector._query_resource_graph.call_args
        assert call_args[0][1] == ['sub-1', 'sub-2']

    def test_no_subscriptions_passes_none(self, connector):
        connector.subscription_ids = []
        self._setup_fetch(connector, [])

        list(connector.fetch_data('batch-001'))

        call_args = connector._query_resource_graph.call_args
        assert call_args[0][1] is None

    def test_uses_configured_query(self, connector):
        custom = "Resources | where type =~ 'microsoft.compute/virtualmachines'"
        connector.resource_query = custom
        self._setup_fetch(connector, [])

        list(connector.fetch_data('batch-001'))

        call_args = connector._query_resource_graph.call_args
        assert call_args[0][0] == custom

    def test_respects_data_filters(self, connector):
        resources = [
            {'id': 'r1', 'name': 'vm1'},
            {'id': 'r2', 'name': 'vm2'},
        ]
        connector.auth.authenticate = MagicMock()
        connector._query_resource_graph = MagicMock(return_value=iter(resources))
        connector._should_include_record = MagicMock(side_effect=[True, False])

        result = list(connector.fetch_data('batch-001'))

        assert len(result) == 1
        assert result[0]['id'] == 'r1'

    def test_logs_execution_and_metrics(self, connector):
        resources = [
            {'id': 'r1', 'name': 'vm1'},
            {'id': 'r2', 'name': 'vm2'},
        ]
        self._setup_fetch(connector, resources)
        connector.log_execution_start = MagicMock(return_value=1000.0)
        connector.log_execution_end = MagicMock()
        connector.log_sync_metrics = MagicMock()

        list(connector.fetch_data('batch-001'))

        connector.log_execution_start.assert_called_once()
        connector.log_execution_end.assert_called_once()
        connector.log_sync_metrics.assert_called_once()
        # Verify record count in metrics
        metrics_call = connector.log_sync_metrics.call_args
        assert metrics_call[0][1] == 2  # records_fetched

    def test_exception_logs_failure(self, connector):
        connector.auth.authenticate = MagicMock()
        connector._query_resource_graph = MagicMock(
            side_effect=requests.HTTPError('API error')
        )
        connector.log_execution_start = MagicMock(return_value=1000.0)
        connector.log_execution_end = MagicMock()

        with pytest.raises(requests.HTTPError):
            list(connector.fetch_data('batch-001'))

        connector.log_execution_end.assert_called_once()
        assert connector.log_execution_end.call_args[1].get('success') is False \
            or connector.log_execution_end.call_args[0][2] is False


# ---------------------------------------------------------------------------
# TestSchema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_schema_has_required_structure(self, connector):
        schema = connector.get_schema()
        assert 'fields' in schema
        assert 'source' in schema
        assert schema['source'] == 'azure_resource_graph'

    def test_schema_includes_core_fields(self, connector):
        schema = connector.get_schema()
        names = {f['name'] for f in schema['fields']}
        for field in ['id', 'name', 'type', 'location',
                      'resourceGroup', 'subscriptionId']:
            assert field in names, f'Missing field: {field}'

    def test_vm_preset_has_ip_fields(self, connector):
        """Default preset (virtual_machines) has IP + VM fields."""
        schema = connector.get_schema()
        names = {f['name'] for f in schema['fields']}
        for field in ['computerName', 'osType', 'vmSize',
                      'privateIPAddress', 'publicIpAddress']:
            assert field in names, f'Missing field: {field}'

    def test_vm_basic_preset_has_nic_count(self):
        c = AzureResourceGraphConnector(
            _make_config(query_preset='virtual_machines_basic')
        )
        schema = c.get_schema()
        names = {f['name'] for f in schema['fields']}
        assert 'nicCount' in names
        assert 'computerName' in names
        # Should NOT have IP join fields
        assert 'privateIPAddress' not in names

    def test_infrastructure_preset_has_properties(self):
        c = AzureResourceGraphConnector(
            _make_config(query_preset='infrastructure')
        )
        schema = c.get_schema()
        names = {f['name'] for f in schema['fields']}
        assert 'properties' in names
        assert 'computerName' not in names

    def test_all_resources_preset_has_raw_properties(self):
        c = AzureResourceGraphConnector(
            _make_config(query_preset='all_resources')
        )
        schema = c.get_schema()
        names = {f['name'] for f in schema['fields']}
        assert 'properties' in names
        assert 'identity' in names
        assert 'computerName' not in names

    def test_each_field_has_name_path_type(self, connector):
        schema = connector.get_schema()
        for field in schema['fields']:
            assert 'name' in field
            assert 'path' in field
            assert 'type' in field
