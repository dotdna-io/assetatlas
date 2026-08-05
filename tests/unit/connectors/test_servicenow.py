# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for ServiceNowConnector.

Inherits 11 standard tests from ConnectorTestCase and adds connector-specific
tests for offset pagination, lookup table enrichment, and reference field
flattening.
"""

import json
import pytest
import responses
from urllib.parse import urlparse, parse_qs

from tests.unit.connectors.conftest import ConnectorTestCase, MockHTTP, FIXTURES_DIR
from src.modules.integrations.connectors.servicenow_connector import ServiceNowConnector


BASE_URL = "https://dev12345.service-now.com"
CI_URL = f"{BASE_URL}/api/now/table/cmdb_ci"
STATS_URL = f"{BASE_URL}/api/now/stats/cmdb_ci"
MODEL_URL = f"{BASE_URL}/api/now/table/cmdb_model"
LOCATION_URL = f"{BASE_URL}/api/now/table/cmn_location"
COMPANY_URL = f"{BASE_URL}/api/now/table/core_company"


def _register_lookup_tables(mock_http):
    """Register mock responses for all lookup tables."""
    mock_http.register_from_fixture("GET", MODEL_URL, "servicenow/lookup_cmdb_model.json")
    mock_http.register_from_fixture("GET", MODEL_URL, "servicenow/lookup_cmdb_model.json")
    mock_http.register_json("GET", MODEL_URL, {"result": []})

    mock_http.register_from_fixture("GET", LOCATION_URL, "servicenow/lookup_cmn_location.json")
    mock_http.register_from_fixture("GET", LOCATION_URL, "servicenow/lookup_cmn_location.json")
    mock_http.register_json("GET", LOCATION_URL, {"result": []})

    mock_http.register_from_fixture("GET", COMPANY_URL, "servicenow/lookup_core_company.json")
    mock_http.register_from_fixture("GET", COMPANY_URL, "servicenow/lookup_core_company.json")
    mock_http.register_json("GET", COMPANY_URL, {"result": []})


class TestServiceNowConnector(ConnectorTestCase):
    connector_class = ServiceNowConnector
    mock_config = {
        "instance": "dev12345",
        "username": "test-user",
        "password": "test-pass",
        "page_size": 100,
        "enable_enrichment": False,
    }

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_http):
        """Register default CI table + stats responses for all tests."""
        # Single-page CI response (for base class tests)
        mock_http.register_from_fixture("GET", CI_URL, "servicenow/cmdb_ci_single.json")
        # Empty second page to terminate pagination
        mock_http.register_json("GET", CI_URL, {"result": []})

        # Stats endpoint for test_connection
        mock_http.register_from_fixture("GET", CI_URL, "servicenow/cmdb_ci_single.json")
        mock_http.register_from_fixture("GET", STATS_URL, "servicenow/stats_count.json")

    # -------------------------------------------------------------------------
    # Connector-specific: offset pagination
    # -------------------------------------------------------------------------

    @responses.activate
    def test_pagination_uses_sysparm_offset(self):
        """sysparm_offset and sysparm_limit are sent as query params."""
        fixtures = FIXTURES_DIR / "servicenow"

        with open(fixtures / "cmdb_ci_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "cmdb_ci_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.GET, CI_URL, json=page1, status=200)
        responses.add(responses.GET, CI_URL, json=page2, status=200)
        responses.add(responses.GET, CI_URL, json={"result": []}, status=200)

        config = {**self.mock_config, "page_size": 10}
        connector = ServiceNowConnector(config)
        records = list(connector.fetch_data("batch-paginate"))

        # page1 has 2 records, page2 has 1 = 3 total
        assert len(records) == 3

        # Check that offset was passed correctly
        ci_calls = [c for c in responses.calls if "/table/cmdb_ci" in c.request.url]
        assert len(ci_calls) >= 2

        # First call should have sysparm_offset=0
        qs1 = parse_qs(urlparse(ci_calls[0].request.url).query)
        assert qs1.get("sysparm_offset") == ["0"]
        assert qs1.get("sysparm_limit") == ["10"]

        # Second call should have sysparm_offset=2 (len of first page)
        qs2 = parse_qs(urlparse(ci_calls[1].request.url).query)
        assert qs2.get("sysparm_offset") == ["2"]

    @responses.activate
    def test_pagination_multi_page_yields_all_records(self):
        """Records from all pages are yielded."""
        fixtures = FIXTURES_DIR / "servicenow"

        with open(fixtures / "cmdb_ci_page1.json") as f:
            page1 = json.load(f)
        with open(fixtures / "cmdb_ci_page2.json") as f:
            page2 = json.load(f)

        responses.add(responses.GET, CI_URL, json=page1, status=200)
        responses.add(responses.GET, CI_URL, json=page2, status=200)
        responses.add(responses.GET, CI_URL, json={"result": []}, status=200)

        config = {**self.mock_config, "page_size": 10}
        connector = ServiceNowConnector(config)
        records = list(connector.fetch_data("batch-multi"))

        names = {r["name"] for r in records}
        assert "DESKTOP-WIN001" in names
        assert "SERVER-LIN001" in names
        assert "LAPTOP-MAC001" in names

    @responses.activate
    def test_pagination_single_page(self):
        """Single page response terminates after empty second page."""
        fixtures = FIXTURES_DIR / "servicenow"

        with open(fixtures / "cmdb_ci_single.json") as f:
            single = json.load(f)

        responses.add(responses.GET, CI_URL, json=single, status=200)
        responses.add(responses.GET, CI_URL, json={"result": []}, status=200)

        connector = ServiceNowConnector(self.mock_config)
        records = list(connector.fetch_data("batch-single"))
        assert len(records) == 1

    # -------------------------------------------------------------------------
    # Connector-specific: lookup table enrichment
    # -------------------------------------------------------------------------

    @responses.activate
    def test_enrichment_resolves_lookup_fields(self):
        """Lookup tables resolve sys_id references to display names."""
        fixtures = FIXTURES_DIR / "servicenow"

        # Lookup tables (each needs first page + empty page to terminate)
        with open(fixtures / "lookup_cmdb_model.json") as f:
            models = json.load(f)
        with open(fixtures / "lookup_cmn_location.json") as f:
            locations = json.load(f)
        with open(fixtures / "lookup_core_company.json") as f:
            companies = json.load(f)

        # Register lookup table responses (order: model, location, manufacturer)
        responses.add(responses.GET, MODEL_URL, json=models, status=200)
        responses.add(responses.GET, MODEL_URL, json={"result": []}, status=200)
        responses.add(responses.GET, LOCATION_URL, json=locations, status=200)
        responses.add(responses.GET, LOCATION_URL, json={"result": []}, status=200)
        responses.add(responses.GET, COMPANY_URL, json=companies, status=200)
        responses.add(responses.GET, COMPANY_URL, json={"result": []}, status=200)

        # CI data
        with open(fixtures / "cmdb_ci_single.json") as f:
            ci_data = json.load(f)
        responses.add(responses.GET, CI_URL, json=ci_data, status=200)
        responses.add(responses.GET, CI_URL, json={"result": []}, status=200)

        config = {**self.mock_config, "enable_enrichment": True}
        connector = ServiceNowConnector(config)
        records = list(connector.fetch_data("batch-enrich"))

        assert len(records) == 1
        rec = records[0]

        # model_id should be resolved to display_name from lookup
        assert rec["model_id"] == "Dell OptiPlex 7090"
        # location should be resolved to name from lookup
        assert rec["location"] == "New York HQ"
        # manufacturer should be resolved to name from lookup
        assert rec["manufacturer"] == "Dell Inc."

    @responses.activate
    def test_enrichment_adds_enriched_subfields(self):
        """Enrichment adds {field}_enriched.{subfield} keys."""
        fixtures = FIXTURES_DIR / "servicenow"

        with open(fixtures / "lookup_cmdb_model.json") as f:
            models = json.load(f)
        with open(fixtures / "lookup_cmn_location.json") as f:
            locations = json.load(f)
        with open(fixtures / "lookup_core_company.json") as f:
            companies = json.load(f)

        responses.add(responses.GET, MODEL_URL, json=models, status=200)
        responses.add(responses.GET, MODEL_URL, json={"result": []}, status=200)
        responses.add(responses.GET, LOCATION_URL, json=locations, status=200)
        responses.add(responses.GET, LOCATION_URL, json={"result": []}, status=200)
        responses.add(responses.GET, COMPANY_URL, json=companies, status=200)
        responses.add(responses.GET, COMPANY_URL, json={"result": []}, status=200)

        with open(fixtures / "cmdb_ci_single.json") as f:
            ci_data = json.load(f)
        responses.add(responses.GET, CI_URL, json=ci_data, status=200)
        responses.add(responses.GET, CI_URL, json={"result": []}, status=200)

        config = {**self.mock_config, "enable_enrichment": True}
        connector = ServiceNowConnector(config)
        records = list(connector.fetch_data("batch-subfields"))

        rec = records[0]

        # Location enriched subfields
        assert rec["location_enriched.city"] == "New York"
        assert rec["location_enriched.state"] == "NY"
        assert rec["location_enriched.country"] == "US"

        # Model enriched subfields
        assert rec["model_id_enriched.model_number"] == "7090"

    @responses.activate
    def test_enrichment_resolves_foreign_keys(self):
        """Model's manufacturer FK is resolved via manufacturer lookup cache."""
        fixtures = FIXTURES_DIR / "servicenow"

        with open(fixtures / "lookup_cmdb_model.json") as f:
            models = json.load(f)
        with open(fixtures / "lookup_cmn_location.json") as f:
            locations = json.load(f)
        with open(fixtures / "lookup_core_company.json") as f:
            companies = json.load(f)

        responses.add(responses.GET, MODEL_URL, json=models, status=200)
        responses.add(responses.GET, MODEL_URL, json={"result": []}, status=200)
        responses.add(responses.GET, LOCATION_URL, json=locations, status=200)
        responses.add(responses.GET, LOCATION_URL, json={"result": []}, status=200)
        responses.add(responses.GET, COMPANY_URL, json=companies, status=200)
        responses.add(responses.GET, COMPANY_URL, json={"result": []}, status=200)

        with open(fixtures / "cmdb_ci_single.json") as f:
            ci_data = json.load(f)
        responses.add(responses.GET, CI_URL, json=ci_data, status=200)
        responses.add(responses.GET, CI_URL, json={"result": []}, status=200)

        config = {**self.mock_config, "enable_enrichment": True}
        connector = ServiceNowConnector(config)
        records = list(connector.fetch_data("batch-fk"))

        rec = records[0]

        # Model's manufacturer field (mfg-001) should be resolved via
        # the manufacturer lookup cache to "Dell Inc." (not the raw sys_id)
        assert rec["model_id_enriched.manufacturer"] == "Dell Inc."

    # -------------------------------------------------------------------------
    # Connector-specific: reference field flattening
    # -------------------------------------------------------------------------

    def test_flatten_reference_object(self):
        """Reference field objects are collapsed to sys_id strings."""
        record = {
            "name": "DESKTOP-001",
            "model_id": {"link": "https://dev.service-now.com/...", "value": "model-abc"},
            "location": {"link": "https://dev.service-now.com/...", "value": "loc-xyz"},
            "serial_number": "SN-123",
        }
        flat = ServiceNowConnector._flatten_reference_fields(record)

        assert flat["model_id"] == "model-abc"
        assert flat["location"] == "loc-xyz"
        assert flat["name"] == "DESKTOP-001"
        assert flat["serial_number"] == "SN-123"

    def test_flatten_display_value_wrapper(self):
        """sysparm_display_value=all wrappers use display_value."""
        record = {
            "manufacturer": {
                "display_value": "Dell Inc.",
                "value": "mfg-001",
                "link": "https://dev.service-now.com/...",
            },
            "name": {"display_value": "DESKTOP-001", "value": "DESKTOP-001"},
        }
        flat = ServiceNowConnector._flatten_reference_fields(record)

        assert flat["manufacturer"] == "Dell Inc."
        assert flat["name"] == "DESKTOP-001"

    def test_flatten_display_value_empty_uses_value(self):
        """When display_value is empty, falls back to value."""
        record = {
            "company": {
                "display_value": "",
                "value": "comp-001",
                "link": "https://dev.service-now.com/...",
            },
        }
        flat = ServiceNowConnector._flatten_reference_fields(record)
        assert flat["company"] == "comp-001"

    def test_flatten_plain_string_passthrough(self):
        """Non-dict values pass through unchanged."""
        record = {"serial_number": "SN-123", "ip_address": "10.0.1.1"}
        flat = ServiceNowConnector._flatten_reference_fields(record)
        assert flat == record

    # -------------------------------------------------------------------------
    # Connector-specific: metadata and instance parsing
    # -------------------------------------------------------------------------

    def test_metadata_source_is_servicenow_cmdb(self, connector, mock_http):
        """The _source metadata field must be 'servicenow_cmdb'."""
        records = list(connector.fetch_data("batch-meta"))
        assert records[0]["_source"] == "servicenow_cmdb"

    def test_instance_from_url(self):
        """instance_url is parsed correctly."""
        config = {
            **self.mock_config,
            "instance_url": "https://myinst.service-now.com",
        }
        del config["instance"]
        connector = ServiceNowConnector(config)
        assert connector.instance == "myinst"
        assert connector.base_url == "https://myinst.service-now.com"

    def test_missing_instance_raises(self):
        """Missing both instance and instance_url raises ValueError."""
        config = {**self.mock_config}
        del config["instance"]
        with pytest.raises(ValueError, match="instance"):
            ServiceNowConnector(config)

    # -------------------------------------------------------------------------
    # Connector-specific: enrichment disabled
    # -------------------------------------------------------------------------

    @responses.activate
    def test_enrichment_disabled_skips_lookup_tables(self):
        """When enable_enrichment=False, no lookup table requests are made."""
        fixtures = FIXTURES_DIR / "servicenow"

        with open(fixtures / "cmdb_ci_single.json") as f:
            ci_data = json.load(f)

        responses.add(responses.GET, CI_URL, json=ci_data, status=200)
        responses.add(responses.GET, CI_URL, json={"result": []}, status=200)

        config = {**self.mock_config, "enable_enrichment": False}
        connector = ServiceNowConnector(config)
        records = list(connector.fetch_data("batch-no-enrich"))

        assert len(records) == 1

        # No lookup table calls should have been made
        all_urls = [c.request.url for c in responses.calls]
        for url in all_urls:
            assert "/table/cmdb_model" not in url
            assert "/table/cmn_location" not in url
            assert "/table/core_company" not in url

    # -------------------------------------------------------------------------
    # Connector-specific: schema
    # -------------------------------------------------------------------------

    @responses.activate
    def test_schema_includes_enrichment_fields_when_enabled(self):
        """Schema includes enriched subfields when enrichment is enabled."""
        config = {**self.mock_config, "enable_enrichment": True}
        connector = ServiceNowConnector(config)
        schema = connector.get_schema()

        field_names = {f["name"] for f in schema["fields"]}
        assert "location_enriched.city" in field_names
        assert "model_id_enriched.model_number" in field_names
        assert schema["enrichment_available"] is True

    @responses.activate
    def test_schema_excludes_enrichment_fields_when_disabled(self):
        """Schema excludes enriched subfields when enrichment is disabled."""
        config = {**self.mock_config, "enable_enrichment": False}
        connector = ServiceNowConnector(config)
        schema = connector.get_schema()

        field_names = {f["name"] for f in schema["fields"]}
        assert "location_enriched.city" not in field_names
        assert schema["enrichment_available"] is False


