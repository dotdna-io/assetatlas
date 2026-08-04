# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for ManualUploadConnector.

Inherits standard tests from ConnectorTestCase and adds connector-specific
tests for CSV/JSON parsing, _record_number metadata, and _source override.
"""

import pytest
from pathlib import Path

from tests.unit.connectors.conftest import ConnectorTestCase, FIXTURES_DIR
from src.modules.integrations.connectors.manual_upload_connector import ManualUploadConnector


FIXTURE_DIR = FIXTURES_DIR / "manual_upload"


def _read_fixture(filename: str) -> str:
    return (FIXTURE_DIR / filename).read_text()


class TestManualUploadConnector(ConnectorTestCase):
    connector_class = ManualUploadConnector
    mock_config = {
        "file_format": "csv",
        "file_data": "hostname,ip_address,serial_number\nDESKTOP-001,10.0.1.1,SN-001\n",
        "delimiter": ",",
        "encoding": "utf-8",
        "file_name": "test.csv",
    }

    # ------------------------------------------------------------------
    # Override base tests that use mock_http (N/A for file-based connector)
    # ------------------------------------------------------------------

    def test_connection_success(self, connector):
        result = connector.test_connection()
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "message" in result

    def test_fetch_yields_records(self, connector):
        records = list(connector.fetch_data("test-batch"))
        assert len(records) > 0

    def test_fetch_records_are_flat_dicts(self, connector):
        records = list(connector.fetch_data("test-batch"))
        for record in records:
            assert isinstance(record, dict)
            for key in record:
                assert isinstance(key, str)

    def test_fetch_metadata_injected(self, connector):
        records = list(connector.fetch_data("test-batch"))
        assert len(records) > 0
        record = records[0]
        assert record["_batch_id"] == "test-batch"
        assert "_collected_at" in record
        assert "_source" in record

    # ------------------------------------------------------------------
    # Connector-specific: _source override
    # ------------------------------------------------------------------

    def test_source_includes_format_csv(self):
        connector = ManualUploadConnector(self.mock_config)
        records = list(connector.fetch_data("batch-src"))
        assert records[0]["_source"] == "manual_upload:csv"

    def test_source_includes_format_json(self):
        config = {
            **self.mock_config,
            "file_format": "json",
            "file_data": '[{"hostname": "SRV-001"}]',
        }
        connector = ManualUploadConnector(config)
        records = list(connector.fetch_data("batch-src"))
        assert records[0]["_source"] == "manual_upload:json"

    # ------------------------------------------------------------------
    # Connector-specific: _record_number metadata
    # ------------------------------------------------------------------

    def test_record_number_injected(self):
        csv_data = _read_fixture("sample_csv.csv")
        config = {**self.mock_config, "file_data": csv_data}
        connector = ManualUploadConnector(config)
        records = list(connector.fetch_data("batch-num"))
        assert records[0]["_record_number"] == 1
        assert records[1]["_record_number"] == 2
        assert records[2]["_record_number"] == 3

    # ------------------------------------------------------------------
    # Connector-specific: CSV parsing
    # ------------------------------------------------------------------

    def test_csv_parsing(self):
        csv_data = _read_fixture("sample_csv.csv")
        config = {**self.mock_config, "file_data": csv_data}
        connector = ManualUploadConnector(config)
        records = list(connector.fetch_data("batch-csv"))
        assert len(records) == 3
        assert records[0]["hostname"] == "DESKTOP-WIN001"
        assert records[0]["serial_number"] == "SN-001"

    def test_csv_custom_delimiter(self):
        tsv_data = "hostname\tip\nSRV-001\t10.0.1.1\n"
        config = {**self.mock_config, "file_data": tsv_data, "delimiter": "\t"}
        connector = ManualUploadConnector(config)
        records = list(connector.fetch_data("batch-tsv"))
        assert len(records) == 1
        assert records[0]["hostname"] == "SRV-001"

    # ------------------------------------------------------------------
    # Connector-specific: JSON parsing
    # ------------------------------------------------------------------

    def test_json_array_parsing(self):
        json_data = _read_fixture("sample_json.json")
        config = {
            **self.mock_config,
            "file_format": "json",
            "file_data": json_data,
        }
        connector = ManualUploadConnector(config)
        records = list(connector.fetch_data("batch-json"))
        assert len(records) == 2
        assert records[0]["hostname"] == "DESKTOP-WIN001"

    def test_json_single_object(self):
        config = {
            **self.mock_config,
            "file_format": "json",
            "file_data": '{"hostname": "SRV-001", "ip": "10.0.1.1"}',
        }
        connector = ManualUploadConnector(config)
        records = list(connector.fetch_data("batch-single"))
        assert len(records) == 1
        assert records[0]["hostname"] == "SRV-001"

    def test_ndjson_parsing(self):
        ndjson = '{"hostname": "A"}\n{"hostname": "B"}\n'
        config = {
            **self.mock_config,
            "file_format": "json",
            "file_data": ndjson,
        }
        connector = ManualUploadConnector(config)
        records = list(connector.fetch_data("batch-ndjson"))
        assert len(records) == 2

    # ------------------------------------------------------------------
    # Connector-specific: empty/invalid data
    # ------------------------------------------------------------------

    def test_empty_file_connection_fails(self):
        config = {**self.mock_config, "file_data": "hostname\n"}
        connector = ManualUploadConnector(config)
        result = connector.test_connection()
        assert result["success"] is False

    def test_no_file_data_raises(self):
        config = {**self.mock_config, "file_data": None}
        connector = ManualUploadConnector(config)
        with pytest.raises(ValueError, match="No file data"):
            list(connector.fetch_data("batch-err"))

    # ------------------------------------------------------------------
    # Connector-specific: unsupported format
    # ------------------------------------------------------------------

    def test_unsupported_format_raises(self):
        config = {**self.mock_config, "file_format": "xml"}
        with pytest.raises(ValueError, match="Unsupported format"):
            ManualUploadConnector(config)

    # ------------------------------------------------------------------
    # Connector-specific: no auth
    # ------------------------------------------------------------------

    def test_no_auth_strategy(self):
        connector = ManualUploadConnector(self.mock_config)
        assert connector.auth is None

    # ------------------------------------------------------------------
    # Connector-specific: schema
    # ------------------------------------------------------------------

    def test_schema_infers_fields_from_data(self):
        csv_data = _read_fixture("sample_csv.csv")
        config = {**self.mock_config, "file_data": csv_data}
        connector = ManualUploadConnector(config)
        schema = connector.get_schema()
        field_names = {f["name"] for f in schema["fields"]}
        assert "hostname" in field_names
        assert "ip_address" in field_names
        assert "serial_number" in field_names
