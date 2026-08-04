# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for business-level conflict detection in asset providers.

This test suite verifies that:
1. OS family conflicts (Windows + macOS on same asset) are detected
2. Manufacturer family conflicts (Dell + Lenovo on same asset) are detected
3. Excessive provider counts are flagged
4. Excessive hostname counts are flagged
5. Clean data produces zero findings
"""

import pytest
from uuid import uuid4

import pandas as pd

from src.modules.asset_generation.domain.conflict_detector import ConflictDetector


def _make_providers_df(rows):
    """Build a providers DataFrame from a list of dicts."""
    return pd.DataFrame(rows)


class TestOSFamilyConflicts:
    """Test OS family conflict detection."""

    def test_windows_macos_conflict(self):
        """Windows + macOS on the same asset is a conflict."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': 'Windows 10 Pro', 'manufacturer': None, 'hostname': 'ws01'},
            {'asset_id': asset_id, 'os_normalized': 'macOS Ventura', 'manufacturer': None, 'hostname': 'ws01'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['os_family_conflicts']['count'] == 1
        assert result['has_critical'] is True

    def test_windows_ios_conflict(self):
        """Windows + iOS on the same asset is a conflict."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': 'Windows 11', 'manufacturer': None, 'hostname': 'dev01'},
            {'asset_id': asset_id, 'os_normalized': 'iOS 17', 'manufacturer': None, 'hostname': 'dev01'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['os_family_conflicts']['count'] == 1

    def test_macos_android_conflict(self):
        """macOS + Android on the same asset is a conflict."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': 'macOS Sonoma', 'manufacturer': None, 'hostname': 'x'},
            {'asset_id': asset_id, 'os_normalized': 'Android 14', 'manufacturer': None, 'hostname': 'x'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['os_family_conflicts']['count'] == 1

    def test_same_os_family_no_conflict(self):
        """Multiple Windows versions on same asset is NOT a conflict."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': 'Windows 10', 'manufacturer': None, 'hostname': 'ws01'},
            {'asset_id': asset_id, 'os_normalized': 'Windows 11 Pro', 'manufacturer': None, 'hostname': 'ws01'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['os_family_conflicts']['count'] == 0

    def test_null_os_ignored(self):
        """Null OS values should not cause false positives."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': 'Windows 10', 'manufacturer': None, 'hostname': 'ws01'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'ws01'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['os_family_conflicts']['count'] == 0

    def test_apple_ios_prefix_matched(self):
        """'Apple iOS 17' should match the apple_ios family."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': 'Windows 10', 'manufacturer': None, 'hostname': 'x'},
            {'asset_id': asset_id, 'os_normalized': 'Apple iOS 17', 'manufacturer': None, 'hostname': 'x'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['os_family_conflicts']['count'] == 1

    def test_linux_not_in_conflict_pairs(self):
        """Linux + Windows should NOT be flagged (Linux is not in conflict pairs)."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': 'Windows 10', 'manufacturer': None, 'hostname': 'x'},
            {'asset_id': asset_id, 'os_normalized': 'Ubuntu 22.04', 'manufacturer': None, 'hostname': 'x'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['os_family_conflicts']['count'] == 0


class TestManufacturerConflicts:
    """Test manufacturer family conflict detection."""

    def test_dell_lenovo_conflict(self):
        """Dell + Lenovo on the same asset is a conflict."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': 'Dell Inc.', 'hostname': 'ws01'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': 'Lenovo', 'hostname': 'ws01'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['manufacturer_conflicts']['count'] == 1

    def test_hp_apple_conflict(self):
        """HP + Apple on the same asset is a conflict."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': 'Hewlett Packard', 'hostname': 'x'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': 'Apple', 'hostname': 'x'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['manufacturer_conflicts']['count'] == 1

    def test_same_manufacturer_family_no_conflict(self):
        """Multiple Dell variants on same asset is NOT a conflict."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': 'Dell Inc.', 'hostname': 'ws01'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': 'Dell Technologies', 'hostname': 'ws01'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['manufacturer_conflicts']['count'] == 0

    def test_null_manufacturer_ignored(self):
        """Null manufacturer should not cause false positives."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': 'Dell Inc.', 'hostname': 'ws01'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'ws01'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['manufacturer_conflicts']['count'] == 0

    def test_unknown_manufacturer_no_conflict(self):
        """Unknown manufacturers should not trigger conflicts."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': 'Acme Corp', 'hostname': 'ws01'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': 'Globex Inc', 'hostname': 'ws01'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['manufacturer_conflicts']['count'] == 0


class TestExcessiveProviders:
    """Test excessive provider count detection."""

    def test_over_threshold(self):
        """Assets with >30 providers should be flagged."""
        asset_id = str(uuid4())
        rows = [
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': f'h{i}'}
            for i in range(35)
        ]
        df = _make_providers_df(rows)

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['excessive_providers']['count'] == 1
        assert result['excessive_providers']['max_providers'] == 35

    def test_at_threshold_no_flag(self):
        """Assets with exactly 30 providers should NOT be flagged."""
        asset_id = str(uuid4())
        rows = [
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': f'h{i}'}
            for i in range(30)
        ]
        df = _make_providers_df(rows)

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['excessive_providers']['count'] == 0

    def test_under_threshold_no_flag(self):
        """Assets with <30 providers should NOT be flagged."""
        asset_id = str(uuid4())
        rows = [
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': f'h{i}'}
            for i in range(5)
        ]
        df = _make_providers_df(rows)

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['excessive_providers']['count'] == 0


class TestExcessiveHostnames:
    """Test excessive hostname count detection."""

    def test_over_threshold(self):
        """Assets with >3 distinct hostnames should be flagged."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'host-a'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'host-b'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'host-c'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'host-d'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['excessive_hostnames']['count'] == 1
        assert result['excessive_hostnames']['max_hostnames'] == 4

    def test_at_threshold_no_flag(self):
        """Assets with exactly 3 distinct hostnames should NOT be flagged."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'host-a'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'host-b'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'host-c'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['excessive_hostnames']['count'] == 0

    def test_duplicate_hostnames_not_counted(self):
        """Duplicate hostnames on the same asset should be collapsed."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'host-a'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'host-a'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'host-b'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['excessive_hostnames']['count'] == 0

    def test_null_hostnames_ignored(self):
        """Null hostnames should not be counted."""
        asset_id = str(uuid4())
        df = _make_providers_df([
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': 'host-a'},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': None},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': None},
            {'asset_id': asset_id, 'os_normalized': None, 'manufacturer': None, 'hostname': None},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['excessive_hostnames']['count'] == 0


class TestCleanData:
    """Test that clean data produces zero findings."""

    def test_no_conflicts_clean_data(self):
        """Clean data with separate assets should produce zero findings."""
        id1, id2 = str(uuid4()), str(uuid4())
        df = _make_providers_df([
            {'asset_id': id1, 'os_normalized': 'Windows 10', 'manufacturer': 'Dell Inc.', 'hostname': 'ws01'},
            {'asset_id': id1, 'os_normalized': 'Windows 10 Pro', 'manufacturer': 'Dell Inc.', 'hostname': 'ws01'},
            {'asset_id': id2, 'os_normalized': 'macOS Ventura', 'manufacturer': 'Apple', 'hostname': 'mac01'},
        ])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['total_conflicts'] == 0
        assert result['has_critical'] is False

    def test_empty_dataframe(self):
        """Empty DataFrame should produce zero findings."""
        df = pd.DataFrame(columns=['asset_id', 'os_normalized', 'manufacturer', 'hostname'])

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['total_conflicts'] == 0
        assert result['has_critical'] is False

    def test_missing_columns_handled(self):
        """DataFrame missing expected columns should not crash."""
        df = pd.DataFrame({'asset_id': [str(uuid4())], 'some_other_col': ['x']})

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['total_conflicts'] == 0

    def test_asset_ids_capped_at_ten(self):
        """Asset IDs list should be capped at 10 even with more conflicts."""
        rows = []
        for _ in range(15):
            asset_id = str(uuid4())
            rows.append({'asset_id': asset_id, 'os_normalized': 'Windows 10', 'manufacturer': None, 'hostname': 'x'})
            rows.append({'asset_id': asset_id, 'os_normalized': 'macOS Ventura', 'manufacturer': None, 'hostname': 'x'})
        df = _make_providers_df(rows)

        detector = ConflictDetector(uuid4())
        result = detector.detect_conflicts(df)

        assert result['os_family_conflicts']['count'] == 15
        assert len(result['os_family_conflicts']['asset_ids']) == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
