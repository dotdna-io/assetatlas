# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for hybrid_cpe_normalizer.py bug fixes (#275, #276, #277)."""

import pytest

from src.modules.asset_generation.domain.os_normalization.hybrid_cpe_normalizer import (
    HybridCPENormalizer,
)


@pytest.fixture
def normalizer():
    return HybridCPENormalizer()


# ── #275: Darwin kernel versions must not leak as macOS versions ──────────

class TestDarwinKernelVersions:
    """Darwin kernel numbers must map to macOS versions, not pass through."""

    @pytest.mark.parametrize("darwin_ver, expected_macos", [
        ("Darwin 20.6.0", "11"),
        ("Darwin 21.1.0", "12"),
        ("Darwin 22.4.0", "13"),
        ("Darwin 23.0.0", "14"),
        ("Darwin 24.3.0", "15"),
        ("Darwin 25.3.0", "16"),
    ])
    def test_known_darwin_maps_to_macos(self, normalizer, darwin_ver, expected_macos):
        result = normalizer.normalize(darwin_ver)
        assert result.os_normalized == f"mac os x {expected_macos}"

    @pytest.mark.parametrize("darwin_ver", [
        "Darwin 26.0.1",
        "Darwin 26.2",
        "Darwin 26.3",
        "Darwin 27.0.0",
    ])
    def test_unknown_darwin_does_not_leak_kernel_number(self, normalizer, darwin_ver):
        result = normalizer.normalize(darwin_ver)
        # Should NOT contain the raw kernel number as a macOS version
        assert "26" not in (result.os_normalized or "")
        assert "27" not in (result.os_normalized or "")


# ── #276: Android version fragmentation ──────────────────────────────────

class TestAndroidVersionNormalization:
    """Android trailing .0 should be stripped; implausible versions rejected."""

    @pytest.mark.parametrize("input_str, expected_version", [
        ("Android 12.0", "12"),
        ("Android 9.0", "9"),
        ("Android 10.0", "10"),
        ("Android 14.0", "14"),
    ])
    def test_trailing_zero_stripped(self, normalizer, input_str, expected_version):
        result = normalizer.normalize(input_str)
        assert result.os_normalized == f"android {expected_version}"

    @pytest.mark.parametrize("input_str, expected_version", [
        ("Android 12", "12"),
        ("Android 8.1", "8.1"),
        ("Android 4.4.2", "4.4.2"),
    ])
    def test_non_trailing_zero_preserved(self, normalizer, input_str, expected_version):
        result = normalizer.normalize(input_str)
        assert result.os_normalized == f"android {expected_version}"

    def test_implausible_version_rejected(self, normalizer):
        result = normalizer.normalize("Android 1.0")
        # Version should be stripped (android < 4 is implausible)
        assert result.version is None or result.os_normalized == "android"


# ── #277: Red Hat prefix on enterprise linux ─────────────────────────────

class TestRedHatDisplayName:
    """Enterprise Linux should display as 'red hat enterprise linux'."""

    @pytest.mark.parametrize("input_str, expected", [
        ("RHEL 9.7", "red hat enterprise linux 9.7"),
        ("RHEL 8.10", "red hat enterprise linux 8.10"),
        ("Red Hat Enterprise Linux 9.6", "red hat enterprise linux 9.6"),
        ("RHEL 7.9", "red hat enterprise linux 7.9"),
    ])
    def test_rhel_has_red_hat_prefix(self, normalizer, input_str, expected):
        result = normalizer.normalize(input_str)
        assert result.os_normalized == expected

    def test_rhel_without_version(self, normalizer):
        result = normalizer.normalize("RHEL")
        assert result.os_normalized.startswith("red hat enterprise linux")


# ── #681 A9a: Windows Server build-number normalization ──────────────────
#
# _get_windows_version_from_build had mis-ordered branches: a generic
# `build >= 10240 -> windows_10` branch sat above the more-specific Windows
# Server branches, making them dead code. Build-only Windows Server strings
# (no year in the text) therefore never produced a `windows_server_*` value,
# so the server-context caller fell back to its family default
# (windows_server_2019), and Server 2016/2022/2025 were mis-normalized.

