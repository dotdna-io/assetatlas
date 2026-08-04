# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Shared test infrastructure for connector unit tests.

Provides:
- MockHTTP: helper wrapping the `responses` library for HTTP mocking
- mock_http: pytest fixture that yields a MockHTTP instance
- ConnectorTestCase: base class defining the standard 11-test contract
"""

import json
import pytest
import responses
from pathlib import Path
from typing import Dict, List, Optional, Type, Any

from src.modules.integrations.base.base_connector import BaseConnector

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class MockHTTP:
    """Wraps a `responses.RequestsMock` context with convenience methods."""

    def __init__(self, rsps):
        self.rsps = rsps

    def register_json(self, method, url, body, status=200, headers=None):
        method_map = {
            "GET": responses.GET,
            "POST": responses.POST,
            "PUT": responses.PUT,
            "DELETE": responses.DELETE,
            "PATCH": responses.PATCH,
        }
        self.rsps.add(
            method_map[method.upper()],
            url,
            json=body,
            status=status,
            headers=headers or {},
        )

    def register_from_fixture(self, method, url, fixture_path, status=200, headers=None):
        full_path = FIXTURES_DIR / fixture_path
        with open(full_path) as f:
            body = json.load(f)
        self.register_json(method, url, body, status, headers)

    def register_auth_success(self, url, token="test-token", expires_in=3600):
        self.register_json(
            "POST",
            url,
            {"access_token": token, "expires_in": expires_in, "token_type": "Bearer"},
        )

    def register_paginated(self, method, url, pages, headers=None):
        for page in pages:
            self.register_json(method, url, page, headers=headers)

    def register_rate_limit(self, method, url, retry_after=1, then_body=None):
        method_map = {"GET": responses.GET, "POST": responses.POST}
        self.rsps.add(
            method_map[method.upper()],
            url,
            status=429,
            headers={"Retry-After": str(retry_after)},
        )
        if then_body:
            self.register_json(method, url, then_body)


@pytest.fixture
def mock_http():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield MockHTTP(rsps)


class ConnectorTestCase:
    """
    Base class for connector unit tests.

    Subclasses must set:
        connector_class: Type[BaseConnector]
        mock_config: Dict[str, Any]

    Seven tests run automatically; four are skipped and must be overridden
    with appropriate mocks in the subclass.
    """

    connector_class: Type[BaseConnector] = None
    mock_config: Dict[str, Any] = {}

    @pytest.fixture
    def connector(self):
        assert self.connector_class is not None, "Set connector_class on test class"
        return self.connector_class(self.mock_config)

    # --- Schema tests (3) ---

    def test_config_schema_completeness(self):
        schema = self.connector_class.get_config_schema()
        assert isinstance(schema, dict)
        for field_name, field_spec in schema.items():
            assert "type" in field_spec, f"Field '{field_name}' missing 'type'"
            assert "label" in field_spec, f"Field '{field_name}' missing 'label'"
            assert "required" in field_spec, f"Field '{field_name}' missing 'required'"
            assert field_spec["type"] in (
                "string",
                "password",
                "url",
                "number",
                "select",
                "boolean",
            ), f"Field '{field_name}' has unknown type '{field_spec['type']}'"

    def test_config_schema_sensitive_fields_marked(self):
        schema = self.connector_class.get_config_schema()
        for field_name, field_spec in schema.items():
            if field_spec["type"] == "password":
                assert field_spec.get("sensitive") is True, (
                    f"Password field '{field_name}' must have sensitive=True"
                )

    def test_get_schema_returns_fields(self, connector):
        schema = connector.get_schema()
        assert "fields" in schema
        assert isinstance(schema["fields"], list)
        if schema["fields"]:
            field = schema["fields"][0]
            assert "name" in field
            assert "path" in field
            assert "type" in field

    # --- Connection tests (1 real + 2 skipped) ---

    def test_connection_success(self, connector, mock_http):
        result = connector.test_connection()
        assert isinstance(result, dict)
        assert "success" in result
        assert "message" in result

    @pytest.mark.skip(reason="Must be implemented by subclass with appropriate mocks")
    def test_connection_auth_failure(self, connector, mock_http):
        pass

    @pytest.mark.skip(reason="Must be implemented by subclass with appropriate mocks")
    def test_connection_network_error(self, connector, mock_http):
        pass

    # --- Data collection tests (3) ---

    def test_fetch_yields_records(self, connector, mock_http):
        records = list(connector.fetch_data("test-batch"))
        assert len(records) > 0

    def test_fetch_records_are_flat_dicts(self, connector, mock_http):
        records = list(connector.fetch_data("test-batch"))
        for record in records:
            assert isinstance(record, dict)
            for key in record:
                assert isinstance(key, str)

    def test_fetch_metadata_injected(self, connector, mock_http):
        records = list(connector.fetch_data("test-batch"))
        assert len(records) > 0
        record = records[0]
        assert record["_batch_id"] == "test-batch"
        assert "_collected_at" in record
        assert "_source" in record

    # --- Error handling tests (2 skipped) ---

    @pytest.mark.skip(reason="Must be implemented by subclass with appropriate mocks")
    def test_handles_rate_limit(self, connector, mock_http):
        pass

    @pytest.mark.skip(reason="Must be implemented by subclass with appropriate mocks")
    def test_handles_server_error(self, connector, mock_http):
        pass
