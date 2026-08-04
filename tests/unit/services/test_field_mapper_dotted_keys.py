# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for FieldMapper extraction of flat source keys that literally contain dots.

SentinelOne PowerQuery (and other SIEM-passthrough sources) return rows as flat
dicts whose column names contain dots, e.g. {"device.hostname": "DESKTOP-01"}.
These must be read as literal keys, not traversed as nested object paths.
"""

from src.modules.integrations.services.field_mapper import FieldMapper


class TestFieldMapperFlatDottedKeys:
    """Flat source keys containing dots (SentinelOne PowerQuery columns)."""

    def test_identifier_field_from_flat_dotted_key(self):
        """A literal "device.hostname" key maps to hostname, not None."""
        mapper = FieldMapper(field_mapping={'hostname': 'device.hostname'})
        mapped = mapper.map({'device.hostname': 'DESKTOP-01'})
        assert mapped['hostname'] == ['DESKTOP-01']

    def test_scalar_field_from_multi_segment_flat_dotted_key(self):
        """A literal "tag.hs.lentity" key (three segments) maps correctly."""
        mapper = FieldMapper(field_mapping={'user': 'tag.hs.lentity'})
        mapped = mapper.map({'tag.hs.lentity': 'prod-entity'})
        assert mapped['user'] == 'prod-entity'

    def test_nested_path_still_resolves(self):
        """Regression: genuinely nested objects still traverse by dot path."""
        mapper = FieldMapper(field_mapping={'ip': 'network.ipAddress'})
        mapped = mapper.map({'network': {'ipAddress': '10.0.0.1'}})
        assert mapped['ip'] == ['10.0.0.1']

    def test_array_index_path_still_resolves(self):
        """Regression: array-index notation still works on nested data."""
        mapper = FieldMapper(field_mapping={'ip': 'addresses[0]'})
        mapped = mapper.map({'addresses': ['10.0.0.1', '10.0.0.2']})
        assert mapped['ip'] == ['10.0.0.1']

    def test_flat_dotted_key_preferred_over_nested_collision(self):
        """When both a literal dotted key and a nested path exist, prefer the literal key."""
        mapper = FieldMapper(field_mapping={'hostname': 'device.hostname'})
        mapped = mapper.map({
            'device.hostname': 'FLAT-WINS',
            'device': {'hostname': 'NESTED-LOSES'},
        })
        assert mapped['hostname'] == ['FLAT-WINS']