class TestWindowsServerBuildMapping:
    """_get_windows_version_from_build maps 10.0.<build> to the right product.

    When the OS string says 'server', server builds must win over the generic
    desktop ranges, most-specific (highest build) first.
    """

    @pytest.mark.parametrize("os_string, expected", [
        # 'server' in text -> Windows Server, keyed by build number.
        # Pre-fix every one of these returned 'windows_10' (or 'windows_11'
        # for 26100): the generic build>=10240 branch swallowed them.
        ("microsoft windows server 2022 10.0.20348", "windows_server_2022"),
        ("windows server 10.0.26100", "windows_server_2025"),
        ("windows server 2019 10.0.17763", "windows_server_2019"),
        ("windows server 2016 10.0.14393", "windows_server_2016"),
    ])
    def test_server_build_maps_to_server_version(self, normalizer, os_string, expected):
        assert normalizer._get_windows_version_from_build(os_string) == expected

    @pytest.mark.parametrize("os_string, expected", [
        # No 'server' in text -> desktop path, unchanged by the fix.
        ("windows 10 10.0.19045", "windows_10"),
        ("windows 11 10.0.22631", "windows_11"),
    ])
    def test_desktop_build_unchanged(self, normalizer, os_string, expected):
        assert normalizer._get_windows_version_from_build(os_string) == expected

    def test_server_build_below_2016_falls_through_to_desktop(self, normalizer):
        # 10.0.* server builds older than 2016 (14393) have no Server product;
        # they fall through to the desktop mapping (windows_10), as documented.
        assert normalizer._get_windows_version_from_build(
            "windows server 10.0.10240"
        ) == "windows_10"

    def test_no_build_number_returns_none(self, normalizer):
        # No '10.0.NNNNN' build present -> method returns None (unchanged).
        assert normalizer._get_windows_version_from_build("windows 8.1") is None


class TestWindowsServerBuildEndToEnd:
    """Build-only Windows Server strings normalize to the right Server product.

    These carry no year in the text, so before the fix they fell back to the
    windows_server family default (windows_server_2019). Fixing the build
    mapping is what makes the server-context caller emit the right product
    downstream -- the output-changing improvement this fix delivers.
    """

    @pytest.mark.parametrize("os_string, expected_product", [
        ("windows server 10.0.20348", "windows_server_2022"),
        ("windows server 10.0.26100", "windows_server_2025"),
        ("windows server 10.0.14393", "windows_server_2016"),
        # 10.0.17763 already resolved to 2019 (the family default); locked here
        # to prove the fix does not regress the previously-correct case.
        ("windows server 10.0.17763", "windows_server_2019"),
    ])
    def test_build_only_server_string_resolves_product(
        self, normalizer, os_string, expected_product
    ):
        result = normalizer.normalize(os_string)
        assert result.product == expected_product, (
            f"Input {os_string!r}: expected product={expected_product!r}, "
            f"got product={result.product!r}, os_normalized={result.os_normalized!r}"
        )

    @pytest.mark.parametrize("os_string, expected_product", [
        ("windows 10 10.0.19045", "windows_10"),
        ("windows 11 10.0.22631", "windows_11"),
    ])
    def test_desktop_build_string_unchanged(
        self, normalizer, os_string, expected_product
    ):
        result = normalizer.normalize(os_string)
        assert result.product == expected_product


# ── #681 D2: network/embedded firmware must normalize, not pass through ──────
#
# HybridCPENormalizer let Citrix NetScaler and Cisco IOS train-format firmware
# fall through Tier-2 detection to the Tier-4 raw-passthrough, so the firmware
# string became the asset's os_normalized (no canonical OS family, skewed OS
# distribution). These map to a canonical family now.

