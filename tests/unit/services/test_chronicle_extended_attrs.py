# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for extended_attributes tracking in ChronicleService.

Verifies that extended_attributes (JSONB column with per-source nested dicts)
is tracked through chronicle change detection with deep dict comparison.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.modules.asset_generation.services.chronicle_service import ChronicleService


@pytest.fixture
def chronicle():
    """ChronicleService with a mocked db_adapter."""
    return ChronicleService(db_adapter=MagicMock())


@pytest.fixture
def event_time():
    return datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)


class TestExtendedAttributesChangeDetection:
    """Test that extended_attributes changes are detected in update events."""

    def test_value_change_within_data_source_detected(self, chronicle, event_time):
        """A value change inside a data source dict should produce an event."""
        current = {
            'asset-1': {
                'hostname': 'srv-01',
                'extended_attributes': {
                    'Armis': {'riskLevel': '60'},
                    'Intune': {'isEncrypted': 'true'},
                },
            }
        }
        new = {
            'asset-1': {
                'hostname': 'srv-01',
                'extended_attributes': {
                    'Armis': {'riskLevel': '85'},
                    'Intune': {'isEncrypted': 'true'},
                },
            }
        }

        events = chronicle.compare_and_generate_events(
            current, new, event_time=event_time
        )

        assert len(events) == 1
        event = events[0]
        assert event['event_type'] == 'attribute_updated'
        assert 'extended_attributes' in event['changed_attributes']
        change = event['attribute_changes']['extended_attributes']
        assert change['old'] == [current['asset-1']['extended_attributes']]
        assert change['new'] == [new['asset-1']['extended_attributes']]

    def test_new_data_source_appearing_detected(self, chronicle, event_time):
        """A new data source key appearing should produce an event."""
        current = {
            'asset-1': {
                'extended_attributes': {
                    'Armis': {'riskLevel': '60'},
                },
            }
        }
        new = {
            'asset-1': {
                'extended_attributes': {
                    'Armis': {'riskLevel': '60'},
                    'CrowdStrike': {'sensorVersion': '7.0'},
                },
            }
        }

        events = chronicle.compare_and_generate_events(
            current, new, event_time=event_time
        )

        assert len(events) == 1
        assert 'extended_attributes' in events[0]['changed_attributes']

    def test_identical_extended_attributes_no_event(self, chronicle, event_time):
        """Identical extended_attributes should produce no event."""
        attrs = {
            'Armis': {'riskLevel': '60'},
            'Intune': {'isEncrypted': 'true'},
        }
        current = {'asset-1': {'extended_attributes': attrs}}
        new = {'asset-1': {'extended_attributes': attrs.copy()}}

        events = chronicle.compare_and_generate_events(
            current, new, event_time=event_time
        )

        assert len(events) == 0

    def test_none_to_populated_triggers_event(self, chronicle, event_time):
        """Going from None to a populated dict should produce an event."""
        current = {'asset-1': {'extended_attributes': None}}
        new = {
            'asset-1': {
                'extended_attributes': {
                    'Armis': {'riskLevel': '60'},
                },
            }
        }

        events = chronicle.compare_and_generate_events(
            current, new, event_time=event_time
        )

        assert len(events) == 1
        assert 'extended_attributes' in events[0]['changed_attributes']

    def test_deep_diff_shows_per_source_granularity(self, chronicle, event_time):
        """attribute_changes should contain the full old and new dicts."""
        old_attrs = {
            'Armis': {'riskLevel': '60', 'deviceType': 'server'},
            'Intune': {'isEncrypted': 'true'},
        }
        new_attrs = {
            'Armis': {'riskLevel': '85', 'deviceType': 'server'},
            'Intune': {'isEncrypted': 'true'},
        }
        current = {'asset-1': {'extended_attributes': old_attrs}}
        new = {'asset-1': {'extended_attributes': new_attrs}}

        events = chronicle.compare_and_generate_events(
            current, new, event_time=event_time
        )

        assert len(events) == 1
        change = events[0]['attribute_changes']['extended_attributes']
        # old/new are list-wrapped for consistency with other attributes
        assert change['old'] == [old_attrs]
        assert change['new'] == [new_attrs]
        # added/removed are empty for dict comparisons
        assert change['added'] == []
        assert change['removed'] == []


class TestExtendedAttributesCreationSnapshot:
    """Test that creation events store dicts as-is, not list-wrapped."""

    def test_creation_snapshot_stores_dict_as_is(self, chronicle, event_time):
        """Creation full_snapshot should store dict values directly."""
        new = {
            'asset-1': {
                'hostname': 'srv-01',
                'extended_attributes': {
                    'Armis': {'riskLevel': '60'},
                },
            }
        }

        events = chronicle.compare_and_generate_events(
            {}, new, event_time=event_time
        )

        assert len(events) == 1
        snapshot = events[0]['full_snapshot']
        # Dict should be stored as-is, not wrapped in a list
        assert snapshot['extended_attributes'] == {
            'Armis': {'riskLevel': '60'},
        }
        # Non-dict values should still be list-wrapped
        assert snapshot['hostname'] == ['srv-01']


class TestDictsEqual:
    """Test the _dicts_equal static method."""

    def test_equal_dicts(self):
        a = {'Armis': {'riskLevel': '60'}, 'Intune': {'isEncrypted': 'true'}}
        b = {'Intune': {'isEncrypted': 'true'}, 'Armis': {'riskLevel': '60'}}
        assert ChronicleService._dicts_equal(a, b) is True

    def test_unequal_dicts(self):
        a = {'Armis': {'riskLevel': '60'}}
        b = {'Armis': {'riskLevel': '85'}}
        assert ChronicleService._dicts_equal(a, b) is False

    def test_both_none(self):
        assert ChronicleService._dicts_equal(None, None) is True

    def test_one_none(self):
        assert ChronicleService._dicts_equal(None, {'a': 1}) is False
        assert ChronicleService._dicts_equal({'a': 1}, None) is False


