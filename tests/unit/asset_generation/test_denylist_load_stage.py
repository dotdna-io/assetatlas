# SPDX-License-Identifier: AGPL-3.0-or-later
# tests/unit/asset_generation/test_denylist_load_stage.py
import src.modules.asset_generation.pipeline.stages.s02_asset_field_generation as s02mod
from src.modules.asset_generation.pipeline.stages.s02_asset_field_generation import AssetFieldGenerationStage


class _Validator:
    def __init__(self):
        self.denylist_components = None

    def create_asset_field(self, df):
        return df


class _Svc:
    def __init__(self):
        self.db = object()
        self.validator = _Validator()

    def _validate_data(self, df):
        return {}


class _Ctx:
    def __init__(self):
        self.preprocessed_df = "df"
        self.stats = {'stages': {}}


def test_loads_denylist_onto_validator(monkeypatch):
    monkeypatch.setattr(s02mod, "_load_denylist",
                         lambda db: frozenset({"web01"}))
    svc = _Svc()
    AssetFieldGenerationStage(svc)._execute(_Ctx())
    assert svc.validator.denylist_components == frozenset({"web01"})


def test_fail_open_on_error(monkeypatch):
    def boom(db):
        raise RuntimeError("db down")
    monkeypatch.setattr(s02mod, "_load_denylist", boom)
    svc = _Svc()
    AssetFieldGenerationStage(svc)._execute(_Ctx())
    assert svc.validator.denylist_components == frozenset()