# ---------------------------------------------------------------------------
# Issue #1334 — paginator cap-hit surfaces as a collection warning
# ---------------------------------------------------------------------------


def _ci_page(*sys_ids):
    return {"result": [{"sys_id": sid, "name": sid} for sid in sys_ids]}


class TestServiceNowTruncation:
    """Both OffsetPaginator sites relay a cap-hit: the CMDB CI collection
    (a shortfall reads as deleted assets) and the per-table enrichment
    lookups (a shortfall under-enriches assets but loses none)."""

    mock_config = {
        "instance": "dev12345",
        "username": "test-user",
        "password": "test-pass",
        "page_size": 100,
        "enable_enrichment": False,
        "max_pagination_pages": 2,
    }

    @responses.activate
    def test_capped_ci_run_records_one_warning(self):
        # Two non-empty pages and no X-Total-Count header: the cap, not the
        # data, ends the loop. (Without a total, OffsetPaginator deliberately
        # reports truncated whenever the last records land on the cap-th
        # fetch — over-reporting beats under-reporting.)
        responses.add(responses.GET, CI_URL, json=_ci_page("ci-1"), status=200)
        responses.add(responses.GET, CI_URL, json=_ci_page("ci-2"), status=200)

        connector = ServiceNowConnector(self.mock_config)
        records = list(connector.fetch_data("test-batch"))

        assert len(records) == 2
        warnings = connector.collection_warnings
        assert len(warnings) == 1, warnings
        assert "ServiceNow CMDB CIs truncated at the 2-page cap" in warnings[0]

    @responses.activate
    def test_end_of_data_records_no_warning(self):
        # An empty second page is a genuine end-of-data signal.
        responses.add(responses.GET, CI_URL, json=_ci_page("ci-1"), status=200)
        responses.add(responses.GET, CI_URL, json={"result": []}, status=200)

        connector = ServiceNowConnector(self.mock_config)
        records = list(connector.fetch_data("test-batch"))

        assert len(records) == 1
        assert connector.collection_warnings == []

    @responses.activate
    def test_truncated_lookup_table_warns_without_losing_assets(self):
        """A capped lookup table under-enriches assets but loses none — the
        warning must name the table so the operator knows which enrichment
        is partial."""
        config = {
            **self.mock_config,
            "enable_enrichment": True,
            "max_pagination_pages": 1,
        }
        with open(FIXTURES_DIR / "servicenow" / "lookup_cmdb_model.json") as f:
            models = json.load(f)
        # cmdb_model: one non-empty page with no total header — the cap of 1
        # is hit. The other lookup tables are empty: end of data on the
        # first fetch, no warning.
        responses.add(responses.GET, MODEL_URL, json=models, status=200)
        responses.add(responses.GET, LOCATION_URL, json={"result": []}, status=200)
        responses.add(responses.GET, COMPANY_URL, json={"result": []}, status=200)
        # The CI page reports its total, so the CI paginator stops on data,
        # not on the cap.
        responses.add(
            responses.GET, CI_URL, json=_ci_page("ci-1"),
            headers={"X-Total-Count": "1"}, status=200,
        )

        connector = ServiceNowConnector(config)
        records = list(connector.fetch_data("test-batch"))

        assert len(records) == 1
        warnings = connector.collection_warnings
        assert len(warnings) == 1, warnings
        assert "ServiceNow enrichment lookup table cmdb_model" in warnings[0]
        assert "1-page cap" in warnings[0]

    def test_config_schema_declares_max_pagination_pages(self):
        """The cap is operator-settable: declared with the connector's default."""
        field = ServiceNowConnector.get_config_schema()["max_pagination_pages"]
        assert field["type"] == "number"
        assert field["required"] is False
        assert field["default"] == 500
