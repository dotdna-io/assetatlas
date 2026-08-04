# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from unittest.mock import MagicMock

import pytest

from src.modules.assets.services.asset_split_service import AssetSplitService

AID = "438c3df4-3001-4f62-9115-452f58b08070"

CAMERA_ROWS = [
    {"hostname": "Lateral", "fqdn": None, "mac": "ec:71:db:7e:e1:3e", "sn": None},
    {"hostname": "Lateral", "fqdn": None, "mac": "ec:71:db:cb:20:0b", "sn": None},
    {
        "hostname": "bsc_camera_side1",
        "fqdn": None,
        "mac": "ec:71:db:7e:e1:3e",
        "sn": None,
    },
    {
        "hostname": "bsc_camera_side2",
        "fqdn": None,
        "mac": "ec:71:db:cb:20:0b",
        "sn": None,
    },
]


def _service(rows=CAMERA_ROWS, status="idle"):
    repo = MagicMock()
    repo.load_provider_records.return_value = rows
    repo.delete_mappings.return_value = 2
    denylist = MagicMock()
    return AssetSplitService(repo, denylist, lambda: {"status": status}), repo, denylist


def test_execute_rejects_partition_with_foreign_components():
    # A crafted request must not denylist an unrelated identifier or evict
    # another asset's mapping — every component must belong to THIS asset.
    svc, repo, denylist = _service()
    with pytest.raises(ValueError, match="not on this asset"):
        svc.execute(
            AID,
            bridge=["lateral"],
            primary_cluster=["bsc_camera_side1", "ec:71:db:7e:e1:3e"],
            clusters=[
                ["bsc_camera_side1", "ec:71:db:7e:e1:3e"],
                ["someone-elses-host", "ab:cd:ef:12:34:56"],
            ],
            performed_by="admin-1",
        )
    denylist.add_entry.assert_not_called()
    repo.delete_mappings.assert_not_called()


def test_preview_auto_detects_bridge():
    svc, _, _ = _service()
    out = svc.preview(AID)
    assert out["bridge"] == "lateral"
    assert out["would_split"] is True
    assert len(out["clusters"]) == 2


def test_preview_manual_wont_split_on_private_identifier():
    # Cutting a device's own hostname doesn't disconnect the graph -> no split.
    svc, _, _ = _service()
    out = svc.preview(AID, bridge="bsc_camera_side1")
    assert out["would_split"] is False
    assert out["bridge"] is None


def test_preview_manual_mac_cut_flags_weak_cluster():
    # Cutting one device's own MAC still yields 2 clusters but leaves the other
    # device as a bare-hostname cluster with no strong id -> flagged in warnings.
    svc, _, _ = _service()
    out = svc.preview(AID, bridge="ec:71:db:7e:e1:3e")
    assert out["would_split"] is True
    assert out["warnings"]


def test_execute_rejects_when_pipeline_running():
    svc, _, _ = _service(status="running")
    with pytest.raises(ValueError, match="pipeline run is in progress"):
        svc.execute(
            AID,
            bridge=["lateral"],
            primary_cluster=["bsc_camera_side1", "ec:71:db:7e:e1:3e"],
            clusters=[
                ["bsc_camera_side1", "ec:71:db:7e:e1:3e"],
                ["bsc_camera_side2", "ec:71:db:cb:20:0b"],
            ],
            performed_by="admin-1",
        )


def test_execute_rejects_primary_cluster_not_in_clusters():
    svc, repo, denylist = _service()
    with pytest.raises(ValueError, match="invalid split partition"):
        svc.execute(
            AID,
            bridge=["lateral"],
            primary_cluster=["not-a-real-cluster"],
            clusters=[
                ["bsc_camera_side1", "ec:71:db:7e:e1:3e"],
                ["bsc_camera_side2", "ec:71:db:cb:20:0b"],
            ],
            performed_by="admin-1",
        )
    denylist.add_entry.assert_not_called()
    repo.delete_mappings.assert_not_called()
    repo.insert_split_history.assert_not_called()


