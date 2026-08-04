# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for stale mapping detection — _compute_mapping_hash helper."""

import pytest

from src.modules.integrations.services.mapping_service import _compute_mapping_hash


class TestComputeMappingHash:
    """Verify determinism, uniqueness, length, and key-order independence."""

    def test_hash_deterministic(self):
        mapping = {"hostname": "name", "ip": "ip_address"}
        h1 = _compute_mapping_hash(mapping)
        h2 = _compute_mapping_hash(mapping)
        assert h1 == h2

    def test_hash_differs_for_different_mapping(self):
        m1 = {"hostname": "name"}
        m2 = {"hostname": "name", "model": "model_id"}
        assert _compute_mapping_hash(m1) != _compute_mapping_hash(m2)

    def test_hash_is_16_chars(self):
        h = _compute_mapping_hash({"hostname": "name"})
        assert len(h) == 16

    def test_hash_ignores_key_order(self):
        m1 = {"a": "1", "b": "2"}
        m2 = {"b": "2", "a": "1"}
        assert _compute_mapping_hash(m1) == _compute_mapping_hash(m2)

    def test_hash_includes_transformations(self):
        mapping = {"hostname": "name"}
        h_no_tx = _compute_mapping_hash(mapping)
        h_with_tx = _compute_mapping_hash(mapping, {"concat": ["a", "b"]})
        assert h_no_tx != h_with_tx

    def test_hash_none_mapping_treated_as_empty(self):
        h1 = _compute_mapping_hash(None)
        h2 = _compute_mapping_hash({})
        assert h1 == h2

    def test_hash_none_transformations_treated_as_empty(self):
        mapping = {"hostname": "name"}
        h1 = _compute_mapping_hash(mapping, None)
        h2 = _compute_mapping_hash(mapping, {})
        assert h1 == h2

    def test_hash_is_hex_string(self):
        h = _compute_mapping_hash({"x": "y"})
        assert all(c in '0123456789abcdef' for c in h)
