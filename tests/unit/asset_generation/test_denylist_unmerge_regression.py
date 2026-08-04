# SPDX-License-Identifier: AGPL-3.0-or-later
# tests/unit/asset_generation/test_denylist_unmerge_regression.py
import pandas as pd
from src.modules.asset_generation.domain.asset_validator import AssetValidator
from src.modules.asset_generation.domain.asset_identifier import AssetIdentifier


def _assign(df):
    """Assign asset ids the way Stage 4 does: split each asset field into
    components and group them through UnionFind. Returns one id per row."""
    ident = AssetIdentifier()
    ids = []
    for field in df["asset"]:
        comps = [c for c in str(field).split("|") if c]
        ids.append(ident.group_components(comps) if comps else None)
    return ids


def test_shared_hostname_merges_without_denylist():
    v = AssetValidator()
    df = pd.DataFrame([
        {"hostname": "web01", "sn": None, "mac": "aa:bb:cc:dd:ee:01", "ip": None, "fqdn": None, "data_source": "s1"},
        {"hostname": "web01", "sn": None, "mac": "aa:bb:cc:dd:ee:02", "ip": None, "fqdn": None, "data_source": "s2"},
    ])
    ids = _assign(v.create_asset_field(df))
    assert ids[0] == ids[1]  # one Frankenstein asset — the bug


def test_denylist_splits_into_two_assets():
    v = AssetValidator()
    v.denylist_components = frozenset({"web01"})
    df = pd.DataFrame([
        {"hostname": "web01", "sn": None, "mac": "aa:bb:cc:dd:ee:01", "ip": None, "fqdn": None, "data_source": "s1"},
        {"hostname": "web01", "sn": None, "mac": "aa:bb:cc:dd:ee:02", "ip": None, "fqdn": None, "data_source": "s2"},
    ])
    ids = _assign(v.create_asset_field(df))
    assert ids[0] is not None and ids[1] is not None
    assert ids[0] != ids[1]  # fixed: two distinct assets, each by its own MAC
