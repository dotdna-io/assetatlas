# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for OS normalization fixes (QA-4b, QA-4c, QA-4d)."""

import pytest

from src.modules.asset_generation.domain.os_normalization.hybrid_cpe_normalizer import HybridCPENormalizer


@pytest.fixture
def normalizer():
    return HybridCPENormalizer()


class TestEnterpriseLinuxPattern:
    """QA-4b: 'enterprise linux' without 'red hat' prefix should match RHEL."""

    def test_enterprise_linux_9(self, normalizer):
        """'enterprise linux 9.7' should normalize to RHEL."""
        result = normalizer.normalize('enterprise linux 9.7')
        assert result.os_family == 'linux_rhel'

    def test_enterprise_linux_8(self, normalizer):
        """'enterprise linux 8.10' should normalize to RHEL."""
        result = normalizer.normalize('enterprise linux 8.10')
        assert result.os_family == 'linux_rhel'

    def test_red_hat_enterprise_linux_still_works(self, normalizer):
        """Existing 'Red Hat Enterprise Linux' should still work."""
        result = normalizer.normalize('Red Hat Enterprise Linux 9.2')
        assert result.os_family == 'linux_rhel'


class TestDarwinVersionMapping:
    """QA-4c: Darwin kernel versions should map to correct macOS versions."""

    def test_darwin_20_is_big_sur(self, normalizer):
        """Darwin 20.x -> macOS 11 (Big Sur)."""
        result = normalizer.normalize('Darwin 20.6.0')
        assert result.os_family == 'macos'
        assert result.version == '11' or result.version.startswith('11')

    def test_darwin_21_is_monterey(self, normalizer):
        """Darwin 21.x -> macOS 12 (Monterey)."""
        result = normalizer.normalize('Darwin 21.6.0')
        assert result.os_family == 'macos'
        assert result.version == '12' or result.version.startswith('12')

    def test_darwin_22_is_ventura(self, normalizer):
        """Darwin 22.x -> macOS 13 (Ventura)."""
        result = normalizer.normalize('Darwin 22.4.0')
        assert result.os_family == 'macos'
        assert result.version == '13' or result.version.startswith('13')

    def test_darwin_23_is_sonoma(self, normalizer):
        """Darwin 23.x -> macOS 14 (Sonoma)."""
        result = normalizer.normalize('Darwin 23.1.0')
        assert result.os_family == 'macos'
        assert result.version == '14' or result.version.startswith('14')

    def test_darwin_24_is_sequoia(self, normalizer):
        """Darwin 24.x -> macOS 15 (Sequoia)."""
        result = normalizer.normalize('Darwin 24.0.0')
        assert result.os_family == 'macos'
        assert result.version == '15' or result.version.startswith('15')


class TestAmazonLinuxTruncation:
    """QA-4d: Amazon Linux build dates should truncate to YYYY.N."""

    def test_amazon_linux_2023_build_date_truncated(self, normalizer):
        """'Amazon Linux 2023.1.20230119' -> version '2023.1'."""
        result = normalizer.normalize('Amazon Linux 2023.1.20230119')
        assert result.os_family == 'linux_amazon'
        assert result.version == '2023.1'

    def test_amazon_linux_2023_short_version_unchanged(self, normalizer):
        """'Amazon Linux 2023.1' -> version '2023.1' (already truncated)."""
        result = normalizer.normalize('Amazon Linux 2023.1')
        assert result.os_family == 'linux_amazon'
        assert result.version == '2023.1'

    def test_amazon_linux_2_build_date_truncated(self, normalizer):
        """'Amazon Linux 2.0.20230126' -> version '2.0'."""
        result = normalizer.normalize('Amazon Linux 2.0.20230126')
        assert result.os_family == 'linux_amazon'
        assert result.version == '2.0'

    def test_amazon_linux_2_simple(self, normalizer):
        """'Amazon Linux 2' -> version '2'."""
        result = normalizer.normalize('Amazon Linux 2')
        assert result.os_family == 'linux_amazon'
        assert result.version == '2'
