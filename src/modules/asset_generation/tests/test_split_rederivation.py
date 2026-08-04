# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression: the split mechanism must actually separate an over-merged asset.

Mirrors the verified 113 camera case (438c3df4). Guards against 'denylist alone
splits an existing merge' ever regressing.
"""

from src.modules.asset_generation.domain.asset_identifier import AssetIdentifier

AID = "438c3df4-3001-4f62-9115-452f58b08070"
ALL = [
    "ec:71:db:7e:e1:3e",
    "ec:71:db:cb:20:0b",
    "bsc_camera_side1",
    "bsc_camera_side2",
    "c8877389-105c-4ac2-8279-c44d2c50ad31",
    "lateral",
]
PRIMARY = ["bsc_camera_side1", "ec:71:db:7e:e1:3e"]
SPINOFF = ["bsc_camera_side2", "ec:71:db:cb:20:0b"]


def _rederive(mappings):
    ident = AssetIdentifier()
    ident.load_existing_mappings(mappings)
    a = ident.group_components(PRIMARY)
    b = ident.group_components(SPINOFF)
    return a, b


def test_denylist_bridge_alone_does_not_split():
    # Drop only 'lateral' from the mappings (what Stage 3 does); all else -> AID.
    mappings = {c: AID for c in ALL if c != "lateral"}
    a, b = _rederive(mappings)
    assert a == b == AID  # still merged


def test_denylist_plus_evict_nonprimary_splits_and_preserves_primary():
    # Keep only the primary cluster's mappings; evict spin-off + bridge.
    mappings = {c: AID for c in PRIMARY}
    a, b = _rederive(mappings)
    assert a == AID  # primary keeps its id + history
    assert b != AID and b  # spin-off gets a fresh deterministic uuid5
    assert a != b  # split achieved
