# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for AssetHistoryService — source field fallback logic (issue #411)
and dict-valued attribute explosion (issue #549).

The service transforms chronicle events into per-attribute UI events.
Every event must have a non-null ``source`` field; when the chronicle
data doesn't carry an explicit source, the fallback is ``"AssetAtlas"``.

Dict-valued chronicle attributes (``extended_attributes``, ``posture_rules``)
are exploded into one row per changed leaf key so the UI shows scalar values
instead of JSON blobs.
"""

from unittest.mock import MagicMock

import pytest

from src.modules.assets.services.asset_history_service import AssetHistoryService


@pytest.fixture
def service():
    db_adapter = MagicMock()
    return AssetHistoryService(db_adapter)


# ------------------------------------------------------------------
# _transform_update_event
# ------------------------------------------------------------------

class TestTransformUpdateEventSource:
    """source field in attribute_updated events."""

    def test_explicit_source_preserved(self, service):
        event = {
            'id': 1,
            'time': '2026-01-01T00:00:00',
            'attribute_changes': {
                'hostname': {'old': ['a'], 'new': ['b'], 'source': 'CrowdStrike'},
            },
        }
        results = service._transform_update_event(1, '2026-01-01T00:00:00', event)
        assert results[0]['source'] == 'CrowdStrike'

    def test_missing_source_falls_back_to_assetatlas(self, service):
        event = {
            'id': 2,
            'time': '2026-01-01T00:00:00',
            'attribute_changes': {
                'ip': {'old': ['1.1.1.1'], 'new': ['2.2.2.2']},
            },
        }
        results = service._transform_update_event(2, '2026-01-01T00:00:00', event)
        assert results[0]['source'] == 'AssetAtlas'

    def test_none_source_falls_back_to_assetatlas(self, service):
        event = {
            'id': 3,
            'time': '2026-01-01T00:00:00',
            'attribute_changes': {
                'mac': {'old': [], 'new': ['AA:BB'], 'source': None},
            },
        }
        results = service._transform_update_event(3, '2026-01-01T00:00:00', event)
        assert results[0]['source'] == 'AssetAtlas'

    def test_empty_string_source_falls_back(self, service):
        event = {
            'id': 4,
            'time': '2026-01-01T00:00:00',
            'attribute_changes': {
                'os': {'old': [], 'new': ['Linux'], 'source': ''},
            },
        }
        results = service._transform_update_event(4, '2026-01-01T00:00:00', event)
        assert results[0]['source'] == 'AssetAtlas'


# ------------------------------------------------------------------
# _transform_creation_event
# ------------------------------------------------------------------

class TestTransformCreationEventSource:
    """source field in asset_created / asset_readded events."""

    def test_creation_event_source_is_assetatlas(self, service):
        event = {
            'id': 10,
            'time': '2026-01-01T00:00:00',
            'full_snapshot': {'hostname': ['server1'], 'ip': ['10.0.0.1']},
        }
        results = service._transform_creation_event(10, '2026-01-01T00:00:00', event)
        assert len(results) == 2
        for r in results:
            assert r['source'] == 'AssetAtlas'


# ------------------------------------------------------------------
# _transform_removal_event
# ------------------------------------------------------------------

class TestTransformRemovalEventSource:
    """source field in asset_removed events."""

    def test_removal_event_source_is_assetatlas(self, service):
        results = service._transform_removal_event(20, '2026-01-01T00:00:00')
        assert len(results) == 1
        assert results[0]['source'] == 'AssetAtlas'


# ------------------------------------------------------------------
# Dict-valued attribute explosion (issue #549)
# ------------------------------------------------------------------

class TestTransformUpdateEventDictExplosion:
    """Dict-valued attributes drill into per-leaf rows instead of JSON blobs."""

    def test_single_leaf_change_explodes_to_one_row(self, service):
        event = {
            'id': 100,
            'time': '2026-04-27T00:00:00',
            'attribute_changes': {
                'extended_attributes': {
                    'old': [{'enrichment': {'vendor': 'Dell', 'site': 'NY'}}],
                    'new': [{'enrichment': {'vendor': 'HP', 'site': 'NY'}}],
                    'source': 'enrichment_csv',
                }
            },
        }
        results = service._transform_update_event(100, '2026-04-27T00:00:00', event)
        assert len(results) == 1
        assert results[0]['attribute'] == 'extended_attributes.enrichment.vendor'
        assert results[0]['old_value'] == 'Dell'
        assert results[0]['new_value'] == 'HP'
        assert results[0]['action'] == 'updated'
        assert results[0]['source'] == 'enrichment_csv'

    def test_multiple_leaf_changes_explode_to_multiple_rows(self, service):
        event = {
            'id': 101,
            'time': '2026-04-27T00:00:00',
            'attribute_changes': {
                'posture_rules': {
                    'old': [{'rule_a': 'pass', 'rule_b': 'pass'}],
                    'new': [{'rule_a': 'fail', 'rule_b': 'pass', 'rule_c': 'pass'}],
                }
            },
        }
        results = service._transform_update_event(101, '2026-04-27T00:00:00', event)
        attrs = {r['attribute']: r for r in results}
        assert set(attrs.keys()) == {'posture_rules.rule_a', 'posture_rules.rule_c'}
        assert attrs['posture_rules.rule_a']['old_value'] == 'pass'
        assert attrs['posture_rules.rule_a']['new_value'] == 'fail'
        assert attrs['posture_rules.rule_a']['action'] == 'updated'
        assert attrs['posture_rules.rule_c']['old_value'] is None
        assert attrs['posture_rules.rule_c']['new_value'] == 'pass'
        assert attrs['posture_rules.rule_c']['action'] == 'added'

    def test_dict_added_emits_added_row_per_leaf(self, service):
        event = {
            'id': 102,
            'time': '2026-04-27T00:00:00',
            'attribute_changes': {
                'extended_attributes': {
                    'old': [],
                    'new': [{'vendor': 'HP', 'tier': 'gold'}],
                }
            },
        }
        results = service._transform_update_event(102, '2026-04-27T00:00:00', event)
        assert len(results) == 2
        for r in results:
            assert r['action'] == 'added'
            assert r['old_value'] is None
        attrs = {r['attribute']: r['new_value'] for r in results}
        assert attrs == {
            'extended_attributes.vendor': 'HP',
            'extended_attributes.tier': 'gold',
        }

    def test_dict_removed_emits_removed_row_per_leaf(self, service):
        event = {
            'id': 103,
            'time': '2026-04-27T00:00:00',
            'attribute_changes': {
                'extended_attributes': {
                    'old': [{'vendor': 'HP', 'tier': 'gold'}],
                    'new': [],
                }
            },
        }
        results = service._transform_update_event(103, '2026-04-27T00:00:00', event)
        assert len(results) == 2
        for r in results:
            assert r['action'] == 'removed'
            assert r['new_value'] is None
        attrs = {r['attribute']: r['old_value'] for r in results}
        assert attrs == {
            'extended_attributes.vendor': 'HP',
            'extended_attributes.tier': 'gold',
        }

    def test_scalar_attribute_unchanged_path(self, service):
        """Non-dict attributes still emit a single row (no regression)."""
        event = {
            'id': 104,
            'time': '2026-04-27T00:00:00',
            'attribute_changes': {
                'hostname': {'old': ['old-host'], 'new': ['new-host']},
            },
        }
        results = service._transform_update_event(104, '2026-04-27T00:00:00', event)
        assert len(results) == 1
        assert results[0]['attribute'] == 'hostname'
        assert results[0]['old_value'] == 'old-host'
        assert results[0]['new_value'] == 'new-host'

    def test_mixed_scalar_and_dict_attributes(self, service):
        event = {
            'id': 105,
            'time': '2026-04-27T00:00:00',
            'attribute_changes': {
                'hostname': {'old': ['old-host'], 'new': ['new-host']},
                'extended_attributes': {
                    'old': [{'vendor': 'Dell'}],
                    'new': [{'vendor': 'HP'}],
                },
            },
        }
        results = service._transform_update_event(105, '2026-04-27T00:00:00', event)
        attrs = {r['attribute']: r for r in results}
        assert set(attrs.keys()) == {'hostname', 'extended_attributes.vendor'}
        assert attrs['hostname']['new_value'] == 'new-host'
        assert attrs['extended_attributes.vendor']['new_value'] == 'HP'

    def test_no_changes_in_dict_emits_no_rows(self, service):
        """If chronicle somehow emitted an unchanged dict, we drop it."""
        event = {
            'id': 106,
            'time': '2026-04-27T00:00:00',
            'attribute_changes': {
                'extended_attributes': {
                    'old': [{'vendor': 'HP'}],
                    'new': [{'vendor': 'HP'}],
                }
            },
        }
        results = service._transform_update_event(106, '2026-04-27T00:00:00', event)
        assert results == []

    def test_deeply_nested_dict_uses_dotted_path(self, service):
        event = {
            'id': 107,
            'time': '2026-04-27T00:00:00',
            'attribute_changes': {
                'extended_attributes': {
                    'old': [{'a': {'b': {'c': 1}}}],
                    'new': [{'a': {'b': {'c': 2}}}],
                }
            },
        }
        results = service._transform_update_event(107, '2026-04-27T00:00:00', event)
        assert len(results) == 1
        assert results[0]['attribute'] == 'extended_attributes.a.b.c'
        assert results[0]['old_value'] == '1'
        assert results[0]['new_value'] == '2'

    def test_dict_id_includes_dotted_path(self, service):
        """Each row's id is unique per leaf so React keys don't collide."""
        event = {
            'id': 108,
            'time': '2026-04-27T00:00:00',
            'attribute_changes': {
                'extended_attributes': {
                    'old': [{'a': 1, 'b': 2}],
                    'new': [{'a': 9, 'b': 8}],
                }
            },
        }
        results = service._transform_update_event(108, '2026-04-27T00:00:00', event)
        ids = {r['id'] for r in results}
        assert ids == {'108-extended_attributes.a', '108-extended_attributes.b'}
