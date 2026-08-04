# SPDX-License-Identifier: AGPL-3.0-or-later
"""#288: Hostname-pattern heuristics for enterprise naming conventions.

Validates new Priority 9 hostname patterns against production-observed
enterprise naming conventions. All new patterns use low confidence
(0.50-0.55) as last-resort fallback.
"""
import pytest
import pandas as pd

from src.modules.asset_generation.domain.category_prediction_rules import PatternBasedCategoryPredictor
from src.modules.asset_generation.domain.category_prediction import CategoryPrediction


@pytest.fixture
def predictor():
    return PatternBasedCategoryPredictor()


class TestEmbeddedDeviceTypePatterns:
    """Embedded NB/DT codes in continuous hostnames (e.g., CDELNNNB0283, CUKDT5545)."""

    @pytest.mark.parametrize("hostname, expected_subcat", [
        ('CDELNNNB0283', 'laptop'),     # Production: Entra hostname with embedded NB
        ('CESNB1015', 'laptop'),        # Production: short site code + NB
        ('CGBDXYNB2244', 'laptop'),     # Production: longer site code + NB
        ('AUNB8192', 'laptop'),         # Production: AU site + NB
        ('CUKDT5545', 'desktop'),       # Production: UK site + DT
        ('CDEHAMDT0028', 'desktop'),    # Production: DE-HAM site + DT
        ('CNLDT0428', 'desktop'),       # Production: NL site + DT
        ('CDELNNDT0116', 'desktop'),    # Production: DE-LNN site + DT
    ])
    def test_embedded_codes_match(self, predictor, hostname, expected_subcat):
        result = predictor.predict(hostname=hostname)
        assert result is not None
        assert result['subcategory'] == expected_subcat
        assert result['rule_type'] == 'hostname_pattern'

    @pytest.mark.parametrize("hostname", [
        'NEWBRIDGE',       # NB appears but not in [a-z]NB[0-9] position
        'SUNBURY-PC',      # NB appears but followed by letter
        'ADMINTOOL',       # DT appears but not in [a-z]DT[0-9] position
        'EDITBOX',         # DT appears but followed by letter
    ])
    def test_false_positives_rejected(self, predictor, hostname):
        """These hostnames contain NB/DT substrings but should NOT match the patterns."""
        result = predictor.predict(hostname=hostname)
        # Should either be None or not match as hostname_pattern
        assert result is None or result['rule_type'] != 'hostname_pattern'


class TestSiteCodePatterns:
    """Site-code naming: {site}-{type}-{number} (e.g., SIN-N-212696, CAL-D-19053)."""

    @pytest.mark.parametrize("hostname, expected_subcat", [
        ('SIN-N-212696', 'laptop'),
        ('FIN-N-211588', 'laptop'),
        ('MKT-N-19788', 'laptop'),
        ('CAL-N-18775', 'laptop'),
        ('ADM-N-19324', 'laptop'),
        ('CAL-D-19053', 'desktop'),
        ('FAB-D-19853', 'desktop'),
        ('OPL-D-18785', 'desktop'),
    ])
    def test_site_code_patterns_match(self, predictor, hostname, expected_subcat):
        result = predictor.predict(hostname=hostname)
        assert result is not None
        assert result['subcategory'] == expected_subcat
        assert result['rule_type'] == 'hostname_pattern'


class TestDelimitedSegmentPatterns:
    """Delimited device-type segments (e.g., ACE-LT-0023, LAP055, AUWS8830)."""

    @pytest.mark.parametrize("hostname, expected_subcat", [
        ('ACE-LT-0023', 'laptop'),
        ('LT-CE1', 'laptop'),
        ('LT-SALESREP-23', 'laptop'),
        ('LAP055', 'laptop'),
        ('SC-20-LAP-008', 'laptop'),
        ('AUWS8830', 'desktop'),
        ('HSJWS02', 'desktop'),
    ])
    def test_delimited_segments_match(self, predictor, hostname, expected_subcat):
        result = predictor.predict(hostname=hostname)
        assert result is not None
        assert result['subcategory'] == expected_subcat
        assert result['rule_type'] == 'hostname_pattern'

    @pytest.mark.parametrize("hostname", [
        'WESTMINSTER',    # WS appears but not delimited
        'NEWSLETTER',     # LT appears but not delimited
    ])
    def test_delimited_false_positives_rejected(self, predictor, hostname):
        result = predictor.predict(hostname=hostname)
        assert result is None or result['rule_type'] != 'hostname_pattern'


