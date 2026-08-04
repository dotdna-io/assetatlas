# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for OS normalization edge cases (G6, G10)."""

import pytest

from src.modules.asset_generation.domain.os_normalization.hybrid_cpe_normalizer import HybridCPENormalizer  # noqa: E501


@pytest.fixture
def normalizer():
    return HybridCPENormalizer()


# =========================================================================
# Garbage rejection
# =========================================================================

class TestGarbageRejection:
    """Strings that aren't operating systems should be rejected."""

    @pytest.mark.parametrize("value", [
        "145.0.0.0",       # Chrome browser version
        "84.0.4147.125",   # Chrome browser version
        "0.0.0.0",         # Invalid IP-like
    ])
    def test_browser_and_app_versions_rejected(self, normalizer, value):
        result = normalizer.normalize(value)
        assert result.os_normalized is None
        assert result.method == 'rejected'

    @pytest.mark.parametrize("value", [
        "20.013",   # Rockwell firmware
        "24.012",   # Rockwell firmware
        "3.14",     # Bare numeric
    ])
    def test_bare_numeric_versions_rejected(self, normalizer, value):
        result = normalizer.normalize(value)
        assert result.os_normalized is None
        assert result.method == 'rejected'

    @pytest.mark.parametrize("value", [
        "microsoft",
        "cisco device",
        "hp laserjet printer",
        "hp ethernet multi-environment",
        "axis camera",
    ])
    def test_vendor_names_rejected(self, normalizer, value):
        result = normalizer.normalize(value)
        assert result.os_normalized is None
        assert result.method == 'rejected'

    def test_unknown_1_0_rejected(self, normalizer):
        result = normalizer.normalize("unknown 1.0")
        assert result.os_normalized is None
        assert result.method == 'rejected'

    def test_other_rejected(self, normalizer):
        result = normalizer.normalize("other")
        assert result.os_normalized is None
        assert result.method == 'rejected'

    def test_invalid_composite_windows_server_rejected(self, normalizer):
        result = normalizer.normalize("windows server 2022 2019")
        assert result.os_normalized is None
        assert result.method == 'rejected'


# =========================================================================
# Android dessert names → version
# =========================================================================

class TestAndroidDessertNames:
    """Android dessert code names should map to version numbers."""

    @pytest.mark.parametrize("dessert,expected_version", [
        ("pie", "9"),
        ("marshmallow", "6"),
        ("nougat", "7"),
        ("oreo", "8"),
        ("lollipop", "5"),
        ("kitkat", "4.4"),
    ])
    def test_dessert_maps_to_android_version(
        self, normalizer, dessert, expected_version
    ):
        result = normalizer.normalize(dessert)
        assert result.os_family == 'mobile_android'
        assert result.version == expected_version


# =========================================================================
# Android API levels → version
# =========================================================================

class TestAndroidAPILevels:
    """Android API levels > 20 should map to user-facing versions."""

    @pytest.mark.parametrize("api_input,expected_version", [
        ("android 33", "13"),
        ("android 34", "14"),
        ("android 30", "11"),
        ("android 28", "9"),
    ])
    def test_api_level_maps_to_version(
        self, normalizer, api_input, expected_version
    ):
        result = normalizer.normalize(api_input)
        assert result.os_family == 'mobile_android'
        assert result.version == expected_version

    def test_normal_android_version_unchanged(self, normalizer):
        """android 13 (actual version, not API level) should stay as-is."""
        result = normalizer.normalize("android 13")
        assert result.os_family == 'mobile_android'
        assert result.version == '13'


# =========================================================================
# Canonicalization (version fragmentation)
# =========================================================================

class TestOSNameCanonicalization:
    """OS name variants should canonicalize to a single form."""

    @pytest.mark.parametrize("raw,expected", [
        ("rokuos", "roku os"),
        ("windowsce", "windows ce"),
        ("chromeos", "chrome os"),
        ("fireos", "fire os"),
    ])
    def test_variant_canonicalized(self, normalizer, raw, expected):
        result = normalizer.normalize(raw)
        assert result.os_normalized == expected


# =========================================================================
# Normal values still work
# =========================================================================

class TestNormalValuesUnaffected:
    """Legitimate OS strings should still normalize correctly."""

    def test_windows_10(self, normalizer):
        result = normalizer.normalize("Windows 10")
        assert result.os_normalized is not None
        assert 'windows' in result.os_normalized.lower()

    def test_ubuntu(self, normalizer):
        result = normalizer.normalize("Ubuntu 22.04")
        assert result.os_family == 'linux_ubuntu'

    def test_centos_7(self, normalizer):
        result = normalizer.normalize("CentOS 7")
        assert result.os_family == 'linux_centos'

    def test_android_12(self, normalizer):
        """android 12 (version <=20) should not be treated as API level."""
        result = normalizer.normalize("android 12")
        assert result.os_family == 'mobile_android'
        assert result.version == '12'
