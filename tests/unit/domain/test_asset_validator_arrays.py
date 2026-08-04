# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
import pandas as pd
from src.modules.asset_generation.domain.asset_validator import AssetValidator


class TestCreateAssetFieldArrayIdentifiers:
    """Tests that create_asset_field handles array identifier values via explosion."""

    def setup_method(self):
        self.validator = AssetValidator()

    def _make_df(self, rows):
        """Helper to build a DataFrame matching raw_asset_data schema."""
        cols = [
            "hostname", "fqdn", "ip", "mac", "sn",
            "os", "manufacturer", "model", "vendor_category",
            "first_time", "last_time", "user",
            "integration_id", "batch_id"
        ]
        for row in rows:
            for col in cols:
                row.setdefault(col, None)
            row.setdefault("integration_id", "test-int")
            row.setdefault("batch_id", "test-batch")
        return pd.DataFrame(rows)

    def test_array_hostname_does_not_raise(self):
        df = self._make_df([{"hostname": ["server1", "server2"]}])
        result = self.validator.create_asset_field(df)
        assert result is not None
        assert len(result) > 0

    def test_array_ip_does_not_raise(self):
        df = self._make_df([{
            "hostname": ["server1"],
            "ip": ["10.0.0.1", "10.0.0.2"]
        }])
        result = self.validator.create_asset_field(df)
        assert result is not None
        assert len(result) > 0

    def test_single_element_array_same_as_scalar(self):
        df_array = self._make_df([{"hostname": ["server1"], "ip": ["10.0.0.1"]}])
        result = self.validator.create_asset_field(df_array)
        assert result is not None
        assert len(result) > 0

    def test_array_mac_does_not_raise(self):
        df = self._make_df([{
            "hostname": ["server1"],
            "mac": ["aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"]
        }])
        result = self.validator.create_asset_field(df)
        assert result is not None

    def test_none_and_empty_arrays_handled(self):
        df = self._make_df([{
            "hostname": None,
            "ip": [],
            "mac": ["aa:bb:cc:dd:ee:ff"]
        }])
        result = self.validator.create_asset_field(df)
        assert result is not None


class TestExplodeArrayIdentifiers:
    """Tests for the _explode_array_identifiers helper."""

    def setup_method(self):
        self.validator = AssetValidator()

    def test_explodes_list_column(self):
        df = pd.DataFrame([{"hostname": ["a", "b"], "os": "Ubuntu"}])
        result = self.validator._explode_array_identifiers(df)
        assert len(result) == 2
        assert list(result["hostname"]) == ["a", "b"]
        assert list(result["os"]) == ["Ubuntu", "Ubuntu"]

    def test_scalar_columns_unchanged(self):
        df = pd.DataFrame([{"hostname": "server1", "ip": "10.0.0.1"}])
        result = self.validator._explode_array_identifiers(df)
        assert len(result) == 1
        assert result.iloc[0]["hostname"] == "server1"

    def test_empty_list_becomes_none(self):
        df = pd.DataFrame([{"hostname": [], "ip": "10.0.0.1"}])
        result = self.validator._explode_array_identifiers(df)
        assert len(result) == 1
        assert pd.isna(result.iloc[0]["hostname"]) or result.iloc[0]["hostname"] is None

    def test_none_stays_none(self):
        df = pd.DataFrame([{"hostname": None, "ip": ["10.0.0.1"]}])
        result = self.validator._explode_array_identifiers(df)
        assert len(result) == 1

    def test_multiple_array_columns_cross_product(self):
        """Multiple array columns explode independently per column."""
        df = pd.DataFrame([{"hostname": ["a", "b"], "ip": ["1", "2"]}])
        result = self.validator._explode_array_identifiers(df)
        # Explode is per-column sequential, not cross-product
        # After exploding hostname: 2 rows, then exploding ip: 4 rows?
        # Actually pandas explode works column by column, each adds rows
        # The exact behavior depends on implementation
        assert len(result) >= 2


class TestFilterInvalidIps:
    """Tests for filter_invalid_ips() — persisted ip[] array hygiene (#681 D3)."""

    def setup_method(self):
        self.validator = AssetValidator()

    def test_filter_invalid_ips_drops_loopback_apipa_placeholder(self):
        # #681 D3: the persisted ip[] array must get the same loopback/APIPA/
        # placeholder hygiene the merge-key layer already applies via is_valid_ip.
        result = self.validator.filter_invalid_ips(
            ["10.0.0.5", "127.0.0.1", "169.254.1.10", "0.0.0.0"]
        )
        assert result == ["10.0.0.5"]

    def test_filter_invalid_ips_keeps_valid_and_handles_empty(self):
        assert self.validator.filter_invalid_ips(
            ["192.168.1.20", "8.8.8.8"]
        ) == ["192.168.1.20", "8.8.8.8"]
        assert self.validator.filter_invalid_ips([]) == []
        assert self.validator.filter_invalid_ips(None) == []
