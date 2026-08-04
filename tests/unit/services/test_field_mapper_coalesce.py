# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for FieldMapper coalesce operation and TransformationValidator coalesce validation.
"""

import pytest

from src.modules.integrations.services.field_mapper import FieldMapper
from src.modules.integrations.services.transformation_validator import TransformationValidator


class TestFieldMapperCoalesce:
    """Tests for coalesce operation in FieldMapper._apply_transformation."""

    def setup_method(self):
        self.mapper = FieldMapper(field_mapping={})

    def _apply(self, data, operations):
        transformation = {
            'target_field': 'result',
            'operations': operations,
        }
        return self.mapper._apply_transformation(data, transformation)

    def test_returns_first_non_null(self):
        data = {'a': None, 'b': 'second', 'c': 'third'}
        result = self._apply(data, [
            {'type': 'coalesce', 'fields': ['a', 'b', 'c'], 'default': 'fallback'}
        ])
        assert result == 'second'

    def test_returns_first_field_when_present(self):
        data = {'a': 'first', 'b': 'second'}
        result = self._apply(data, [
            {'type': 'coalesce', 'fields': ['a', 'b'], 'default': ''}
        ])
        assert result == 'first'

    def test_returns_default_when_all_null(self):
        data = {'a': None, 'b': None}
        result = self._apply(data, [
            {'type': 'coalesce', 'fields': ['a', 'b'], 'default': 'fallback'}
        ])
        assert result == 'fallback'

    def test_returns_default_when_all_empty_strings(self):
        data = {'a': '', 'b': '  '}
        result = self._apply(data, [
            {'type': 'coalesce', 'fields': ['a', 'b'], 'default': 'fallback'}
        ])
        assert result == 'fallback'

    def test_returns_default_when_fields_missing(self):
        data = {'x': 'unrelated'}
        result = self._apply(data, [
            {'type': 'coalesce', 'fields': ['a', 'b'], 'default': 'none'}
        ])
        assert result == 'none'

    def test_nested_field_path(self):
        data = {'network': {'ip': '10.0.0.1'}, 'hostname': None}
        result = self._apply(data, [
            {'type': 'coalesce', 'fields': ['hostname', 'network.ip'], 'default': ''}
        ])
        assert result == '10.0.0.1'

    def test_empty_default_returns_none(self):
        """When all fields are null and default is empty, result is None (stripped empty)."""
        data = {'a': None}
        result = self._apply(data, [
            {'type': 'coalesce', 'fields': ['a'], 'default': ''}
        ])
        assert result is None

    def test_coalesce_with_subsequent_operation(self):
        """Coalesce result should feed into the next operation in the chain."""
        data = {'a': None, 'b': 'hello world'}
        result = self._apply(data, [
            {'type': 'coalesce', 'fields': ['a', 'b'], 'default': ''},
            {'type': 'uppercase'},
        ])
        assert result == 'HELLO WORLD'


class TestTransformationValidatorCoalesce:
    """Tests for coalesce validation in TransformationValidator."""

    def setup_method(self):
        self.validator = TransformationValidator()

    def _make_transform(self, operations):
        return {
            'type': 'transform',
            'target_field': 'hostname',
            'operations': operations,
        }

    def test_valid_coalesce(self):
        valid, errors = self.validator.validate_transformation(
            self._make_transform([
                {'type': 'coalesce', 'fields': ['name', 'deviceName'], 'default': ''}
            ])
        )
        assert valid is True
        assert errors == []

    def test_missing_fields(self):
        valid, errors = self.validator.validate_transformation(
            self._make_transform([
                {'type': 'coalesce', 'default': ''}
            ])
        )
        assert valid is False
        assert any("'fields'" in e for e in errors)

    def test_empty_fields_list(self):
        valid, errors = self.validator.validate_transformation(
            self._make_transform([
                {'type': 'coalesce', 'fields': [], 'default': ''}
            ])
        )
        assert valid is False
        assert any("'fields'" in e for e in errors)

    def test_missing_default(self):
        valid, errors = self.validator.validate_transformation(
            self._make_transform([
                {'type': 'coalesce', 'fields': ['name']}
            ])
        )
        assert valid is False
        assert any("'default'" in e for e in errors)

    def test_non_string_field_rejected(self):
        valid, errors = self.validator.validate_transformation(
            self._make_transform([
                {'type': 'coalesce', 'fields': [123], 'default': ''}
            ])
        )
        assert valid is False

    def test_empty_field_path_rejected(self):
        valid, errors = self.validator.validate_transformation(
            self._make_transform([
                {'type': 'coalesce', 'fields': [''], 'default': ''}
            ])
        )
        assert valid is False

    def test_validates_against_available_fields(self):
        validator = TransformationValidator(available_fields=['name', 'hostname'])
        valid, errors = validator.validate_transformation({
            'type': 'transform',
            'target_field': 'result',
            'operations': [
                {'type': 'coalesce', 'fields': ['name', 'bogus_field'], 'default': ''}
            ],
        })
        assert valid is False
        assert any('bogus_field' in e for e in errors)
