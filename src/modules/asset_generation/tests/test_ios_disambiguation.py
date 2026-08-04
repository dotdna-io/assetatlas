# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for iOS/Cisco IOS disambiguation post-aggregation step."""

import pytest
import pandas as pd
from src.modules.asset_generation.services.asset_generation_service import AssetGenerationService


class TestIOSDisambiguation:
    """Test the _disambiguate_ios_os method."""

    def test_apple_manufacturer_prefixes_ios(self):
        """ios 18.5 with Apple manufacturer → apple ios 18.5"""
        df = pd.DataFrame([{
            'os_normalized': 'ios 18.5',
            'manufacturer': ['Apple'],
        }])
        result = AssetGenerationService._disambiguate_ios_os(df)
        assert result.iloc[0]['os_normalized'] == 'apple ios 18.5'

    def test_cisco_manufacturer_prefixes_ios(self):
        """ios 15.2 with Cisco manufacturer → cisco ios 15.2"""
        df = pd.DataFrame([{
            'os_normalized': 'ios 15.2',
            'manufacturer': ['Cisco Systems'],
        }])
        result = AssetGenerationService._disambiguate_ios_os(df)
        assert result.iloc[0]['os_normalized'] == 'cisco ios 15.2'

    def test_no_manufacturer_unchanged(self):
        """ios 15.3.1 with no manufacturer → unchanged"""
        df = pd.DataFrame([{
            'os_normalized': 'ios 15.3.1',
            'manufacturer': None,
        }])
        result = AssetGenerationService._disambiguate_ios_os(df)
        assert result.iloc[0]['os_normalized'] == 'ios 15.3.1'

    def test_empty_manufacturer_list_unchanged(self):
        """ios with empty manufacturer list → unchanged"""
        df = pd.DataFrame([{
            'os_normalized': 'ios 15.3.1',
            'manufacturer': [],
        }])
        result = AssetGenerationService._disambiguate_ios_os(df)
        assert result.iloc[0]['os_normalized'] == 'ios 15.3.1'

    def test_already_prefixed_cisco_unchanged(self):
        """cisco ios 15.2 → unchanged"""
        df = pd.DataFrame([{
            'os_normalized': 'cisco ios 15.2',
            'manufacturer': ['Cisco'],
        }])
        result = AssetGenerationService._disambiguate_ios_os(df)
        assert result.iloc[0]['os_normalized'] == 'cisco ios 15.2'

    def test_ios_xe_unchanged(self):
        """ios xe 17.6.3 → unchanged (not bare 'ios')"""
        df = pd.DataFrame([{
            'os_normalized': 'ios xe 17.6.3',
            'manufacturer': None,
        }])
        result = AssetGenerationService._disambiguate_ios_os(df)
        assert result.iloc[0]['os_normalized'] == 'ios xe 17.6.3'

    def test_non_ios_unchanged(self):
        """windows 11 → unchanged"""
        df = pd.DataFrame([{
            'os_normalized': 'windows 11',
            'manufacturer': ['Lenovo'],
        }])
        result = AssetGenerationService._disambiguate_ios_os(df)
        assert result.iloc[0]['os_normalized'] == 'windows 11'

    def test_null_os_unchanged(self):
        """NULL os → unchanged"""
        df = pd.DataFrame([{
            'os_normalized': None,
            'manufacturer': ['Apple'],
        }])
        result = AssetGenerationService._disambiguate_ios_os(df)
        assert result.iloc[0]['os_normalized'] is None