class TestVirtualMachinePatterns:
    """VM and VDI hostname patterns."""

    @pytest.mark.parametrize("hostname, expected_cat, expected_subcat", [
        ('CCV2-VM-032', 'server', 'virtual_server'),
        ('Mule-VM-020', 'server', 'virtual_server'),
        ('azdevinvm01', 'server', 'virtual_server'),
        ('usnymelvm36', 'server', 'virtual_server'),
        ('NAR-RCARRUTH-VM', 'server', 'virtual_server'),
    ])
    def test_vm_patterns_match(self, predictor, hostname, expected_cat, expected_subcat):
        result = predictor.predict(hostname=hostname)
        assert result is not None
        assert result['category'] == expected_cat
        assert result['subcategory'] == expected_subcat

    @pytest.mark.parametrize("hostname, expected_subcat", [
        ('SALES-VDI-001', 'virtual_desktop'),
    ])
    def test_vdi_patterns_match(self, predictor, hostname, expected_subcat):
        result = predictor.predict(hostname=hostname)
        assert result is not None
        assert result['subcategory'] == expected_subcat


class TestConfidenceValues:
    """All new patterns return 0.50-0.55 confidence."""

    @pytest.mark.parametrize("hostname", [
        'CDELNNNB0283', 'CUKDT5545', 'SIN-N-212696', 'CAL-D-19053',
        'LAP055', 'LT-CE1', 'AUWS8830', 'CCV2-VM-032', 'SALES-VDI-001',
    ])
    def test_new_patterns_use_low_confidence(self, predictor, hostname):
        result = predictor.predict(hostname=hostname)
        assert result is not None
        assert 0.50 <= result['confidence'] <= 0.55


class TestPriority9Behavior:
    """Hostname patterns only fire when OS/manufacturer/model/mac_vendor yield no match."""

    def test_hostname_ignored_when_os_matches(self, predictor):
        """OS pattern (Priority 2) takes precedence over hostname (Priority 9)."""
        result = predictor.predict(
            os='Windows Server 2022',
            hostname='CDELNNNB0283',  # Would match as laptop
        )
        assert result is not None
        assert result['category'] == 'server'
        assert result['rule_type'] != 'hostname_pattern'

    def test_hostname_used_when_no_other_signal(self, predictor):
        """With no OS/manufacturer/model, hostname pattern fires."""
        result = predictor.predict(hostname='CDELNNNB0283')
        assert result is not None
        assert result['subcategory'] == 'laptop'
        assert result['rule_type'] == 'hostname_pattern'


class TestPatternOrdering:
    """New patterns are appended after existing ones — first-match-wins."""

    def test_existing_srv_beats_new_vm(self, predictor):
        """SRV-VM01: existing 'srv' pattern (0.7) matches before new VM pattern."""
        result = predictor.predict(hostname='SRV-VM01')
        assert result is not None
        assert result['category'] == 'server'
        assert result['subcategory'] == 'server'  # 'srv' match, not 'virtual_server'
        assert result['confidence'] == 0.7

    def test_existing_laptop_prefix_beats_new_nb(self, predictor):
        """LAPTOP-NB001: existing '^laptop-' (0.85) matches before new NB pattern."""
        result = predictor.predict(hostname='LAPTOP-NB001')
        assert result is not None
        assert result['subcategory'] == 'laptop'
        assert result['confidence'] == 0.85  # Existing pattern confidence


