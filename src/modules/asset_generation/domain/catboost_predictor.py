# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tier 3: CatBoost ML predictor for asset categorization."""

import logging
import os
from typing import Optional

import pandas as pd

from src.modules.asset_generation.domain.agg_utils import extract_scalar

logger = logging.getLogger(__name__)

CANONICAL_CATEGORIES = {
    "server", "workstation", "network", "mobile", "printer",
    "storage", "iot", "ot", "miscellaneous",
}


class CatBoostPredictor:
    """Loads a CatBoost model and predicts category_subcategory labels.

    Single model, joint prediction: predicts 'category_subcategory'
    (e.g. 'server_physical_server') and splits after inference.

    Graceful degradation: if model file is missing, predict() returns None.
    """

    FEATURE_COLUMNS = ["manufacturer", "model", "os_normalized", "mac_vendor", "vendor_category"]
    # Post-aggregation columns where the name differs from the model feature name
    _AGG_COLUMN_ALIASES = {
        "os_normalized": "os_normalized_best",  # renamed to os_normalized in s14
    }

    def __init__(self, model_path: str = "data/models/catboost_category.cbm"):
        self.model = None
        self.label_classes = []

        if not os.path.isfile(model_path):
            logger.warning("CatBoost model not found at %s — Tier 3 disabled", model_path)
            return

        try:
            from catboost import CatBoostClassifier
            self.model = CatBoostClassifier()
            self.model.load_model(model_path)
            self.label_classes = list(self.model.classes_)
            logger.info("CatBoost model loaded: %d classes from %s", len(self.label_classes), model_path)
        except Exception as e:
            logger.warning("Failed to load CatBoost model: %s — Tier 3 disabled", e)
            self.model = None

    def predict(
        self, manufacturer: str, model: str, os_normalized: str,
        mac_vendor: str, vendor_category: str
    ) -> Optional[tuple]:
        """Predict (category, subcategory, confidence) or None."""
        if self.model is None:
            return None

        features = pd.DataFrame([{
            "manufacturer": manufacturer or "",
            "model": model or "",
            "os_normalized": os_normalized or "",
            "mac_vendor": mac_vendor or "",
            "vendor_category": vendor_category or "",
        }])

        probas = self.model.predict_proba(features)[0]
        best_idx = probas.argmax()
        confidence = float(probas[best_idx])
        label = self.label_classes[best_idx]

        return self._split_label(label, confidence)

    def predict_batch(self, df: pd.DataFrame) -> pd.DataFrame:
        """Batch prediction. Returns df with ml_category, ml_subcategory, ml_confidence columns."""
        result = df.copy()
        if self.model is None or df.empty:
            result["ml_category"] = None
            result["ml_subcategory"] = None
            result["ml_confidence"] = 0.0
            return result

        # Build feature frame: resolve post-aggregation column names and extract scalars from lists
        feature_data = {}
        for feat_col in self.FEATURE_COLUMNS:
            src_col = self._AGG_COLUMN_ALIASES.get(feat_col, feat_col)
            if src_col in df.columns:
                feature_data[feat_col] = df[src_col].apply(extract_scalar)
            elif feat_col in df.columns:
                feature_data[feat_col] = df[feat_col].apply(extract_scalar)
            else:
                feature_data[feat_col] = ''
        features = pd.DataFrame(feature_data, index=df.index)
        probas = self.model.predict_proba(features)
        best_indices = probas.argmax(axis=1)
        confidences = probas[range(len(probas)), best_indices]
        labels = [self.label_classes[i] for i in best_indices]

        cats_subcats = [self._split_label(l, 0) for l in labels]
        result["ml_category"] = [cs[0] for cs in cats_subcats]
        result["ml_subcategory"] = [cs[1] for cs in cats_subcats]
        result["ml_confidence"] = confidences

        return result

    @staticmethod
    def _split_label(label: str, confidence: float) -> tuple:
        """Split 'category_subcategory' into (category, subcategory, confidence)."""
        parts = label.split("_", 1)
        if len(parts) == 2 and parts[0] in CANONICAL_CATEGORIES:
            return (parts[0], parts[1], confidence)
        return (label, "unknown", confidence)
