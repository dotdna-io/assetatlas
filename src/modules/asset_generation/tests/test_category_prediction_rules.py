# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for PatternBasedCategoryPredictor: vendor_category refinement, Lenovo machine-type codes."""

import pytest

from src.modules.asset_generation.domain.category_prediction_rules import PatternBasedCategoryPredictor


@pytest.fixture
def predictor():
    return PatternBasedCategoryPredictor()


class TestVendorCategoryRefinement:
    """When a prediction returns subcategory=unknown, vendor_category should refine it."""

    def test_windows10_with_vendor_category_laptop(self, predictor):
        """Windows 10 + vendor_category=laptop -> workstation/laptop."""
        result = predictor.predict(os='Windows 10', vendor_category='laptop')
        assert result is not None
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'laptop'
        assert 'vendor_category' in result['rule_type']

    def test_windows10_with_vendor_category_desktop(self, predictor):
        """Windows 10 + vendor_category=desktop -> workstation/desktop."""
        result = predictor.predict(os='Windows 10', vendor_category='desktop')
        assert result is not None
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'desktop'

    def test_refinement_only_when_category_matches(self, predictor):
        """vendor_category=server should NOT refine a workstation prediction."""
        result = predictor.predict(os='Windows 10', vendor_category='server')
        assert result is not None
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'unknown'

    def test_refinement_does_not_inflate_confidence(self, predictor):
        """Refined confidence should not exceed the vendor_category mapping confidence."""
        result = predictor.predict(os='Windows 10', vendor_category='laptop')
        assert result is not None
        assert result['confidence'] <= 1.0
        assert result['confidence'] == 0.70

    def test_no_refinement_when_subcategory_known(self, predictor):
        """Ubuntu is server category - vendor_category=desktop has category mismatch."""
        result = predictor.predict(os='Ubuntu 22.04 LTS', vendor_category='desktop')
        assert result is not None
        assert result['category'] == 'server'

    def test_vendor_category_hyper_v_guest(self, predictor):
        """hyper-v guest should map to server/virtual_server."""
        result = predictor.predict(vendor_category='hyper-v guest')
        assert result is not None
        assert result['category'] == 'server'
        assert result['subcategory'] == 'virtual_server'

    def test_vendor_category_nas(self, predictor):
        """nas should map to server/server."""
        result = predictor.predict(vendor_category='nas')
        assert result is not None
        assert result['category'] == 'server'
        assert result['subcategory'] == 'server'

    def test_vendor_category_ftp_server(self, predictor):
        """ftp server should map to server/server."""
        result = predictor.predict(vendor_category='ftp server')
        assert result is not None
        assert result['category'] == 'server'
        assert result['subcategory'] == 'server'

    def test_vendor_category_apple_mac(self, predictor):
        """apple mac should map to workstation/unknown (ambiguous)."""
        result = predictor.predict(vendor_category='apple mac')
        assert result is not None
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'unknown'


class TestGenericSubcategoryRefinement:
    """#282: vendor_category should refine generic subcategories, not just 'unknown'."""

    def test_server_server_refined_to_container_host(self, predictor):
        """Windows Server -> server/server + kubernetes node -> server/container_host."""
        result = predictor.predict(
            os='Windows Server 2019', vendor_category='kubernetes node'
        )
        assert result['category'] == 'server'
        assert result['subcategory'] == 'container_host'
        assert 'vendor_category' in result['rule_type']

    def test_server_server_refined_to_hypervisor(self, predictor):
        """Windows Server -> server/server + esxi server -> server/hypervisor."""
        result = predictor.predict(
            os='Windows Server 2022', vendor_category='esxi server'
        )
        assert result['category'] == 'server'
        assert result['subcategory'] == 'hypervisor'

    def test_server_server_refined_to_virtual_server(self, predictor):
        """Windows Server -> server/server + vmware guest -> server/virtual_server."""
        result = predictor.predict(
            os='Windows Server 2016', vendor_category='vmware guest'
        )
        assert result['category'] == 'server'
        assert result['subcategory'] == 'virtual_server'

    def test_specific_subcategory_not_refined(self, predictor):
        """A specific subcategory like 'laptop' should NOT be overwritten."""
        result = predictor.predict(
            os='Windows 10', manufacturer='Dell', model='Latitude 5520',
            vendor_category='desktop'
        )
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'laptop'

    def test_refinement_confidence_capped(self, predictor):
        """Refined confidence should be min(original, mapping)."""
        result = predictor.predict(
            os='Windows Server 2019', vendor_category='kubernetes node'
        )
        # kubernetes node mapping confidence is 0.65
        assert result['confidence'] <= 0.65


