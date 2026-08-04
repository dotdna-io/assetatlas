# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pure bridge-detection for the asset-split feature.

Given the source records that formed an over-merged asset (each record a set of
its merge components, exclusions already applied by the caller), find the single
identifier ("bridge") whose removal partitions the asset into >= 2 device
clusters. No DB access — deterministic and unit-testable.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional


def _is_strong(component: str) -> bool:
    """A MAC (xx:xx:xx:xx:xx:xx) or serial (sn:...) uniquely identifies a device."""
    return component.startswith("sn:") or component.count(":") == 5


def cluster_is_weak(cluster: list[str]) -> bool:
    """True if a cluster keeps no strong identifier (only hostnames)."""
    return not any(_is_strong(c) for c in cluster)


def connected_components(records: list[frozenset[str]]) -> list[set[str]]:
    """Union-find over components; all components within one record are unioned."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for rec in records:
        items = list(rec)
        if items:
            find(items[0])
        for other in items[1:]:
            union(items[0], other)

    groups: dict[str, set[str]] = {}
    for node in list(parent):
        groups.setdefault(find(node), set()).add(node)
    return list(groups.values())


def partition_for_bridge(records: list[frozenset[str]], bridge: str) -> Optional[dict]:
    """Clusters that result from cutting a caller-specified bridge, or None if
    removing it does not split the asset into >= 2 clusters."""
    records = [r for r in records if r]
    reduced = [rec - {bridge} for rec in records]
    reduced = [r for r in reduced if r]
    clusters = connected_components(reduced)
    if len(clusters) < 2:
        return None
    return {"bridge": bridge, "clusters": [sorted(g) for g in clusters]}


def detect_bridge(records: list[frozenset[str]]) -> Optional[dict]:
    """Auto-detect the cleanest bridge, or None.

    None when: no records, the asset is already >1 component (not merged), or no
    single component's removal yields >= 2 clusters that EACH keep a strong id.
    """
    records = [r for r in records if r]
    if not records or len(connected_components(records)) != 1:
        return None

    freq = Counter(c for rec in records for c in rec)
    candidates = sorted(c for c, n in freq.items() if n >= 2)

    best = None  # (score, bridge, clusters)
    for cand in candidates:
        part = partition_for_bridge(records, cand)
        if part is None:
            continue
        clusters = part["clusters"]
        if any(cluster_is_weak(g) for g in clusters):
            continue  # every real device must keep a strong id
        score = (len(clusters), -max(len(g) for g in clusters))
        if best is None or score > best[0]:
            best = (score, cand, clusters)

    if best is None:
        return None
    _, bridge, clusters = best
    return {"bridge": bridge, "clusters": clusters}
