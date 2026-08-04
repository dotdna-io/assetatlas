# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from src.modules.integrations.services.transformation_validator import TransformationValidator


class TestValidateFieldMappingMultiSource:
    """Tests for multi-source (list) field mapping validation."""

    def setup_method(self):
        self.validator = TransformationValidator()

    def test_array_of_strings_is_valid(self):
        mapping = {"ip": ["ip1", "ip2", "ip3"]}
        is_valid, errors = self.validator.validate_field_mapping(mapping)
        assert is_valid is True
        assert errors == []

    def test_empty_array_is_invalid(self):
        mapping = {"ip": []}
        is_valid, errors = self.validator.validate_field_mapping(mapping)
        assert is_valid is False
        assert any("Empty source list" in e for e in errors)

    def test_array_with_non_string_is_invalid(self):
        mapping = {"ip": ["ip1", 123, "ip3"]}
        is_valid, errors = self.validator.validate_field_mapping(mapping)
        assert is_valid is False
        assert any("index 1" in e for e in errors)

    def test_array_with_blank_string_is_invalid(self):
        mapping = {"ip": ["ip1", "  ", "ip3"]}
        is_valid, errors = self.validator.validate_field_mapping(mapping)
        assert is_valid is False
        assert any("index 1" in e for e in errors)

    def test_mixed_string_and_array_mapping_is_valid(self):
        mapping = {"hostname": "deviceName", "ip": ["ip1", "ip2"]}
        is_valid, errors = self.validator.validate_field_mapping(mapping)
        assert is_valid is True
        assert errors == []

    def test_single_element_array_is_valid(self):
        mapping = {"ip": ["primary_ip"]}
        is_valid, errors = self.validator.validate_field_mapping(mapping)
        assert is_valid is True

    def test_available_fields_validates_each_path_in_array(self):
        mapping = {"ip": ["ip1", "nonexistent"]}
        available = ["ip1", "ip2", "hostname"]
        validator = TransformationValidator(available_fields=available)
        is_valid, errors = validator.validate_field_mapping(mapping)
        assert is_valid is False
        assert any("nonexistent" in e for e in errors)
