# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for the shared data-filter evaluation (src.modules.integrations.shared.data_filters).

This is the single source of truth used by both connectors (ingest-time filtering
via BaseConnector._should_include_record) and the wizard's server-side
/preview/filter-count endpoint.
"""

from src.modules.integrations.shared import data_filters


class TestGetNestedValue:
    """Path resolution prefers a literal key, else traverses nested objects."""

    def test_reads_flat_dotted_key(self):
        # SentinelOne PowerQuery columns like "device.hostname" arrive as flat keys
        assert data_filters.get_nested_value({'device.hostname': 'DESKTOP-01'}, 'device.hostname') == 'DESKTOP-01'

    def test_reads_multi_segment_flat_dotted_key(self):
        assert data_filters.get_nested_value({'tag.hs.lentity': 'prod'}, 'tag.hs.lentity') == 'prod'

    def test_traverses_genuinely_nested_path(self):
        assert data_filters.get_nested_value({'network': {'ip': '10.0.0.1'}}, 'network.ip') == '10.0.0.1'

    def test_prefers_literal_key_over_nested_on_collision(self):
        record = {'device.hostname': 'FLAT-WINS', 'device': {'hostname': 'NESTED-LOSES'}}
        assert data_filters.get_nested_value(record, 'device.hostname') == 'FLAT-WINS'

    def test_missing_path_returns_none(self):
        assert data_filters.get_nested_value({'a': 1}, 'b.c') is None


class TestEvaluateCondition:
    def test_equals_is_case_insensitive_for_strings(self):
        assert data_filters.evaluate_condition('DESKTOP-01', 'equals', 'desktop-01') is True

    def test_equals_matches_boolean_against_its_spelling(self):
        # Post-unification, data_filters delegates to the registry, so an `equals`
        # on a boolean ingest value matches its string spelling (J-2 — pins the
        # intended behavior; the legacy type-aware path compared True == 'true' as
        # False).
        assert data_filters.evaluate_condition(True, 'equals', 'true') is True
        assert data_filters.evaluate_condition(False, 'equals', 'FALSE') is True
        assert data_filters.evaluate_condition(True, 'equals', 'false') is False

    def test_is_not_empty(self):
        assert data_filters.evaluate_condition('x', 'is_not_empty') is True

    def test_is_empty_on_empty_list(self):
        assert data_filters.evaluate_condition([], 'is_empty') is True

    def test_none_value_fails_binary_operator(self):
        assert data_filters.evaluate_condition(None, 'equals', 'x') is False

    def test_greater_than_coerces_numeric_strings(self):
        assert data_filters.evaluate_condition('5', 'greater_than', '3') is True

    def test_unknown_operator_keeps_record(self):
        # An unrecognised operator must not silently drop every record.
        assert data_filters.evaluate_condition('x', 'no_such_operator', 'y') is True


class TestEvaluateConditionRegistryRouting:
    def test_equals_routes_through_registry(self):
        assert data_filters.evaluate_condition("Jamf", "equals", "jamf") is True

    def test_is_true_still_coerces(self):
        assert data_filters.evaluate_condition("true", "is_true") is True
        assert data_filters.evaluate_condition("nope", "is_true") is False

    def test_unknown_still_fails_open(self):
        assert data_filters.evaluate_condition("x", "no_such_operator", "y") is True


class TestMatchesFilters:
    def test_equals_matches_flat_dotted_key(self):
        filters = [{'field': 'device.hostname', 'operator': 'equals', 'value': 'DESKTOP-01'}]
        assert data_filters.matches_filters({'device.hostname': 'DESKTOP-01'}, filters) is True

    def test_is_not_empty_keeps_record_with_flat_dotted_key(self):
        filters = [{'field': 'device.hostname', 'operator': 'is_not_empty'}]
        assert data_filters.matches_filters({'device.hostname': 'DESKTOP-01'}, filters) is True

    def test_collision_prefers_flat_key(self):
        filters = [{'field': 'device.hostname', 'operator': 'equals', 'value': 'FLAT-WINS'}]
        record = {'device.hostname': 'FLAT-WINS', 'device': {'hostname': 'NESTED-LOSES'}}
        assert data_filters.matches_filters(record, filters) is True

    def test_and_logic_excludes_when_one_filter_fails(self):
        filters = [
            {'field': 'device.hostname', 'operator': 'is_not_empty'},
            {'field': 'serial', 'operator': 'equals', 'value': 'NOPE'},
        ]
        assert data_filters.matches_filters({'device.hostname': 'X', 'serial': '123'}, filters) is False
