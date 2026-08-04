# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for MAC address utilities."""

import pytest
from src.modules.asset_generation.domain.mac_utils import is_virtual_mac, filter_virtual_macs
from src.modules.asset_generation.domain.asset_validator import AssetValidator


class TestIsVirtualMac:
    """Tests for is_virtual_mac()."""

    @pytest.mark.parametrize("mac", [
        "02:50:41:00:00:01",  # 0x02 — locally administered
        "06:ab:cd:ef:01:23",  # 0x06
        "0a:00:27:00:00:00",  # 0x0a — VirtualBox
        "0e:ff:ff:00:00:01",  # 0x0e
        "12:34:56:78:9a:bc",  # 0x12
        "42:01:0a:80:00:02",  # 0x42 — GCP
        "fa:16:3e:ab:cd:ef",  # 0xfa — OpenStack
    ])
    def test_locally_administered_detected(self, mac):
        assert is_virtual_mac(mac) is True

    def test_all_zeros_detected(self):
        assert is_virtual_mac("00:00:00:00:00:00") is True

    def test_broadcast_detected(self):
        assert is_virtual_mac("ff:ff:ff:ff:ff:ff") is True

    @pytest.mark.parametrize("mac", [
        "00:0c:29:ab:cd:ef",  # VMware OUI
        "00:50:56:ab:cd:ef",  # VMware OUI
        "08:00:27:ab:cd:ef",  # VirtualBox OUI
        "00:15:5d:ab:cd:ef",  # Hyper-V OUI
        "52:54:00:ab:cd:ef",  # KVM OUI
        "00:16:3e:ab:cd:ef",  # Xen OUI
    ])
    def test_known_virtual_oui_detected(self, mac):
        assert is_virtual_mac(mac) is True

    @pytest.mark.parametrize("mac", [
        "a4:83:e7:01:02:03",  # Real OUI
        "20:41:53:59:4e:ff",  # Real OUI
        "3c:22:fb:00:11:22",  # Real OUI
    ])
    def test_real_oui_passes(self, mac):
        assert is_virtual_mac(mac) is False

    def test_none_returns_true(self):
        assert is_virtual_mac(None) is True

    def test_empty_string_returns_true(self):
        assert is_virtual_mac("") is True

    def test_whitespace_only_returns_true(self):
        assert is_virtual_mac("   ") is True

    def test_invalid_format_returns_true(self):
        assert is_virtual_mac("not-a-mac") is True

    def test_case_insensitive(self):
        assert is_virtual_mac("02:AB:CD:EF:01:23") is True
        assert is_virtual_mac("A4:83:E7:01:02:03") is False
        assert is_virtual_mac("00:0C:29:AB:CD:EF") is True

    def test_whitespace_trimmed(self):
        assert is_virtual_mac("  02:50:41:00:00:01  ") is True
        assert is_virtual_mac("  a4:83:e7:01:02:03  ") is False

    # #681 D3: shared appliance/bridge OUIs must be treated as virtual so they are
    # filtered out of the persisted mac[] array. They are globally-administered, so
    # they escape the locally-administered-bit heuristic and must be listed explicitly.
    def test_apple_bridge_mac_is_virtual(self):
        assert is_virtual_mac("ac:de:48:00:11:22") is True

    def test_fortinet_shared_mac_is_virtual(self):
        assert is_virtual_mac("00:09:0f:aa:00:01") is True

    def test_shared_appliance_macs_map_to_expected_vendor(self):
        # Pin the vendor label so a wrong/renamed prefix entry would be caught.
        assert AssetValidator.is_virtual_mac("ac:de:48:00:11:22") == (True, "Apple")
        assert AssetValidator.is_virtual_mac("00:09:0f:aa:00:01") == (True, "Fortinet")


class TestFilterVirtualMacs:
    """Tests for filter_virtual_macs()."""

    def test_filters_virtual_keeps_real(self):
        macs = [
            "02:50:41:00:00:01",  # virtual (locally administered)
            "a4:83:e7:01:02:03",  # real
            "00:00:00:00:00:00",  # all zeros
            "00:0c:29:ab:cd:ef",  # virtual (VMware OUI)
            "ff:ff:ff:ff:ff:ff",  # broadcast
        ]
        result = filter_virtual_macs(macs)
        assert result == ["a4:83:e7:01:02:03"]

    def test_none_returns_empty(self):
        assert filter_virtual_macs(None) == []

    def test_empty_list_returns_empty(self):
        assert filter_virtual_macs([]) == []

    def test_all_virtual_returns_empty(self):
        macs = ["02:50:41:00:00:01", "ff:ff:ff:ff:ff:ff"]
        assert filter_virtual_macs(macs) == []

    def test_all_real_returns_all(self):
        macs = ["a4:83:e7:01:02:03", "3c:22:fb:00:11:22"]
        result = filter_virtual_macs(macs)
        assert result == macs

    def test_filter_drops_shared_appliance_macs(self):
        # #681 D3: Apple-bridge + Fortinet shared OUIs are dropped, real NIC kept.
        macs = ["ac:de:48:00:11:22", "00:09:0f:aa:00:01", "a4:83:e7:01:02:03"]
        assert filter_virtual_macs(macs) == ["a4:83:e7:01:02:03"]
