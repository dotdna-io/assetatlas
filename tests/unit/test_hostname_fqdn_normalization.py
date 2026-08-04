# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test hostname/FQDN normalization and FQDN component bridging.

Part A: Column normalization in preprocess() — detects and corrects
hostname/FQDN field misplacement (e.g., FQDN stored in hostname column).

Part B: FQDN short-hostname component bridging — extracts the short
hostname from a FQDN and adds it as an additional merge component so
records reporting only the FQDN can merge with records reporting only
the short hostname.

Relates to #289.
"""
import pytest
import pandas as pd

from src.modules.asset_generation.domain.asset_enricher import AssetEnricher
from src.modules.asset_generation.domain.asset_validator import AssetValidator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def enricher():
    return AssetEnricher()


@pytest.fixture
def validator():
    return AssetValidator()


def _make_row(**overrides):
    """Build a minimal row dict for preprocess()."""
    defaults = {
        'hostname': None, 'fqdn': None, 'ip': None, 'mac': None,
        'sn': None, 'os': None, 'manufacturer': None, 'model': None,
        'vendor_category': None, 'data_source': 'test',
    }
    defaults.update(overrides)
    return defaults


# ===========================================================================
# Part A: Hostname/FQDN Column Normalization (preprocess)
# ===========================================================================

class TestHostnameFqdnNormalization:
    """Hostname/FQDN field misplacement detection and correction."""

    def test_fqdn_in_hostname_moves_to_fqdn(self, enricher):
        """hostname='host01.domain.com', fqdn=None -> move to fqdn, extract short."""
        df = pd.DataFrame([_make_row(hostname='host01.domain.com')])
        result = enricher.preprocess(df)
        assert result.iloc[0]['hostname'] == 'host01'
        assert result.iloc[0]['fqdn'] == 'host01.domain.com'

    def test_short_hostname_in_fqdn_moves_to_hostname(self, enricher):
        """hostname=None, fqdn='host01' -> move to hostname, clear fqdn."""
        df = pd.DataFrame([_make_row(fqdn='host01')])
        result = enricher.preprocess(df)
        assert result.iloc[0]['hostname'] == 'host01'
        assert result.iloc[0]['fqdn'] is None or pd.isna(result.iloc[0]['fqdn'])

    def test_swapped_fields_get_corrected(self, enricher):
        """hostname='host01.domain.com', fqdn='host01' -> swap."""
        df = pd.DataFrame([_make_row(
            hostname='host01.domain.com', fqdn='host01',
        )])
        result = enricher.preprocess(df)
        assert result.iloc[0]['hostname'] == 'host01'
        assert result.iloc[0]['fqdn'] == 'host01.domain.com'

    def test_correct_placement_unchanged(self, enricher):
        """hostname='host01', fqdn='host01.domain.com' -> no change."""
        df = pd.DataFrame([_make_row(
            hostname='host01', fqdn='host01.domain.com',
        )])
        result = enricher.preprocess(df)
        assert result.iloc[0]['hostname'] == 'host01'
        assert result.iloc[0]['fqdn'] == 'host01.domain.com'

    def test_both_fqdns_extracts_short_hostname(self, enricher):
        """hostname='host01.site-a.com', fqdn='host01.site-b.com' -> extract short."""
        df = pd.DataFrame([_make_row(
            hostname='host01.site-a.com', fqdn='host01.site-b.com',
        )])
        result = enricher.preprocess(df)
        assert result.iloc[0]['hostname'] == 'host01'
        assert result.iloc[0]['fqdn'] == 'host01.site-b.com'

    def test_both_short_clears_fqdn(self, enricher):
        """hostname='host01', fqdn='host02' -> clear fqdn (not a real FQDN)."""
        df = pd.DataFrame([_make_row(hostname='host01', fqdn='host02')])
        result = enricher.preprocess(df)
        assert result.iloc[0]['hostname'] == 'host01'
        assert result.iloc[0]['fqdn'] is None or pd.isna(result.iloc[0]['fqdn'])

    def test_ip_in_hostname_not_treated_as_fqdn(self, enricher):
        """hostname='192.168.1.100' -> not mistaken for FQDN (dots are from IP)."""
        df = pd.DataFrame([_make_row(hostname='192.168.1.100')])
        result = enricher.preprocess(df)
        assert result.iloc[0]['hostname'] == '192.168.1.100'

    def test_both_null_unchanged(self, enricher):
        """Both hostname and fqdn are None -> no change, no crash."""
        df = pd.DataFrame([_make_row()])
        result = enricher.preprocess(df)
        assert pd.isna(result.iloc[0]['hostname']) or result.iloc[0]['hostname'] is None
        assert pd.isna(result.iloc[0]['fqdn']) or result.iloc[0]['fqdn'] is None

    def test_mixed_rows_normalized(self, enricher):
        """Multiple rows with different misplacement patterns all normalize."""
        df = pd.DataFrame([
            # Row 0: FQDN in hostname, fqdn empty -> move
            _make_row(hostname='srv01.corp.local'),
            # Row 1: swapped -> swap
            _make_row(hostname='web02.corp.local', fqdn='web02'),
            # Row 2: correct -> no change
            _make_row(hostname='db03', fqdn='db03.corp.local'),
            # Row 3: IP in hostname -> unchanged
            _make_row(hostname='10.0.0.1'),
        ])
        result = enricher.preprocess(df)
        # Row 0: moved
        assert result.iloc[0]['hostname'] == 'srv01'
        assert result.iloc[0]['fqdn'] == 'srv01.corp.local'
        # Row 1: swapped
        assert result.iloc[1]['hostname'] == 'web02'
        assert result.iloc[1]['fqdn'] == 'web02.corp.local'
        # Row 2: unchanged
        assert result.iloc[2]['hostname'] == 'db03'
        assert result.iloc[2]['fqdn'] == 'db03.corp.local'
        # Row 3: unchanged
        assert result.iloc[3]['hostname'] == '10.0.0.1'

    def test_no_hostname_or_fqdn_columns(self, enricher):
        """Graceful handling when hostname/fqdn columns are absent."""
        df = pd.DataFrame([{
            'ip': '10.0.0.1', 'mac': None, 'sn': None, 'os': None,
            'data_source': 'test',
        }])
        result = enricher.preprocess(df)
        # Should not crash
        assert 'ip' in result.columns


class TestPreprocessHandlesIdentifierLists:
    """Identifier columns are text[] in raw_asset_data and arrive as Python
    lists when a record carries multiple values (multi-NIC host, multi-source
    mapping). preprocess() must not crash on multi-element lists nor silently
    stringify them — the lists need to flow through intact so s02's
    _explode_array_identifiers can expand them into scalar rows.
    """

    def test_multi_mac_list_does_not_crash(self, enricher):
        """Regression: pd.isna(['m1','m2']) returns array → if array: ValueError.

        The ~2-second pipeline crash on 113 was triggered by any record whose
        mac column was a list with 2+ elements.
        """
        df = pd.DataFrame([_make_row(
            hostname='server-01',
            mac=['aa:bb:cc:dd:ee:ff', '11:22:33:44:55:66'],
        )])
        # Must not raise.
        result = enricher.preprocess(df)
        # MAC value preserved as a list of normalized addresses so
        # _explode_array_identifiers can still split into separate rows.
        mac = result.iloc[0]['mac']
        assert isinstance(mac, list)
        assert mac == ['aa:bb:cc:dd:ee:ff', '11:22:33:44:55:66']

    def test_single_element_mac_list_normalizes_to_scalar(self, enricher):
        """One-element lists collapse to a scalar so existing scalar paths still apply."""
        df = pd.DataFrame([_make_row(
            hostname='server-01',
            mac=['AABBCCDDEEFF'],
        )])
        result = enricher.preprocess(df)
        assert result.iloc[0]['mac'] == 'aa:bb:cc:dd:ee:ff'

    def test_multi_mac_list_drops_invalid_elements(self, enricher):
        """Invalid MACs in the list are dropped, valid ones survive."""
        df = pd.DataFrame([_make_row(
            hostname='server-01',
            mac=['aa:bb:cc:dd:ee:ff', 'unknown', None, ''],
        )])
        result = enricher.preprocess(df)
        # Only the valid MAC survives — collapsed to scalar.
        assert result.iloc[0]['mac'] == 'aa:bb:cc:dd:ee:ff'

    def test_all_invalid_mac_list_returns_none(self, enricher):
        df = pd.DataFrame([_make_row(
            hostname='server-01',
            mac=['unknown', None, ''],
        )])
        result = enricher.preprocess(df)
        assert result.iloc[0]['mac'] is None

    def test_hostname_list_passes_through_intact(self, enricher):
        """Multi-hostname rows used to be silently stringified to "['x', 'y']"
        by .astype(str) inside the FQDN normalization block. They should now
        flow through untouched so s02 can explode them.
        """
        df = pd.DataFrame([_make_row(
            hostname=['host-a', 'host-b'],
            ip='10.0.0.1',
        )])
        result = enricher.preprocess(df)
        assert result.iloc[0]['hostname'] == ['host-a', 'host-b']

    def test_fqdn_list_passes_through_intact(self, enricher):
        df = pd.DataFrame([_make_row(
            fqdn=['srv1.corp.local', 'srv1.dmz.local'],
            ip='10.0.0.1',
        )])
        result = enricher.preprocess(df)
        assert result.iloc[0]['fqdn'] == ['srv1.corp.local', 'srv1.dmz.local']

    def test_scalar_rows_still_normalized_when_mixed_with_list_rows(self, enricher):
        """List-typed rows are skipped by the FQDN normalizer, but scalar rows
        in the same DataFrame must still get the existing normalization.
        """
        df = pd.DataFrame([
            # List-typed row: must pass through untouched
            _make_row(hostname=['host-a', 'host-b'], ip='10.0.0.1'),
            # Scalar row with FQDN-in-hostname: must still be normalized
            _make_row(hostname='srv01.corp.local', ip='10.0.0.2'),
        ])
        result = enricher.preprocess(df).reset_index(drop=True)
        assert result.iloc[0]['hostname'] == ['host-a', 'host-b']
        assert result.iloc[1]['hostname'] == 'srv01'
        assert result.iloc[1]['fqdn'] == 'srv01.corp.local'


# ===========================================================================
# Part B: FQDN Short-Hostname Component Bridging (create_asset_field)
# ===========================================================================

class TestFqdnComponentBridging:
    """FQDN short-hostname extraction as additional merge component."""

    def _make_asset_df(self, **overrides):
        """Build a single-row DataFrame for create_asset_field()."""
        defaults = {
            'hostname': None, 'fqdn': None, 'ip': None, 'mac': None,
            'sn': None, 'manufacturer': None, 'data_source': 'test',
        }
        defaults.update(overrides)
        return pd.DataFrame([defaults])

    def test_fqdn_short_hostname_added_as_component(self, validator):
        """FQDN 'websvr01.domain.com' -> 'websvr01' added as component."""
        df = self._make_asset_df(fqdn='websvr01.domain.com')
        result = validator.create_asset_field(df)
        asset = result.iloc[0]['asset']
        parts = asset.split('|')
        assert 'websvr01.domain.com' in parts, f"FQDN not in asset: {asset}"
        assert 'websvr01' in parts, f"Short hostname not bridged: {asset}"

    def test_fqdn_short_not_duplicated_with_existing_hostname(self, validator):
        """If hostname='websvr01' already in fields, don't add again."""
        df = self._make_asset_df(
            hostname='websvr01', fqdn='websvr01.domain.com',
        )
        result = validator.create_asset_field(df)
        asset = result.iloc[0]['asset']
        parts = asset.split('|')
        # 'websvr01' should appear exactly once (from hostname, not duplicated by bridging)
        assert parts.count('websvr01') == 1, f"Duplicated short hostname: {asset}"

    def test_fqdn_short_not_added_if_invalid(self, validator):
        """Short hostname from FQDN that fails validation (< 5 chars) not added."""
        df = self._make_asset_df(fqdn='ab.domain.com')
        result = validator.create_asset_field(df)
        asset = result.iloc[0]['asset']
        parts = asset.split('|')
        assert 'ab' not in parts, f"Invalid short hostname added: {asset}"