def test_execute_rejects_fewer_than_two_clusters():
    svc, repo, denylist = _service()
    with pytest.raises(ValueError, match="invalid split partition"):
        svc.execute(
            AID,
            bridge=["lateral"],
            primary_cluster=["bsc_camera_side1", "ec:71:db:7e:e1:3e"],
            clusters=[["bsc_camera_side1", "ec:71:db:7e:e1:3e"]],
            performed_by="admin-1",
        )
    denylist.add_entry.assert_not_called()
    repo.delete_mappings.assert_not_called()
    repo.insert_split_history.assert_not_called()


def test_execute_denylists_bridge_evicts_nonprimary_and_audits():
    svc, repo, denylist = _service()
    out = svc.execute(
        AID,
        bridge=["lateral"],
        primary_cluster=["bsc_camera_side1", "ec:71:db:7e:e1:3e"],
        clusters=[
            ["bsc_camera_side1", "ec:71:db:7e:e1:3e"],
            ["bsc_camera_side2", "ec:71:db:cb:20:0b"],
        ],
        performed_by="admin-1",
    )
    # bridge denylisted
    denylist.add_entry.assert_called_once()
    assert denylist.add_entry.call_args.kwargs["raw_value"] == "lateral"
    # evicted = non-primary cluster components + the bridge (NOT the primary's)
    deleted = set(repo.delete_mappings.call_args.args[0])
    assert deleted == {"bsc_camera_side2", "ec:71:db:cb:20:0b", "lateral"}
    # audit written; primary keeps the original id
    repo.insert_split_history.assert_called_once()
    assert out["primary_id"] == AID
    assert AID in out["split_into"]
    # spin-off id is the deterministic uuid5 of its components
    spin = uuid.uuid5(
        uuid.UUID("a55e7a55-0000-4000-8000-a55e7a55e7a5"),
        "bsc_camera_side2|ec:71:db:cb:20:0b",
    )
    assert str(spin) in out["split_into"]


def test_preview_includes_merge_keys():
    svc, _, _ = _service()
    keys = svc.preview(AID)["merge_keys"]
    assert len(keys) == 5  # 3 distinct hostnames + 2 MACs in CAMERA_ROWS
    assert sum(1 for k in keys if k["type"] == "mac") == 2
    assert sum(1 for k in keys if k["type"] == "hostname") == 3


def test_preview_returns_merge_keys_even_when_no_autosplit():
    # NB: mac must not trip AssetValidator.is_virtual_mac's locally-administered
    # heuristic (e.g. aa:bb:... has that bit set) or _rows_to_records drops it.
    svc, _, _ = _service(
        rows=[
            {"hostname": "solo", "fqdn": None, "mac": "00:11:22:33:44:55", "sn": None}
        ]
    )
    out = svc.preview(AID)
    assert out["would_split"] is False
    assert len(out["merge_keys"]) == 2  # picker still has candidates


def test_execute_idempotent_when_bridge_already_denylisted():
    # Real-DB behavior: denylist.add_entry(idempotent=True) hits ON CONFLICT DO
    # NOTHING and returns None rather than raising — this avoids the
    # request-scope poisoning a caught IntegrityError would cause, structurally
    # rather than by catching an exception (full proof is the live 113 run).
    svc, repo, denylist = _service()
    denylist.add_entry.return_value = None
    out = svc.execute(
        AID,
        bridge=["lateral"],
        primary_cluster=["bsc_camera_side1", "ec:71:db:7e:e1:3e"],
        clusters=[
            ["bsc_camera_side1", "ec:71:db:7e:e1:3e"],
            ["bsc_camera_side2", "ec:71:db:cb:20:0b"],
        ],
        performed_by="admin-1",
    )
    assert denylist.add_entry.call_args.kwargs["idempotent"] is True
    repo.delete_mappings.assert_called_once()  # eviction still ran
    repo.insert_split_history.assert_called_once()  # audit still ran
    assert out["primary_id"] == AID


def test_execute_denylists_mac_bridge_with_correct_type():
    svc, repo, denylist = _service()
    svc.execute(
        AID,
        bridge=["ec:71:db:7e:e1:3e"],
        primary_cluster=["bsc_camera_side2", "ec:71:db:cb:20:0b"],
        clusters=[["bsc_camera_side2", "ec:71:db:cb:20:0b"], ["bsc_camera_side1"]],
        performed_by="admin-1",
    )
    kw = denylist.add_entry.call_args.kwargs
    assert kw["identifier_type"] == "mac"
    assert kw["raw_value"] == "ec:71:db:7e:e1:3e"
