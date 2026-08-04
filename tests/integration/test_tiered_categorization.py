# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression test: tiered cascade must not change any asset that rules resolved."""
import pytest
import pandas as pd
from unittest.mock import MagicMock
from src.modules.asset_generation.services.asset_generation_service import AssetGenerationService


class TestTieredCascadeRegression:
    """Verify the no-regression guarantee."""

    def test_resolved_assets_identical_with_and_without_cascade(self):
        """Run category selection with and without cascade.
        Every asset resolved by rules at confidence >= 0.85 must have
        identical category/subcategory in both runs."""
        df = pd.DataFrame([
            {"asset_id": "resolved-1", "category_best": "server",
             "subcategory_best": "physical_server", "category_confidence_best": 0.95,
             "category_rule_type_best": "os_pattern",
             "manufacturer": "Dell", "model": "PowerEdge R740",
             "os_normalized": "windows server 2022", "mac_vendor": "", "vendor_category": ""},
            {"asset_id": "resolved-2", "category_best": "workstation",
             "subcategory_best": "laptop", "category_confidence_best": 0.90,
             "category_rule_type_best": "combined_patterns",
             "manufacturer": "Apple", "model": "MacBook Pro",
             "os_normalized": "macos 14", "mac_vendor": "Apple", "vendor_category": ""},
            {"asset_id": "resolved-3", "category_best": "network",
             "subcategory_best": "switch", "category_confidence_best": 0.98,
             "category_rule_type_best": "os_pattern",
             "manufacturer": "Cisco", "model": "Catalyst 9300",
             "os_normalized": "ios-xe 17.6", "mac_vendor": "Cisco", "vendor_category": ""},
            {"asset_id": "unresolved-1", "category_best": "miscellaneous",
             "subcategory_best": "unknown", "category_confidence_best": 0.50,
             "category_rule_type_best": "fallback",
             "manufacturer": "Fortinet", "model": "FortiGate 60F",
             "os_normalized": "", "mac_vendor": "", "vendor_category": ""},
            {"asset_id": "unresolved-2", "category_best": "unknown",
             "subcategory_best": "unknown", "category_confidence_best": 0.50,
             "category_rule_type_best": "fallback",
             "manufacturer": "Zebra", "model": "TC21",
             "os_normalized": "", "mac_vendor": "", "vendor_category": ""},
        ])

        baseline = df.copy()

        # Build service mock with KB that resolves both unresolved assets
        service = AssetGenerationService.__new__(AssetGenerationService)
        service.knowledge_base = MagicMock()

        def kb_lookup(mfr, model, mac):
            if "fortinet" in mfr.lower():
                return ("network", "firewall", 0.85)
            if "zebra" in mfr.lower():
                return ("mobile", "handheld_scanner", 0.80)
            return None

        service.knowledge_base.lookup.side_effect = kb_lookup
        service.catboost_predictor = None
        service.enable_knowledge_base = True
        service.enable_ml_prediction = False
        service.rule_confidence_threshold = 0.85
        service.ml_confidence_threshold = 0.70

        result = service._apply_tiered_fallback(df)

        # CRITICAL: Verify resolved assets are IDENTICAL
        for asset_id in ["resolved-1", "resolved-2", "resolved-3"]:
            baseline_row = baseline[baseline["asset_id"] == asset_id].iloc[0]
            result_row = result[result["asset_id"] == asset_id].iloc[0]
            assert result_row["category_best"] == baseline_row["category_best"], \
                f"category changed for {asset_id}"
            assert result_row["subcategory_best"] == baseline_row["subcategory_best"], \
                f"subcategory changed for {asset_id}"
            assert result_row["category_confidence_best"] == baseline_row["category_confidence_best"], \
                f"confidence changed for {asset_id}"
            assert result_row["category_rule_type_best"] == baseline_row["category_rule_type_best"], \
                f"rule_type changed for {asset_id}"

        # Verify unresolved assets WERE changed
        unresolved1 = result[result["asset_id"] == "unresolved-1"].iloc[0]
        assert unresolved1["category_best"] == "network"
        assert unresolved1["category_rule_type_best"] == "knowledge_base"

        unresolved2 = result[result["asset_id"] == "unresolved-2"].iloc[0]
        assert unresolved2["category_best"] == "mobile"
        assert unresolved2["category_rule_type_best"] == "knowledge_base"

    def test_no_asset_moves_to_worse_category(self):
        """No asset should go from a real category to miscellaneous/unknown."""
        service = AssetGenerationService.__new__(AssetGenerationService)
        service.knowledge_base = MagicMock()
        # KB returns miscellaneous for everything — this should NOT override existing
        service.knowledge_base.lookup.return_value = ("miscellaneous", "unknown", 0.80)
        service.catboost_predictor = None
        service.enable_knowledge_base = True
        service.enable_ml_prediction = False
        service.rule_confidence_threshold = 0.85
        service.ml_confidence_threshold = 0.70

        df = pd.DataFrame([{
            "asset_id": "a1", "category_best": "server",
            "subcategory_best": "physical_server", "category_confidence_best": 0.95,
            "category_rule_type_best": "os_pattern",
            "manufacturer": "Dell", "model": "PowerEdge",
            "os_normalized": "windows server", "mac_vendor": "", "vendor_category": "",
        }])

        result = service._apply_tiered_fallback(df)
        assert result.loc[0, "category_best"] == "server"  # unchanged

    def test_mixed_resolved_and_unresolved_batch(self):
        """In a batch with both resolved and unresolved, only unresolved are touched."""
        service = AssetGenerationService.__new__(AssetGenerationService)
        service.knowledge_base = MagicMock()
        service.knowledge_base.lookup.return_value = ("printer", "laser_printer", 0.80)
        service.catboost_predictor = None
        service.enable_knowledge_base = True
        service.enable_ml_prediction = False
        service.rule_confidence_threshold = 0.85
        service.ml_confidence_threshold = 0.70

        rows = []
        # 50 resolved assets
        for i in range(50):
            rows.append({
                "asset_id": f"resolved-{i}", "category_best": "workstation",
                "subcategory_best": "laptop", "category_confidence_best": 0.92,
                "category_rule_type_best": "os_pattern",
                "manufacturer": "Dell", "model": "Latitude",
                "os_normalized": "windows 11", "mac_vendor": "", "vendor_category": "",
            })
        # 5 unresolved assets
        for i in range(5):
            rows.append({
                "asset_id": f"unresolved-{i}", "category_best": "miscellaneous",
                "subcategory_best": "unknown", "category_confidence_best": 0.50,
                "category_rule_type_best": "fallback",
                "manufacturer": "Canon", "model": "imageCLASS",
                "os_normalized": "", "mac_vendor": "", "vendor_category": "",
            })

        df = pd.DataFrame(rows)
        result = service._apply_tiered_fallback(df)

        # All 50 resolved are untouched
        resolved = result[result["asset_id"].str.startswith("resolved-")]
        assert (resolved["category_best"] == "workstation").all()
        assert (resolved["category_rule_type_best"] == "os_pattern").all()

        # All 5 unresolved are updated
        unresolved = result[result["asset_id"].str.startswith("unresolved-")]
        assert (unresolved["category_best"] == "printer").all()
        assert (unresolved["category_rule_type_best"] == "knowledge_base").all()
