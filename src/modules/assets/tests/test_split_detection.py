# SPDX-License-Identifier: AGPL-3.0-or-later
from src.modules.assets.services.split_detection import (
    detect_bridge,
    partition_for_bridge,
    cluster_is_weak,
    connected_components,
)

# The real 113 camera over-merge, IP already excluded by the caller.
CAMERA = [
    frozenset({"lateral", "ec:71:db:7e:e1:3e"}),  # Network Scan .196
    frozenset({"lateral", "ec:71:db:cb:20:0b"}),  # Network Scan .90
    frozenset({"bsc_camera_side1", "ec:71:db:7e:e1:3e"}),  # Unifi .196
    frozenset({"bsc_camera_side2", "ec:71:db:cb:20:0b"}),  # Unifi .90
]


def test_detects_hostname_bridge_and_two_clusters():
    result = detect_bridge(CAMERA)
    assert result is not None
    assert result["bridge"] == "lateral"
    clusters = {frozenset(c) for c in result["clusters"]}
    assert clusters == {
        frozenset({"bsc_camera_side1", "ec:71:db:7e:e1:3e"}),
        frozenset({"bsc_camera_side2", "ec:71:db:cb:20:0b"}),
    }


def test_does_not_propose_a_mac_that_orphans_a_bare_hostname():
    # Cutting a MAC would leave a hostname-only cluster; auto-detect must not pick it.
    result = detect_bridge(CAMERA)
    assert result["bridge"] == "lateral"  # not a MAC


def test_partition_for_manual_bridge():
    result = partition_for_bridge(CAMERA, "lateral")
    assert result is not None
    assert len(result["clusters"]) == 2


def test_partition_returns_none_when_cut_does_not_split():
    # A component present in only one record can't disconnect the graph.
    single = [frozenset({"hostA", "aa:bb:cc:dd:ee:ff"})]
    assert partition_for_bridge(single, "aa:bb:cc:dd:ee:ff") is None


def test_detect_returns_none_when_already_single_device():
    single = [frozenset({"hostA", "aa:bb:cc:dd:ee:ff"})]
    assert detect_bridge(single) is None


def test_three_way_shared_hostname():
    records = [
        frozenset({"shared", "aa:aa:aa:aa:aa:a1"}),
        frozenset({"shared", "aa:aa:aa:aa:aa:a2"}),
        frozenset({"shared", "aa:aa:aa:aa:aa:a3"}),
        frozenset({"dev1", "aa:aa:aa:aa:aa:a1"}),
        frozenset({"dev2", "aa:aa:aa:aa:aa:a2"}),
        frozenset({"dev3", "aa:aa:aa:aa:aa:a3"}),
    ]
    result = detect_bridge(records)
    assert result["bridge"] == "shared"
    assert len(result["clusters"]) == 3


def test_cluster_is_weak():
    assert cluster_is_weak(["bare_hostname"]) is True
    assert cluster_is_weak(["host", "aa:bb:cc:dd:ee:ff"]) is False
    assert cluster_is_weak(["sn:abc123"]) is False


def test_connected_components_keeps_singletons():
    comps = connected_components([frozenset({"a"}), frozenset({"b"})])
    assert {frozenset(c) for c in comps} == {frozenset({"a"}), frozenset({"b"})}


def test_bare_hub_and_spoke_three_way_is_detected():
    # Three devices joined ONLY by a shared bogus hostname, each with its own MAC.
    records = [
        frozenset({"hub", "aa:aa:aa:aa:aa:a1"}),
        frozenset({"hub", "aa:aa:aa:aa:aa:a2"}),
        frozenset({"hub", "aa:aa:aa:aa:aa:a3"}),
    ]
    result = detect_bridge(records)
    assert result is not None
    assert result["bridge"] == "hub"
    assert len(result["clusters"]) == 3


def test_rejects_bridge_that_would_orphan_a_strongless_cluster():
    # Cutting 'hub' leaves {hostx, hosty} with no MAC/serial -> weak -> must be rejected.
    records = [
        frozenset({"hub", "hostx", "hosty"}),
        frozenset({"hub", "aa:aa:aa:aa:aa:a1"}),
    ]
    assert detect_bridge(records) is None


def test_tie_break_is_deterministic():
    # bridgea and bridgeb both split into two strong (MAC-bearing) clusters that
    # score identically; the smaller-lexicographic bridge must win, every run.
    records = [
        frozenset({"bridgea", "bridgeb", "aa:aa:aa:aa:aa:01"}),
        frozenset({"bridgea", "aa:aa:aa:aa:aa:02"}),
        frozenset({"bridgeb", "aa:aa:aa:aa:aa:03"}),
    ]
    result = detect_bridge(records)
    assert result is not None
    assert result["bridge"] == "bridgea"
