# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for the FieldMapper ``current_timestamp`` operation and its validation.

The ``current_timestamp`` operation lets an integration that produces no
timestamp of its own stamp a field (typically ``last_time`` / "Last Seen") with
the collection time, so presence-based sources get a meaningful last-seen value.
"""

from datetime import datetime, timezone

from src.modules.integrations.services.field_mapper import FieldMapper
from src.modules.integrations.services.transformation_validator import TransformationValidator


class TestFieldMapperCurrentTimestamp:
    """Tests for the current_timestamp operation in FieldMapper._apply_transformation."""

    def setup_method(self):
        self.mapper = FieldMapper(field_mapping={})

    def _apply(self, data, operations):
        transformation = {
            'target_field': 'result',
            'operations': operations,
        }
        return self.mapper._apply_transformation(data, transformation)

    def test_emits_current_utc_time_as_iso8601(self):
        """A lone current_timestamp op yields a tz-aware ISO-8601 value close to now."""
        before = datetime.now(timezone.utc)
        result = self._apply({}, [{'type': 'current_timestamp'}])
        after = datetime.now(timezone.utc)

        assert result is not None
        parsed = datetime.fromisoformat(result)
        assert parsed.tzinfo is not None, "timestamp must be timezone-aware"
        # Falls within the window bracketing the call (no clock skew assumptions).
        assert before <= parsed <= after

    def test_ignores_source_data(self):
        """The op does not depend on any source field — empty payload is fine."""
        result = self._apply({'unrelated': 'value'}, [{'type': 'current_timestamp'}])
        assert result is not None
        assert datetime.fromisoformat(result).tzinfo is not None

    def test_round_trips_through_timestamp_conversion(self):
        """Output parses via the same converter last_time mapping applies to it."""
        result = self._apply({}, [{'type': 'current_timestamp'}])
        converted = self.mapper._convert_to_timestamp(result)
        assert converted is not None
        assert datetime.fromisoformat(converted).tzinfo is not None


class TestTransformationValidatorCurrentTimestamp:
    """Tests for current_timestamp validation in TransformationValidator."""

    def setup_method(self):
        self.validator = TransformationValidator()

    def _make_transform(self, operations):
        return {
            'type': 'transform',
            'target_field': 'last_time',
            'operations': operations,
        }

    def test_lone_current_timestamp_is_valid(self):
        """A transform consisting only of current_timestamp is accepted as a data source."""
        valid, errors = self.validator.validate_transformation(
            self._make_transform([{'type': 'current_timestamp'}])
        )
        assert valid is True
        assert errors == []

    def test_current_timestamp_needs_no_parameters(self):
        """Extra/absent params don't matter — the op takes no arguments."""
        valid, errors = self.validator.validate_transformation(
            self._make_transform([
                {'type': 'current_timestamp'},
                {'type': 'trim'},
            ])
        )
        assert valid is True
        assert errors == []
