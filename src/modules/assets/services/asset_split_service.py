# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import uuid
from typing import Callable, List, Optional

from src.modules.asset_generation.domain.asset_identifier import _ASSET_ID_NAMESPACE
from src.modules.asset_generation.domain.asset_validator import AssetValidator
from src.modules.assets.services import split_detection
from src.modules.assets.services.asset_service import decode_merge_component


def _rows_to_records(rows: List[dict]) -> List[frozenset]:
    """One provider row -> the frozenset of its non-excluded merge components.

    Applies the IP-exclusion, virtual-MAC exclusion, and canonical-form
    normalization (lowercasing, 'sn:' prefix) — NOT the full set of Stage 4's
    frequency/SN exclusions.
    """
    records = []
    for r in rows:
        comps = set()
        for key in ("hostname", "fqdn"):
            val = (r.get(key) or "").strip().lower()
            if val:
                comps.add(val)
        sn = (r.get("sn") or "").strip().lower()
        if sn:
            comps.add(f"sn:{sn}")
        mac = (r.get("mac") or "").strip().lower()
        if mac:
            is_virtual, _ = AssetValidator.is_virtual_mac(mac)
            if not is_virtual:
                comps.add(mac)
        if comps:
            records.append(frozenset(comps))
    return records


def _expected_id(components: List[str]) -> str:
    key = "|".join(sorted({str(c) for c in components}))
    return str(uuid.uuid5(_ASSET_ID_NAMESPACE, key))


def _decode_component(comp: str) -> dict:
    """Classify a merge component for the UI picker, reusing the shared decoder.
    `component` carries the raw canonical form (e.g. 'sn:abc123') so the manual
    picker can send it straight back as `bridge`; `value` is the display form (a
    serial without its 'sn:' prefix)."""
    ctype, value = decode_merge_component(comp)
    return {"type": ctype, "value": value, "component": comp}


class AssetSplitService:
    def __init__(
        self, split_repo, denylist_service, pipeline_status: Callable[[], dict]
    ):
        self.repo = split_repo
        self.denylist = denylist_service
        self.pipeline_status = pipeline_status

    def preview(self, asset_id: str, bridge: Optional[str] = None) -> dict:
        records = _rows_to_records(self.repo.load_provider_records(asset_id))
        all_components = sorted(set().union(*records)) if records else []
        merge_keys = [_decode_component(c) for c in all_components]

        if bridge:
            part = split_detection.partition_for_bridge(records, bridge.lower())
        else:
            part = split_detection.detect_bridge(records)

        if not part:
            return {
                "merge_keys": merge_keys,
                "bridge": None,
                "clusters": [],
                "would_split": False,
                "warnings": [],
            }

        warnings = [
            f"Device with {cluster!r} would be left without a unique identifier"
            for cluster in part["clusters"]
            if split_detection.cluster_is_weak(cluster)
        ]
        return {
            "merge_keys": merge_keys,
            "bridge": part["bridge"],
            "clusters": part["clusters"],
            "would_split": True,
            "warnings": warnings,
        }

    def execute(
        self,
        asset_id: str,
        *,
        bridge: List[str],
        primary_cluster: List[str],
        clusters: List[List[str]],
        performed_by: str,
    ) -> dict:
        status = (self.pipeline_status() or {}).get("status")
        if status in ("pending", "running"):
            raise ValueError("a pipeline run is in progress; try again shortly")
        if len(clusters) < 2 or primary_cluster not in clusters:
            raise ValueError("invalid split partition")

        # Validate the client partition against the asset's OWN components:
        # delete_mappings and the denylist act globally by value, so a foreign
        # component would evict another asset's mapping or ban an unrelated
        # identifier. Only ever split identifiers actually on this asset.
        records = _rows_to_records(self.repo.load_provider_records(asset_id))
        asset_components = set().union(*records) if records else set()
        requested = set(bridge)
        for cluster in clusters:
            requested.update(cluster)
        if not requested <= asset_components:
            raise ValueError(
                "split partition references identifiers not on this asset"
            )

        # 1. Denylist each bridge component, idempotently. A duplicate
        # (identifier_type, identifier_value) hits ON CONFLICT DO NOTHING at
        # the repo layer and never raises, so it can't poison this request's
        # connection scope (a caught IntegrityError would still latch the
        # scope's errored flag and roll back the whole request at teardown).
        # Re-splitting an asset that shares an already-denylisted bridge still
        # proceeds to eviction/audit below.
        for comp in bridge:
            itype, raw = decode_merge_component(comp)
            self.denylist.add_entry(
                identifier_type=itype,
                raw_value=raw,
                reason=f"asset split of {asset_id}"[:100],
                created_by=performed_by,
                idempotent=True,
            )

        # 2. Evict non-primary clusters' components + the bridge (keep primary's).
        to_delete = set(bridge)
        split_into = [asset_id]  # primary keeps its id
        for cluster in clusters:
            if cluster == primary_cluster:
                continue
            to_delete.update(cluster)
            split_into.append(_expected_id(cluster))
        self.repo.delete_mappings(sorted(to_delete))

        # 3. Audit. split_history.split_reason is varchar(100); cap to fit a
        # multi-component bridge that would otherwise overflow it.
        reason = (f"over-merge bridge: {', '.join(bridge)}")[:100]
        self.repo.insert_split_history(
            split_from=asset_id,
            split_into=split_into,
            component_distribution={
                "primary": primary_cluster,
                "clusters": clusters,
                "bridge": bridge,
            },
            reason=reason,
            performed_by=performed_by,
        )
        return {"primary_id": asset_id, "split_into": split_into}
