# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for virtual MAC address detection and exclusion from persistent mappings.

This test suite verifies that:
1. Virtual MAC addresses are correctly identified by OUI prefix
2. Virtual MACs are excluded from persistent mappings
3. Physical MACs are retained in persistent mappings
4. The exclusion works correctly in AssetIdentifier and MappingRepository
"""

import pytest
from unittest.mock import Mock
from uuid import UUID

from src.modules.asset_generation.domain.asset_validator import AssetValidator
from src.modules.asset_generation.domain.asset_identifier import AssetIdentifier
from src.modules.asset_generation.repositories.mapping_repository import MappingRepository
from src.database.postgres_adapter import PostgreSQLAdapter


class TestVirtualMACDetection:
    """Test virtual MAC detection in AssetValidator."""

    def test_vmware_mac_detection(self):
        """Test detection of VMware MAC addresses."""
        # VMware OUI prefixes
        assert AssetValidator.is_virtual_mac("00:50:56:12:34:56") == (True, "VMware")
        assert AssetValidator.is_virtual_mac("00:0c:29:ab:cd:ef") == (True, "VMware")
        assert AssetValidator.is_virtual_mac("00:05:69:11:22:33") == (True, "VMware")
        assert AssetValidator.is_virtual_mac("00:1c:14:aa:bb:cc") == (True, "VMware")

    def test_virtualbox_mac_detection(self):
        """Test detection of VirtualBox MAC addresses."""
        assert AssetValidator.is_virtual_mac("08:00:27:12:34:56") == (True, "VirtualBox")

    def test_hyperv_mac_detection(self):
        """Test detection of Hyper-V MAC addresses."""
        assert AssetValidator.is_virtual_mac("00:15:5d:12:34:56") == (True, "Hyper-V")
        assert AssetValidator.is_virtual_mac("00:03:ff:ab:cd:ef") == (True, "Hyper-V")

    def test_kvm_qemu_mac_detection(self):
        """Test detection of KVM/QEMU MAC addresses."""
        assert AssetValidator.is_virtual_mac("52:54:00:12:34:56") == (True, "KVM/QEMU")
        assert AssetValidator.is_virtual_mac("52:54:01:ab:cd:ef") == (True, "KVM/QEMU")

    def test_proxmox_guest_mac_detection(self):
        """Test detection of Proxmox guest (LXC/VM) virtual NIC MAC addresses.

        Proxmox auto-assigns guest vNIC MACs from its own registered OUI
        BC:24:11 ('Proxmox Server Solutions GmbH'). These belong to guest
        vNICs (veth for LXC, tap for VMs) on a vmbr bridge, NOT host hardware.
        The first octet 0xBC has the locally-administered bit clear, so the LAA
        heuristic misses them and they must be listed explicitly — otherwise a
        Proxmox host over-merges with every guest that shares the bridge (see
        hypervisor-01).
        """
        # Real guest MACs observed on hypervisor-01's LXC containers
        assert AssetValidator.is_virtual_mac("bc:24:11:b4:18:aa") == (True, "Proxmox")
        assert AssetValidator.is_virtual_mac("bc:24:11:49:84:32") == (True, "Proxmox")

    def test_xen_mac_detection(self):
        """Test detection of Xen MAC addresses."""
        assert AssetValidator.is_virtual_mac("00:16:3e:12:34:56") == (True, "Xen")

    def test_docker_mac_detection(self):
        """Test detection of Docker MAC addresses."""
        assert AssetValidator.is_virtual_mac("02:42:12:34:56:78") == (True, "Docker")

    def test_parallels_mac_detection(self):
        """Test detection of Parallels MAC addresses."""
        assert AssetValidator.is_virtual_mac("00:1c:42:12:34:56") == (True, "Parallels")

    def test_aws_mac_detection(self):
        """Test detection of AWS MAC addresses."""
        assert AssetValidator.is_virtual_mac("02:00:5e:12:34:56") == (True, "AWS")
        assert AssetValidator.is_virtual_mac("02:50:00:ab:cd:ef") == (True, "AWS")
        assert AssetValidator.is_virtual_mac("02:ae:5f:11:22:33") == (True, "AWS")

    def test_gcp_mac_detection(self):
        """Test detection of Google Cloud MAC addresses."""
        assert AssetValidator.is_virtual_mac("42:01:12:34:56:78") == (True, "Google Cloud")

    def test_locally_administered_mac_detection(self):
        """Test detection of locally administered MACs (bit 1 set in first octet)."""
        # Locally administered MACs have the 2nd bit set in the first octet
        # Example: 0x02, 0x06, 0x0a, 0x0e, etc.
        assert AssetValidator.is_virtual_mac("02:00:00:12:34:56")[0] == True  # Generic locally administered
        assert AssetValidator.is_virtual_mac("06:00:00:ab:cd:ef")[0] == True
        assert AssetValidator.is_virtual_mac("0a:00:00:11:22:33")[0] == True

    def test_physical_mac_detection(self):
        """Test that physical MAC addresses are correctly identified."""
        # Well-known physical vendor MACs
        assert AssetValidator.is_virtual_mac("00:11:22:33:44:55") == (False, "Physical")  # Ciena
        assert AssetValidator.is_virtual_mac("00:1a:a0:12:34:56") == (False, "Physical")  # Dell
        assert AssetValidator.is_virtual_mac("00:1b:21:ab:cd:ef") == (False, "Physical")  # Intel
        assert AssetValidator.is_virtual_mac("00:1c:c0:11:22:33") == (False, "Physical")  # Cisco
        assert AssetValidator.is_virtual_mac("00:50:c2:aa:bb:cc") == (False, "Physical")  # IEEE Registration
        assert AssetValidator.is_virtual_mac("d4:be:d9:12:34:56") == (False, "Physical")  # Dell
        assert AssetValidator.is_virtual_mac("a4:5e:60:ab:cd:ef") == (False, "Physical")  # Apple

    def test_mac_format_normalization(self):
        """Test that different MAC formats are handled correctly."""
        # Colon format
        assert AssetValidator.is_virtual_mac("00:50:56:12:34:56") == (True, "VMware")
        # Dash format
        assert AssetValidator.is_virtual_mac("00-50-56-12-34-56") == (True, "VMware")
        # Uppercase
        assert AssetValidator.is_virtual_mac("00:50:56:AB:CD:EF") == (True, "VMware")
        # Mixed case
        assert AssetValidator.is_virtual_mac("00:50:56:aB:cD:eF") == (True, "VMware")

    def test_invalid_mac_handling(self):
        """Test handling of invalid MAC addresses."""
        assert AssetValidator.is_virtual_mac("") == (False, "Physical")
        assert AssetValidator.is_virtual_mac(None) == (False, "Physical")
        assert AssetValidator.is_virtual_mac("invalid") == (False, "Physical")
        assert AssetValidator.is_virtual_mac("00:50:56") == (False, "Physical")  # Partial MAC, not a valid 6-octet address


class TestVirtualMACExclusionInMappings:
    """Test that virtual MACs are excluded from persistent mappings."""

    def test_asset_identifier_excludes_virtual_macs(self):
        """Test that AssetIdentifier.get_all_mappings() excludes virtual MACs."""
        identifier = AssetIdentifier()

        # Create assets with physical and virtual MACs
        components_physical = ["server01", "00:11:22:33:44:55"]  # Physical MAC
        components_vmware = ["vm01", "00:50:56:12:34:56"]  # VMware MAC
        components_docker = ["container01", "02:42:12:34:56:78"]  # Docker MAC

        identifier.group_components(components_physical)
        identifier.group_components(components_vmware)
        identifier.group_components(components_docker)

        # Get all mappings
        mappings = identifier.get_all_mappings()

        # Physical MAC should be in mappings
        assert "00:11:22:33:44:55" in mappings
        # Virtual MACs should NOT be in mappings
        assert "00:50:56:12:34:56" not in mappings
        assert "02:42:12:34:56:78" not in mappings
        # Hostnames should still be in mappings
        assert "server01" in mappings
        assert "vm01" in mappings
        assert "container01" in mappings

    def test_mapping_repository_excludes_virtual_macs(self):
        """Test that MappingRepository.save_mappings_bulk() excludes virtual MACs."""
        # Mock database adapter
        mock_db = Mock(spec=PostgreSQLAdapter)
        mock_db.bulk_set_asset_id_mappings = Mock(return_value=0)

        repo = MappingRepository(mock_db)

        # Create mappings with physical and virtual MACs
        mappings = {
            "server01": UUID("12345678-1234-5678-1234-567812345678"),
            "00:11:22:33:44:55": UUID("12345678-1234-5678-1234-567812345678"),  # Physical
            "00:50:56:12:34:56": UUID("abcdef12-3456-7890-abcd-ef1234567890"),  # VMware
            "08:00:27:ab:cd:ef": UUID("fedcba98-7654-3210-fedc-ba9876543210"),  # VirtualBox
            "02:42:12:34:56:78": UUID("11111111-2222-3333-4444-555555555555"),  # Docker
        }

        # Save mappings
        count = repo.save_mappings_bulk(mappings, use_uuid=False)

        # Should have saved only hostname and physical MAC
        assert mock_db.bulk_set_asset_id_mappings.called
        saved_mappings = mock_db.bulk_set_asset_id_mappings.call_args[0][0]
        saved_components = [comp for comp, _ in saved_mappings]

        assert "server01" in saved_components
        assert "00:11:22:33:44:55" in saved_components
        assert "00:50:56:12:34:56" not in saved_components
        assert "08:00:27:ab:cd:ef" not in saved_components
        assert "02:42:12:34:56:78" not in saved_components

    def test_mixed_physical_virtual_macs(self):
        """Test filtering when asset has both physical and virtual MACs."""
        identifier = AssetIdentifier()

        # Create asset with both physical and virtual MACs
        components = [
            "server01",
            "00:11:22:33:44:55",  # Physical
            "00:50:56:12:34:56",  # Virtual (VMware)
            "192.168.1.100"       # IP (also excluded)
        ]

        asset_id = identifier.group_components(components)
        mappings = identifier.get_all_mappings()

        # Only physical MAC and hostname should be in mappings
        assert "server01" in mappings
        assert "00:11:22:33:44:55" in mappings
        # Virtual MAC and IP should be excluded
        assert "00:50:56:12:34:56" not in mappings
        assert "192.168.1.100" not in mappings

    def test_all_components_virtual_or_ip(self):
        """Test when all components are virtual MACs or IPs (should still have mappings internally)."""
        identifier = AssetIdentifier()

        # Create asset with only virtual MACs and IPs
        components = [
            "00:50:56:12:34:56",  # Virtual (VMware)
            "192.168.1.100"       # IP
        ]

        asset_id = identifier.group_components(components)

        # Internal UnionFind should still have these components
        assert identifier.get_asset_id_for_component("00:50:56:12:34:56") == asset_id
        assert identifier.get_asset_id_for_component("192.168.1.100") == asset_id

        # But get_all_mappings() should exclude them
        mappings = identifier.get_all_mappings()
        assert "00:50:56:12:34:56" not in mappings
        assert "192.168.1.100" not in mappings

    def test_virtual_mac_exclusion_logging(self, caplog):
        """Test that virtual MAC exclusion is logged correctly."""
        import logging
        caplog.set_level(logging.INFO)

        identifier = AssetIdentifier()

        # Create assets with virtual MACs
        identifier.group_components(["vm01", "00:50:56:12:34:56"])  # VMware
        identifier.group_components(["vm02", "08:00:27:ab:cd:ef"])  # VirtualBox

        # Get mappings (triggers filtering)
        mappings = identifier.get_all_mappings()

        # Check that logging occurred
        assert "virtual MAC" in caplog.text.lower() or "filtered" in caplog.text.lower()


class TestVirtualMACRegressionTests:
    """Regression tests for specific scenarios."""

    def test_case_insensitive_virtual_mac_detection(self):
        """Test that virtual MAC detection is case-insensitive."""
        # Uppercase
        assert AssetValidator.is_virtual_mac("00:50:56:AB:CD:EF") == (True, "VMware")
        # Lowercase
        assert AssetValidator.is_virtual_mac("00:50:56:ab:cd:ef") == (True, "VMware")
        # Mixed
        assert AssetValidator.is_virtual_mac("00:50:56:Ab:Cd:Ef") == (True, "VMware")

    def test_virtual_mac_with_dash_format(self):
        """Test virtual MAC detection with dash format."""
        assert AssetValidator.is_virtual_mac("00-50-56-12-34-56") == (True, "VMware")
        assert AssetValidator.is_virtual_mac("08-00-27-ab-cd-ef") == (True, "VirtualBox")

    def test_normalized_virtual_mac_in_mappings(self):
        """Test that normalized virtual MACs are still excluded."""
        identifier = AssetIdentifier()

        # Create asset with uppercase virtual MAC
        components = ["vm01", "00:50:56:AB:CD:EF"]
        identifier.group_components(components)

        mappings = identifier.get_all_mappings()

        # Virtual MAC should be excluded regardless of case
        assert "00:50:56:ab:cd:ef" not in mappings
        assert "00:50:56:AB:CD:EF" not in mappings

    def test_multiple_virtual_vendors_single_asset(self):
        """Test asset with MACs from multiple virtual vendors."""
        identifier = AssetIdentifier()

        components = [
            "multi-nic-vm",
            "00:50:56:12:34:56",  # VMware
            "08:00:27:ab:cd:ef",  # VirtualBox
            "52:54:00:11:22:33",  # KVM
        ]

        identifier.group_components(components)
        mappings = identifier.get_all_mappings()

        # All virtual MACs should be excluded
        assert "00:50:56:12:34:56" not in mappings
        assert "08:00:27:ab:cd:ef" not in mappings
        assert "52:54:00:11:22:33" not in mappings
        # Hostname should be retained
        assert "multi-nic-vm" in mappings

    def test_proxmox_guest_mac_not_persisted_as_bridge(self):
        """A Proxmox guest vNIC MAC must not be persisted as a mapping.

        This locks the persistence side of the fix: the guest's BC:24:11 vNIC
        MAC is reported by both the guest and host-side sources, so if it were
        persisted as a component it would bridge the Proxmox host to every guest
        on the next run via load_existing_mappings(). Excluding it from
        get_all_mappings() keeps that cross-run bridge out of the mappings table
        (the within-run strip happens separately in _classify_row). See
        hypervisor-01, which over-merged the host with its lxc-* containers.
        """
        identifier = AssetIdentifier()
        # Host (physical NIC) and a guest (Proxmox vNIC) as separate groups
        identifier.group_components(["hypervisor-01", "58:47:ca:7c:6a:22"])
        identifier.group_components(["lxc-n8n", "bc:24:11:b4:18:aa"])

        mappings = identifier.get_all_mappings()

        # Hostnames and the physical host NIC persist as identity
        assert "hypervisor-01" in mappings
        assert "lxc-n8n" in mappings
        assert "58:47:ca:7c:6a:22" in mappings
        # The Proxmox guest vNIC MAC must NOT persist (it would bridge host<->guest)
        assert "bc:24:11:b4:18:aa" not in mappings


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
