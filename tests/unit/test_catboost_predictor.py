# SPDX-License-Identifier: AGPL-3.0-or-later
import sys
sys.path.insert(0, '.')

import pytest
from unittest.mock import MagicMock
import numpy as np
import pandas as pd
from src.modules.asset_generation.domain.catboost_predictor import CatBoostPredictor


def test_returns_none_when_model_missing():
    predictor = CatBoostPredictor(model_path="/nonexistent/model.cbm")
    result = predictor.predict("Dell", "Latitude 7490", "windows 11", "", "")
    assert result is None


def test_predict_returns_tuple():
    predictor = CatBoostPredictor.__new__(CatBoostPredictor)
    predictor.model = MagicMock()
    predictor.model.predict_proba.return_value = np.array([[0.05, 0.05, 0.85, 0.05]])
    predictor.label_classes = ["workstation_laptop", "network_switch", "server_physical_server", "mobile_phone"]

    result = predictor.predict("Dell", "PowerEdge R740", "", "", "server")
    assert result is not None
    cat, subcat, conf = result
    assert cat == "server"
    assert subcat == "physical_server"
    assert conf == pytest.approx(0.85, abs=0.01)


def test_predict_batch_returns_dataframe():
    predictor = CatBoostPredictor.__new__(CatBoostPredictor)
    predictor.model = MagicMock()
    predictor.model.predict_proba.return_value = np.array([[0.1, 0.85, 0.05], [0.8, 0.1, 0.1]])
    predictor.label_classes = ["workstation_laptop", "server_physical_server", "network_switch"]

    df = pd.DataFrame({
        "manufacturer": ["Dell", "Dell"],
        "model": ["PowerEdge R740", "Latitude 7490"],
        "os_normalized": ["", "windows 11"],
        "mac_vendor": ["", ""],
        "vendor_category": ["", ""]
    })
    result = predictor.predict_batch(df)
    assert "ml_category" in result.columns
    assert "ml_subcategory" in result.columns
    assert "ml_confidence" in result.columns
    assert result.iloc[0]["ml_category"] == "server"
    assert result.iloc[1]["ml_category"] == "workstation"


def test_predict_batch_empty_df():
    predictor = CatBoostPredictor.__new__(CatBoostPredictor)
    predictor.model = None
    df = pd.DataFrame(columns=["manufacturer", "model", "os_normalized", "mac_vendor", "vendor_category"])
    result = predictor.predict_batch(df)
    assert "ml_category" in result.columns


def test_split_label_standard():
    assert CatBoostPredictor._split_label("server_physical_server", 0.9) == ("server", "physical_server", 0.9)


def test_split_label_single_part():
    assert CatBoostPredictor._split_label("miscellaneous", 0.5) == ("miscellaneous", "unknown", 0.5)


def test_split_label_multi_underscore():
    assert CatBoostPredictor._split_label("workstation_virtual_desktop", 0.8) == ("workstation", "virtual_desktop", 0.8)
