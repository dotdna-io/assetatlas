# SPDX-License-Identifier: AGPL-3.0-or-later
import sys
sys.path.insert(0, '.')

import pytest
import pandas as pd
from unittest.mock import MagicMock


def make_service(**overrides):
    """Create a minimal service mock for cascade testing."""
    from src.modules.asset_generation.services.asset_generation_service import AssetGenerationService
    service = AssetGenerationService.__new__(AssetGenerationService)
    service.knowledge_base = overrides.get('knowledge_base', MagicMock())
    service.catboost_predictor = overrides.get('catboost_predictor', MagicMock())
    service.enable_knowledge_base = overrides.get('enable_knowledge_base', True)
    service.enable_ml_prediction = overrides.get('enable_ml_prediction', True)
    service.rule_confidence_threshold = overrides.get('rule_confidence_threshold', 0.85)
    service.ml_confidence_threshold = overrides.get('ml_confidence_threshold', 0.70)
    service.logger = MagicMock()
    return service


def make_df(rows):
    return pd.DataFrame(rows)


class TestTieredFallback:
    def test_resolved_assets_untouched(self):
        """Assets with high-confidence rule predictions are never modified."""
        service = make_service()
        df = make_df([{
            "asset_id": "a1", "category_best": "server",
            "subcategory_best": "physical_server", "category_confidence_best": 0.95,
            "category_rule_type_best": "os_pattern",
            "manufacturer": "Dell", "model": "PowerEdge R740",
            "os_normalized": "windows server 2022", "mac_vendor": "", "vendor_category": "",
        }])
        service.knowledge_base.lookup.return_value = ("workstation", "laptop", 0.90)
        result = service._apply_tiered_fallback(df)
        assert result.loc[0, "category_best"] == "server"
        service.knowledge_base.lookup.assert_not_called()

    def test_kb_resolves_fallback(self):
        """Tier 2 (KB) resolves a fallback asset."""
        service = make_service()
        df = make_df([{
            "asset_id": "a1", "category_best": "miscellaneous",
            "subcategory_best": "unknown", "category_confidence_best": 0.50,
            "category_rule_type_best": "fallback",
            "manufacturer": "Fortinet", "model": "FortiGate 60F",
            "os_normalized": "", "mac_vendor": "", "vendor_category": "",
        }])
        service.knowledge_base.lookup.return_value = ("network", "firewall", 0.85)
        result = service._apply_tiered_fallback(df)
        assert result.loc[0, "category_best"] == "network"
        assert result.loc[0, "subcategory_best"] == "firewall"
        assert result.loc[0, "category_rule_type_best"] == "knowledge_base"
        service.catboost_predictor.predict_batch.assert_not_called()

    def test_catboost_resolves_when_kb_misses(self):
        """Tier 3 (CatBoost) resolves when KB returns None."""
        service = make_service()
        df = make_df([{
            "asset_id": "a1", "category_best": "miscellaneous",
            "subcategory_best": "unknown", "category_confidence_best": 0.50,
            "category_rule_type_best": "fallback",
            "manufacturer": "Acme", "model": "Widget",
            "os_normalized": "", "mac_vendor": "", "vendor_category": "",
        }])
        service.knowledge_base.lookup.return_value = None
        ml_result = df.copy()
        ml_result["ml_category"] = "ot"
        ml_result["ml_subcategory"] = "plc"
        ml_result["ml_confidence"] = 0.82
        service.catboost_predictor.predict_batch.return_value = ml_result
        result = service._apply_tiered_fallback(df)
        assert result.loc[0, "category_best"] == "ot"
        assert result.loc[0, "category_rule_type_best"] == "ml_catboost"

    def test_catboost_rejected_below_threshold(self):
        """CatBoost prediction below ml_confidence_threshold stays as fallback."""
        service = make_service()
        df = make_df([{
            "asset_id": "a1", "category_best": "miscellaneous",
            "subcategory_best": "unknown", "category_confidence_best": 0.50,
            "category_rule_type_best": "fallback",
            "manufacturer": "Unknown", "model": "",
            "os_normalized": "", "mac_vendor": "", "vendor_category": "",
        }])
        service.knowledge_base.lookup.return_value = None
        ml_result = df.copy()
        ml_result["ml_category"] = "workstation"
        ml_result["ml_subcategory"] = "laptop"
        ml_result["ml_confidence"] = 0.55  # below 0.70 threshold
        service.catboost_predictor.predict_batch.return_value = ml_result
        result = service._apply_tiered_fallback(df)
        assert result.loc[0, "category_best"] == "miscellaneous"

    def test_low_confidence_rule_passed_to_lower_tiers(self):
        """Rule prediction below rule_confidence_threshold is overridable."""
        service = make_service()
        df = make_df([{
            "asset_id": "a1", "category_best": "workstation",
            "subcategory_best": "unknown", "category_confidence_best": 0.65,
            "category_rule_type_best": "hostname_pattern",
            "manufacturer": "HP", "model": "ProLiant DL380",
            "os_normalized": "", "mac_vendor": "", "vendor_category": "",
        }])
        service.knowledge_base.lookup.return_value = ("server", "physical_server", 0.90)
        result = service._apply_tiered_fallback(df)
        assert result.loc[0, "category_best"] == "server"

    def test_unknown_category_passed_to_lower_tiers(self):
        """Assets with category 'unknown' are treated as unresolved."""
        service = make_service()
        df = make_df([{
            "asset_id": "a1", "category_best": "unknown",
            "subcategory_best": "unknown", "category_confidence_best": 0.50,
            "category_rule_type_best": "fallback",
            "manufacturer": "Canon", "model": "imageCLASS",
            "os_normalized": "", "mac_vendor": "", "vendor_category": "",
        }])
        service.knowledge_base.lookup.return_value = ("printer", "laser_printer", 0.80)
        result = service._apply_tiered_fallback(df)
        assert result.loc[0, "category_best"] == "printer"

    def test_empty_df_returns_immediately(self):
        """Empty DataFrame returns without errors."""
        service = make_service()
        df = pd.DataFrame(columns=["category_best", "category_confidence_best"])
        result = service._apply_tiered_fallback(df)
        assert result.empty

    def test_all_resolved_skips_cascade(self):
        """When all assets are resolved, KB and CatBoost are never called."""
        service = make_service()
        df = make_df([
            {"asset_id": "a1", "category_best": "server", "subcategory_best": "physical_server",
             "category_confidence_best": 0.95, "category_rule_type_best": "os_pattern",
             "manufacturer": "", "model": "", "os_normalized": "", "mac_vendor": "", "vendor_category": ""},
            {"asset_id": "a2", "category_best": "workstation", "subcategory_best": "laptop",
             "category_confidence_best": 0.90, "category_rule_type_best": "combined_patterns",
             "manufacturer": "", "model": "", "os_normalized": "", "mac_vendor": "", "vendor_category": ""},
        ])
        result = service._apply_tiered_fallback(df)
        service.knowledge_base.lookup.assert_not_called()
        service.catboost_predictor.predict_batch.assert_not_called()

    def test_kb_disabled_skips_to_catboost(self):
        """When KB is disabled, unresolved assets go straight to CatBoost."""
        service = make_service(enable_knowledge_base=False)
        df = make_df([{
            "asset_id": "a1", "category_best": "miscellaneous",
            "subcategory_best": "unknown", "category_confidence_best": 0.50,
            "category_rule_type_best": "fallback",
            "manufacturer": "Fortinet", "model": "FortiGate",
            "os_normalized": "", "mac_vendor": "", "vendor_category": "",
        }])
        ml_result = df.copy()
        ml_result["ml_category"] = "network"
        ml_result["ml_subcategory"] = "firewall"
        ml_result["ml_confidence"] = 0.90
        service.catboost_predictor.predict_batch.return_value = ml_result
        result = service._apply_tiered_fallback(df)
        assert result.loc[0, "category_best"] == "network"
        service.knowledge_base.lookup.assert_not_called()
