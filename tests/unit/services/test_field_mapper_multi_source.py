# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from src.modules.integrations.services.field_mapper import FieldMapper


class TestExtractValueArrayBehavior:
    """Tests that _extract_value returns full arrays instead of first element."""

    def setup_method(self):
        self.mapper = FieldMapper(field_mapping={}, transformations={})

    def test_nested_array_returns_full_list(self):
        data = {"ips": ["10.0.0.1", "10.0.0.2", "10.0.0.3"]}
        result = self.mapper._extract_value(data, "ips")
        assert result == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]

    def test_jsonpath_array_returns_full_list(self):
        data = {"network": {"interfaces": [{"ip": "10.0.0.1"}, {"ip": "10.0.0.2"}]}}
        result = self.mapper._extract_value(data, "$.network.interfaces[*].ip")
        assert isinstance(result, list)
        assert len(result) == 2

    def test_scalar_value_still_returns_scalar(self):
        data = {"hostname": "server1"}
        result = self.mapper._extract_value(data, "hostname")
        assert result == "server1"

    def test_single_element_array_returns_list(self):
        data = {"ips": ["10.0.0.1"]}
        result = self.mapper._extract_value(data, "ips")
        assert result == ["10.0.0.1"]

    def test_empty_array_returns_none(self):
        data = {"ips": []}
        result = self.mapper._extract_value(data, "ips")
        assert result is None


class TestResolveMapping:
    """Tests for multi-source field mapping resolution."""

    def test_single_source_returns_one_element_array(self):
        mapper = FieldMapper(
            field_mapping={"hostname": "deviceName"},
            transformations={}
        )
        data = {"deviceName": "server1"}
        result = mapper.map(data)
        assert result["hostname"] == ["server1"]

    def test_multi_source_collects_unique_values(self):
        mapper = FieldMapper(
            field_mapping={"ip": ["ip1", "ip2", "ip3"]},
            transformations={}
        )
        data = {"ip1": "10.0.0.1", "ip2": "10.0.0.2", "ip3": "10.0.0.3"}
        result = mapper.map(data)
        assert result["ip"] == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]

    def test_multi_source_deduplicates(self):
        mapper = FieldMapper(
            field_mapping={"ip": ["ip1", "ip2", "ip3"]},
            transformations={}
        )
        data = {"ip1": "10.0.0.1", "ip2": "10.0.0.1", "ip3": "10.0.0.2"}
        result = mapper.map(data)
        assert result["ip"] == ["10.0.0.1", "10.0.0.2"]

    def test_multi_source_skips_nulls(self):
        mapper = FieldMapper(
            field_mapping={"ip": ["ip1", "ip2", "ip3"]},
            transformations={}
        )
        data = {"ip1": "10.0.0.1", "ip3": "10.0.0.3"}
        result = mapper.map(data)
        assert result["ip"] == ["10.0.0.1", "10.0.0.3"]

    def test_multi_source_all_null_returns_none(self):
        mapper = FieldMapper(
            field_mapping={"ip": ["ip1", "ip2"]},
            transformations={}
        )
        data = {"other_field": "value"}
        result = mapper.map(data)
        assert result.get("ip") is None

    def test_multi_source_flattens_array_source(self):
        mapper = FieldMapper(
            field_mapping={"ip": ["ips", "mgmt_ip"]},
            transformations={}
        )
        data = {"ips": ["10.0.0.1", "10.0.0.2"], "mgmt_ip": "10.0.0.3"}
        result = mapper.map(data)
        assert result["ip"] == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]

    def test_type_conversion_applied_element_wise(self):
        mapper = FieldMapper(
            field_mapping={"mac": ["mac1", "mac2"]},
            transformations={}
        )
        data = {"mac1": "AA-BB-CC-DD-EE-FF", "mac2": "11:22:33:44:55:66"}
        result = mapper.map(data)
        assert "AA:BB:CC:DD:EE:FF" in result["mac"]
        assert "11:22:33:44:55:66" in result["mac"]

    def test_type_conversion_filters_invalid_values(self):
        mapper = FieldMapper(
            field_mapping={"ip": ["ip1", "ip2"]},
            transformations={}
        )
        data = {"ip1": "10.0.0.1", "ip2": "not_an_ip"}
        result = mapper.map(data)
        assert result["ip"] == ["10.0.0.1"]

    def test_multi_source_with_virtual_field(self):
        mapper = FieldMapper(
            field_mapping={"hostname": ["raw_host", "computed_host"]},
            transformations={
                "computed_host": {
                    "type": "transform",
                    "operations": [
                        {"type": "field", "field": "fqdn"},
                        {"type": "regex_extract", "pattern": "^([^.]+)"}
                    ]
                }
            }
        )
        data = {"raw_host": "server1", "fqdn": "server2.example.com"}
        result = mapper.map(data)
        assert "server1" in result["hostname"]
        assert "server2" in result["hostname"]

    def test_backward_compat_string_mapping(self):
        mapper = FieldMapper(
            field_mapping={"hostname": "deviceName", "ip": "ipAddress"},
            transformations={}
        )
        data = {"deviceName": "server1", "ipAddress": "10.0.0.1"}
        result = mapper.map(data)
        assert result["hostname"] == ["server1"]
        assert result["ip"] == ["10.0.0.1"]

    def test_non_identifier_fields_stay_scalar(self):
        """os, manufacturer, model, etc. should NOT be wrapped in arrays."""
        mapper = FieldMapper(
            field_mapping={"hostname": "host", "os": "operating_system", "manufacturer": "vendor"},
            transformations={}
        )
        data = {"host": "server1", "operating_system": "Ubuntu 22.04", "vendor": "Dell"}
        result = mapper.map(data)
        assert result["hostname"] == ["server1"]
        assert result["os"] == "Ubuntu 22.04"
        assert result["manufacturer"] == "Dell"


class TestExtendedFieldsMultiSource:
    """Tests for _collect_extended_fields with multi-source mappings."""

    def test_multi_source_extended_field_collects_values(self):
        mapper = FieldMapper(
            field_mapping={
                "hostname": "host",
                "custom_field": ["src1", "src2"]
            },
            transformations={}
        )
        data = {"host": "server1", "src1": "val1", "src2": "val2"}
        result = mapper.map(data)
        assert result["extended_fields"]["custom_field"] == ["val1", "val2"]

    def test_single_source_extended_field_stays_scalar(self):
        mapper = FieldMapper(
            field_mapping={
                "hostname": "host",
                "custom_field": "source"
            },
            transformations={}
        )
        data = {"host": "server1", "source": "value"}
        result = mapper.map(data)
        assert result["extended_fields"]["custom_field"] == "value"
