# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for Stage 3 mapping validation (#281).

Validates that loaded asset_id_mappings are filtered through current
validation rules so historical entries with now-invalid component
values don't persist as merge keys.

NOTE: Only is_valid_component_value is applied (basic sanity: no spaces,
slashes, etc.). Hostname validation (length, letters, generics) is NOT
applied because the ID assignment stage accepts any non-IP, non-virtual-MAC
component regardless of hostname validity.
"""

import uuid

from unittest.mock import Mock

from src.modules.asset_generation.domain.asset_validator import AssetValidator
from src.modules.asset_generation.pipeline.stages.s03_load_mappings import (
    LoadMappingsStage,
)


def _make_stage():
    """Create a LoadMappingsStage with a lightweight mock service."""
    service = Mock()
    service.validator = AssetValidator()
    return LoadMappingsStage(service)


def _uid():
    return str(uuid.uuid4())


class TestMappingValidation:
    """Test _validate_mappings filters invalid historical entries."""

    def test_valid_hostname_passes(self):
        stage = _make_stage()
        aid = _uid()
        validated, skipped = stage._validate_mappings({'server01': aid}, frozenset())
        assert 'server01' in validated
        assert skipped['invalid_value'] == 0

    def test_short_component_passes(self):
        """Short hostnames like '15' are accepted — ID assignment uses them."""
        stage = _make_stage()
        aid = _uid()
        validated, skipped = stage._validate_mappings({'15': aid}, frozenset())
        assert '15' in validated

    def test_hostname_with_spaces_filtered(self):
        """'iMac, uMac, weMac' should fail is_valid_component_value (spaces)."""
        stage = _make_stage()
        aid = _uid()
        validated, skipped = stage._validate_mappings(
            {'iMac, uMac, weMac': aid}, frozenset()
        )
        assert 'imac, umac, wemac' not in validated
        assert skipped['invalid_value'] == 1

    def test_numeric_only_hostname_passes(self):
        """Purely numeric hostnames are accepted — ID assignment uses them."""
        stage = _make_stage()
        aid = _uid()
        validated, skipped = stage._validate_mappings({'12345': aid}, frozenset())
        assert '12345' in validated

    def test_sn_prefix_passes(self):
        """sn: prefixed components pass validation."""
        stage = _make_stage()
        aid = _uid()
        validated, skipped = stage._validate_mappings({'sn:15': aid}, frozenset())
        assert 'sn:15' in validated
        assert skipped['invalid_value'] == 0

    def test_mac_passes(self):
        """MAC addresses pass validation."""
        stage = _make_stage()
        aid = _uid()
        validated, skipped = stage._validate_mappings(
            {'aa:bb:cc:dd:ee:ff': aid}, frozenset()
        )
        assert 'aa:bb:cc:dd:ee:ff' in validated
        assert skipped['invalid_value'] == 0

    def test_empty_component_filtered(self):
        stage = _make_stage()
        aid = _uid()
        validated, skipped = stage._validate_mappings({'': aid}, frozenset())
        assert len(validated) == 0
        assert skipped['invalid_value'] == 1

    def test_component_with_slashes_filtered(self):
        """Components with slashes should fail is_valid_component_value."""
        stage = _make_stage()
        aid = _uid()
        validated, skipped = stage._validate_mappings(
            {'path/to/thing': aid}, frozenset()
        )
        assert len(validated) == 0
        assert skipped['invalid_value'] == 1

    def test_generic_hostname_passes(self):
        """Generic hostnames like 'iphone' are accepted — filtering is
        done at the ID assignment stage, not at mapping load."""
        stage = _make_stage()
        aid = _uid()
        validated, skipped = stage._validate_mappings({'iphone': aid}, frozenset())
        assert 'iphone' in validated

    def test_mixed_valid_and_invalid(self):
        """Only structurally invalid entries should be filtered."""
        stage = _make_stage()
        aid1, aid2, aid3, aid4 = _uid(), _uid(), _uid(), _uid()
        raw = {
            'valid-server-01': aid1,
            '15': aid2,                      # short but accepted
            'sn:dell-sn-123': aid3,          # valid SN
            'has spaces bad': aid4,           # spaces → filtered
        }
        validated, skipped = stage._validate_mappings(raw, frozenset())
        assert 'valid-server-01' in validated
        assert '15' in validated
        assert 'sn:dell-sn-123' in validated
        assert 'has spaces bad' not in validated
        assert skipped['invalid_value'] == 1