class TestLenovoMachineTypePatterns:
    """#280: Lenovo machine-type prefix codes should resolve laptop/desktop."""

    def test_lenovo_laptop_machinetype_20xx(self, predictor):
        """Lenovo + 20NQS4M800 (ThinkPad machine type) -> workstation/laptop."""
        result = predictor.predict(manufacturer='Lenovo', model='20NQS4M800')
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'laptop'
        assert result['confidence'] == 1.0

    def test_lenovo_laptop_machinetype_21xx(self, predictor):
        """Lenovo + 21K6S0AP00 (ThinkPad Gen 4+ machine type) -> workstation/laptop."""
        result = predictor.predict(manufacturer='Lenovo', model='21K6S0AP00')
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'laptop'

    def test_lenovo_desktop_machinetype_10xx(self, predictor):
        """Lenovo + 10MJS0AB00 (ThinkCentre machine type) -> workstation/desktop."""
        result = predictor.predict(manufacturer='Lenovo', model='10MJS0AB00')
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'desktop'

    def test_lenovo_desktop_machinetype_11xx(self, predictor):
        """Lenovo + 11EKSCR900 -> workstation/desktop."""
        result = predictor.predict(manufacturer='Lenovo', model='11EKSCR900')
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'desktop'

    def test_lenovo_desktop_machinetype_30xx(self, predictor):
        """Lenovo + 30D1S0AB00 (ThinkStation machine type) -> workstation/desktop."""
        result = predictor.predict(manufacturer='Lenovo', model='30D1S0AB00')
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'desktop'

    def test_lenovo_thinkpad_name_still_works(self, predictor):
        """Human-readable ThinkPad name should still match (regression check)."""
        result = predictor.predict(manufacturer='Lenovo', model='ThinkPad T14s Gen 2')
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'laptop'

    def test_lenovo_thinkcentre_name_still_works(self, predictor):
        """Human-readable ThinkCentre name should still match (regression check)."""
        result = predictor.predict(manufacturer='Lenovo', model='ThinkCentre M920q')
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'desktop'

    def test_lenovo_normalized_manufacturer(self, predictor):
        """Lenovo Global Technology Limited should normalize and still match."""
        result = predictor.predict(
            manufacturer='Lenovo Global Technology Limited', model='20NQS4M800'
        )
        assert result['category'] == 'workstation'
        assert result['subcategory'] == 'laptop'


class TestVendorCategoryMapExtensions:
    """Test newly added VENDOR_CATEGORY_MAP entries."""

    @pytest.mark.parametrize("vendor_cat,expected_cat,expected_subcat", [
        ('appliances', 'miscellaneous', 'unknown'),
        ('vcs', 'miscellaneous', 'video_conference'),
        ('iot gateways', 'iot', 'gateway'),
        ('lab equipment', 'ot', 'measuring_instrument'),
        ('sensors', 'iot', 'sensor'),
        ('storage server', 'server', 'storage'),
    ])
    def test_new_vendor_category_mapped(self, vendor_cat, expected_cat, expected_subcat):
        from src.modules.asset_generation.domain.category_prediction_rules import PatternBasedCategoryPredictor
        predictor = PatternBasedCategoryPredictor()
        result = predictor.predict(vendor_category=vendor_cat)
        assert result is not None, f"'{vendor_cat}' should not produce None"
        assert result['rule_type'] != 'fallback', f"'{vendor_cat}' should not be fallback"
        assert result['category'] == expected_cat
        assert result['subcategory'] == expected_subcat