class TestCategoryPredictionWiring:
    """Hostname column is wired through CategoryPrediction.predict_categories()."""

    def test_hostname_resolves_fallback_rows(self):
        """Rows that would fall back to miscellaneous/unknown get resolved via hostname."""
        cp = CategoryPrediction()
        df = pd.DataFrame([
            {'os_normalized': None, 'manufacturer': None, 'model': None,
             'mac_vendor_enriched': None, 'hostname': 'CDELNNNB0283'},
            {'os_normalized': None, 'manufacturer': None, 'model': None,
             'mac_vendor_enriched': None, 'hostname': 'CUKDT5545'},
            {'os_normalized': 'Windows Server 2022', 'manufacturer': None,
             'model': None, 'mac_vendor_enriched': None, 'hostname': 'SRV-NB001'},
        ])
        result = cp.predict_categories(df, hostname_column='hostname')
        # Row 0: hostname resolves to laptop
        assert result.iloc[0]['predicted_subcategory'] == 'laptop'
        assert result.iloc[0]['category_rule_type'] == 'hostname_pattern'
        # Row 1: hostname resolves to desktop
        assert result.iloc[1]['predicted_subcategory'] == 'desktop'
        assert result.iloc[1]['category_rule_type'] == 'hostname_pattern'
        # Row 2: OS pattern wins (Priority 2 beats Priority 9)
        assert result.iloc[2]['predicted_category'] == 'server'
        assert result.iloc[2]['category_rule_type'] != 'hostname_pattern'

    def test_dedup_optimization_preserved(self):
        """Adding hostname does not break the dedup key optimization."""
        cp = CategoryPrediction()
        # 100 rows with same OS/mfr/model/mac but different hostnames
        rows = []
        for i in range(100):
            rows.append({
                'os_normalized': 'Windows 10',
                'manufacturer': 'Dell',
                'model': 'Latitude 5530',
                'mac_vendor_enriched': 'Dell',
                'hostname': f'LAPTOP-{i:04d}',
            })
        df = pd.DataFrame(rows)
        result = cp.predict_categories(df, hostname_column='hostname')
        # All should resolve via OS pattern (not hostname) — dedup collapses to 1 combo
        stats = cp.get_stats()
        assert stats['by_rule_type'].get('hostname_pattern', 0) == 0

    def test_vendor_category_part_of_dedup_key(self):
        """Rows sharing os/mfr/model/mac but differing in vendor_category must each be
        predicted from their OWN vendor_category. Regression: assets with no other
        signal (e.g. firewalls with vendor_category=firewall) were conflated into one
        dedup bucket and took the first row's vendor_category, landing in miscellaneous."""
        cp = CategoryPrediction()
        df = pd.DataFrame([
            {'os_normalized': None, 'manufacturer': None, 'model': None,
             'mac_vendor_enriched': None, 'vendor_category': 'firewall'},
            {'os_normalized': None, 'manufacturer': None, 'model': None,
             'mac_vendor_enriched': None, 'vendor_category': 'server'},
        ])
        result = cp.predict_categories(df)
        # firewall -> (network, firewall); server -> (server, server). Each row uses
        # its OWN vendor_category, not the bucket's first occurrence.
        assert result.iloc[0]['predicted_category'] == 'network'
        assert result.iloc[0]['predicted_subcategory'] == 'firewall'
        assert result.iloc[1]['predicted_category'] == 'server'
        assert result.iloc[1]['predicted_subcategory'] == 'server'

    def test_vendor_category_keyed_even_with_shared_non_null_base_field(self):
        """The fix must generalise beyond the all-null bucket: rows sharing a non-null
        base field (mac_vendor) but differing in vendor_category must still each predict
        from their own value (vendor_category is Priority 6, above mac_vendor Priority 8)."""
        cp = CategoryPrediction()
        df = pd.DataFrame([
            {'os_normalized': None, 'manufacturer': None, 'model': None,
             'mac_vendor_enriched': 'Cisco', 'vendor_category': 'firewall'},
            {'os_normalized': None, 'manufacturer': None, 'model': None,
             'mac_vendor_enriched': 'Cisco', 'vendor_category': 'switch'},
        ])
        result = cp.predict_categories(df)
        assert result.iloc[0]['predicted_subcategory'] == 'firewall'
        assert result.iloc[1]['predicted_subcategory'] == 'switch'

    def test_missing_hostname_column_graceful(self):
        """If hostname column is missing from DataFrame, prediction still works."""
        cp = CategoryPrediction()
        df = pd.DataFrame([
            {'os_normalized': 'Windows 10', 'manufacturer': None,
             'model': None, 'mac_vendor_enriched': None},
        ])
        result = cp.predict_categories(df, hostname_column='hostname')
        assert result.iloc[0]['predicted_category'] == 'workstation'

    def test_stats_reflect_hostname_overrides(self):
        """Stats should count hostname-resolved rows correctly, not as fallback."""
        cp = CategoryPrediction()
        df = pd.DataFrame([
            {'os_normalized': None, 'manufacturer': None, 'model': None,
             'mac_vendor_enriched': None, 'hostname': 'CDELNNNB0283'},
        ])
        cp.predict_categories(df, hostname_column='hostname')
        stats = cp.get_stats()
        assert stats['by_rule_type'].get('hostname_pattern', 0) == 1
        assert stats['fallback_predictions'] == 0


class TestPostAggregationHostname:
    """Post-aggregation path extracts hostname from array and uses it for prediction."""

    def test_hostname_array_extraction(self):
        """predict_categories_for_aggregated_assets() extracts hostname from array column."""
        from src.modules.asset_generation.domain.category_selector import CategorySelector

        selector = CategorySelector()
        cp = CategoryPrediction()

        # Simulate aggregated DataFrame with array-valued hostname column
        agg_df = pd.DataFrame([
            {
                'id': 'asset-001',
                'os_normalized_best': None,
                'manufacturer': [None],
                'model': [None],
                'mac_vendor_enriched': [None],
                'hostname': ['CDELNNNB0283', 'CDELNNNB0283'],
            },
            {
                'id': 'asset-002',
                'os_normalized_best': None,
                'manufacturer': [None],
                'model': [None],
                'mac_vendor_enriched': [None],
                'hostname': ['CUKDT5545'],
            },
        ])

        result = selector.predict_categories_for_aggregated_assets(
            agg_df, cp, asset_id_field='id'
        )
        # asset-001: hostname resolves to laptop via NB pattern
        row1 = result[result['id'] == 'asset-001'].iloc[0]
        assert row1['subcategory_aggregated'] == 'laptop'
        # asset-002: hostname resolves to desktop via DT pattern
        row2 = result[result['id'] == 'asset-002'].iloc[0]
        assert row2['subcategory_aggregated'] == 'desktop'