class TestCitrixNetScalerNormalization:
    """Citrix NetScaler firmware strings get the citrix/netscaler family."""

    @pytest.mark.parametrize("os_string", [
        "netscaler",
        "NetScaler",
        "Citrix NetScaler",
        "citrix adc",
    ])
    def test_netscaler_detected_as_family(self, normalizer, os_string):
        result = normalizer.normalize(os_string)
        assert result.os_family == "network_citrix_netscaler"
        assert result.product == "netscaler"
        assert result.os_normalized == "netscaler"
        assert result.vendor == "citrix"
        assert result.tier == 2            # was tier 4 (raw passthrough) pre-fix

    def test_bare_netscaler_was_passthrough_now_recognized(self, normalizer):
        # Regression contract: pre-fix this returned tier-4 fallback with
        # os_family None; the family must now be populated so downstream
        # enrichment/selection treat it as a real network OS.
        result = normalizer.normalize("netscaler")
        assert result.os_family is not None
        assert result.method != "fallback"

    @pytest.mark.parametrize("os_string", [
        "netscaler 13.1(4)",
        "Citrix ADC 13.1(2)",
    ])
    def test_versioned_netscaler_not_hijacked_by_cisco_train(self, normalizer, os_string):
        # Regression guard: the Cisco IOS train-format pattern lives in
        # network_cisco_ios, which _detect_os_family checks BEFORE
        # network_citrix_netscaler. Unless that pattern is anchored to the start
        # of the string, a versioned NetScaler value ("...13.1(4)") is stolen by
        # Cisco IOS (wrong vendor/product/CPE). The family must stay NetScaler.
        result = normalizer.normalize(os_string)
        assert result.os_family == "network_citrix_netscaler"
        assert result.vendor == "citrix"

    @pytest.mark.parametrize("os_string, expected", [
        ("netscaler ns13.1", "netscaler 13.1"),
        ("NetScaler NS13.1", "netscaler 13.1"),
    ])
    def test_versioned_netscaler_keeps_version(self, normalizer, os_string, expected):
        # Pre-fix the generic extractor grabbed the trailing "1" -> "netscaler 1"
        # (a degradation). The NetScaler "NS<maj>.<min>" version must be preserved.
        result = normalizer.normalize(os_string)
        assert result.os_family == "network_citrix_netscaler"
        assert result.os_normalized == expected


class TestCiscoIOSTrainFormatNormalization:
    """Cisco IOS train-format firmware (e.g. 15.2(2)e7) maps to cisco ios <ver>."""

    @pytest.mark.parametrize("os_string, expected", [
        ("15.2(2)e7, release software (fc3)", "cisco ios 15.2"),
        ("12.2(44)se5", "cisco ios 12.2"),
        ("15.1(4)m", "cisco ios 15.1"),
    ])
    def test_train_format_maps_to_cisco_ios(self, normalizer, os_string, expected):
        result = normalizer.normalize(os_string)
        assert result.os_family == "network_cisco_ios"
        assert result.os_normalized == expected

    def test_existing_cisco_ios_string_unchanged(self, normalizer):
        # Regression guard: the literal "cisco ios" path is unaffected.
        result = normalizer.normalize("cisco ios")
        assert result.os_family == "network_cisco_ios"

    @pytest.mark.parametrize("os_string", [
        "Windows 11 Enterprise",   # must NOT be swept into cisco by the new pattern
        "Windows 10 10.0.19045",   # build numbers are not Cisco train format
        "Ubuntu 20.04",
        # Other vendors' firmware that ALSO uses an X.Y(Z) version token. Because
        # network_cisco_ios is iterated before these families, the train-format
        # pattern must be anchored to the START of the string or it steals them
        # from their own families (#681 D2 review regression guard).
        "pan-os 10.1(3)",
        "fortios 7.2(1)",
        "junos 20.4(3)",
        "nx-os 9.3(5)",
        "vmware esxi 7.0(3)",
    ])
    def test_non_cisco_strings_not_misclassified(self, normalizer, os_string):
        result = normalizer.normalize(os_string)
        assert result.os_family != "network_cisco_ios"
        assert result.os_family != "network_citrix_netscaler"
