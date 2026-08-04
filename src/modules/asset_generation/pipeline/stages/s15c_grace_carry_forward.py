# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 15c — grace carry-forward for absent assets (#795).

Keeps an asset that has disappeared from ALL inputs in the inventory (silent,
carried forward) until it has been continuously absent for the configured grace
window, only then letting it be hard-removed. Reuses the existing s16
truncate+reinsert and s18 diff: a carried asset is re-injected into scored_df so
the diff sees it as "present" and emits no asset_removed/asset_readded.
"""

import logging
from datetime import datetime, timezone

import pandas as pd

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


def compute_grace_partition(old_assets_df, scored_df, grace_days, now: datetime, resolver=None):
    """Partition prior assets that are absent from this run into carry vs remove.

    Args:
        old_assets_df: prior `assets` snapshot (has `id`, may have `missing_since`).
        scored_df: this run's input-derived assets (has `id`).
        grace_days: days an absent asset is carried before real removal.
        now: tz-aware "current run" time.
        resolver: optional callable ``id -> current_root_id | None`` (typically
            ``UnionFind.find(id, allow_missing=True)``). An absent asset whose id
            resolves to a DIFFERENT id that is PRESENT this run was *merged away*
            — its identity was absorbed by a live asset — not genuinely lost. Such
            assets are dropped immediately and reported as merge pairs instead of
            being parked under the grace window (which would leave a duplicate row
            visible for the whole grace period). If the resolved id is absent from
            this run (e.g. an SN split orphaned the old root), the asset is treated
            as genuinely absent and follows the normal grace path.

    Returns:
        (carried_df, carry_ids, removed_ids, merged_pairs)
        carried_df: rows from old_assets_df to re-inject, with `missing_since` set.
        carry_ids / removed_ids: list[str].
        merged_pairs: {removed_id: surviving_id} for assets merged away this run.
        Note: ``carried_df['missing_since']`` values are tz-aware ``pd.Timestamp`` (UTC).
    """
    if old_assets_df is None or old_assets_df.empty:
        # Always hand back a DataFrame (never None) so callers can use
        # `carried.empty` without a separate None guard.
        empty = old_assets_df if old_assets_df is not None else pd.DataFrame()
        return (empty, [], [], {})

    old = old_assets_df.copy()
    # Normalize ids on BOTH sides: psycopg2 yields uuid.UUID, scored_df uses str.
    old['id'] = old['id'].astype(str)
    if scored_df is not None and not scored_df.empty:
        present_ids = set(scored_df['id'].astype(str))
    else:
        present_ids = set()

    absent = old[~old['id'].isin(present_ids)].copy()
    if absent.empty:
        return (old.iloc[0:0], [], [], {})

    # Separate "merged away" from "genuinely absent". An absent id that resolves
    # to a DIFFERENT id which is PRESENT this run had its identity absorbed by a
    # live asset — so drop it immediately (carrying it forward is the lingering-
    # duplicate bug) and report it as a merge pair. The present-survivor guard
    # matters: an SN/safety split can leave the resolved root an orphan absent
    # from scored_df, in which case the asset was not cleanly merged into a live
    # asset and must fall through to the normal grace path. Vectorized to mirror
    # the stale-id resolution in AssetGenerationService._assign_asset_ids.
    # No survivor can be present when the run produced nothing, so skip the
    # per-id resolve entirely on a total outage (merged_mask would be all-False).
    merged_pairs: dict = {}
    if resolver is not None and present_ids:
        roots = absent['id'].map(resolver)
        merged_mask = roots.notna() & (roots != absent['id']) & roots.isin(present_ids)
        if merged_mask.any():
            merged_pairs = dict(zip(absent.loc[merged_mask, 'id'], roots[merged_mask]))
            absent = absent[~merged_mask].copy()
            if absent.empty:
                return (old.iloc[0:0], [], [], merged_pairs)

    if 'missing_since' not in absent.columns:
        absent['missing_since'] = pd.NaT
    prior = pd.to_datetime(absent['missing_since'], errors='coerce', utc=True)
    now_ts = pd.Timestamp(now)
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize('UTC')
    effective = prior.fillna(now_ts)
    days_absent = (now_ts - effective).dt.days

    carry_mask = days_absent < int(grace_days)
    carried = absent[carry_mask].copy()
    carried['missing_since'] = effective[carry_mask]

    carry_ids = carried['id'].tolist()
    removed_ids = absent.loc[~carry_mask, 'id'].tolist()
    return (carried, carry_ids, removed_ids, merged_pairs)


class GraceCarryForwardStage(PipelineStage):
    """Carry absent-but-in-grace assets forward so they survive truncate+reinsert."""

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Grace Carry-Forward"

    @property
    def key(self) -> str:
        return "grace_carry_forward"

    @property
    def stage_label(self) -> str:
        return "Grace Carry-Forward"

    def _grace_days(self) -> int:
        """Effective grace = min(configured grace, mapping_expiration_days).

        Capping by mapping_expiration_days guarantees a carried asset's
        identifier mappings outlive the carry window (the keep-alive refreshes
        them each run), so a return always reconstructs the same id — no
        duplicate row. 0 disables the feature.
        """
        try:
            grace = int(self.service._get_setting('asset_generation', 'removal_grace_period_days'))
        except (TypeError, ValueError):
            grace = 0
        if grace <= 0:
            return 0
        try:
            map_exp = int(self.service._get_setting('asset_generation', 'mapping_expiration_days'))
        except (TypeError, ValueError):
            map_exp = grace
        return min(grace, map_exp)

    def _execute(self, ctx: PipelineContext) -> None:
        grace_days = self._grace_days()
        old_df = ctx.old_assets_df
        no_prior = old_df is None or old_df.empty
        if grace_days <= 0 or not ctx.track_changes or no_prior or ctx.scored_df is None:
            ctx.stats['stages'][self.key] = {
                'carried_forward': 0, 'removed': 0, 'merged_away': 0, 'grace_days': grace_days,
            }
            logger.info("Grace carry-forward inactive (grace_days=%s, prior=%s)", grace_days, not no_prior)
            return

        now = datetime.now(timezone.utc)
        present_count = len(ctx.scored_df)
        # Resolve each prior id to its current UnionFind root so a prior asset
        # whose identity was absorbed by another asset this run is recognised as a
        # merge (drop immediately) rather than a disappearance (park under grace).
        carried, carry_ids, removed_ids, merged_pairs = compute_grace_partition(
            old_df, ctx.scored_df, grace_days, now,
            resolver=lambda i: self.service.identifier.union_find.find(
                str(i), allow_missing=True,
            ),
        )

        # Surface the absorbed ids as merge pairs so s18/chronicle link them to the
        # surviving asset (an accurate merge) instead of logging a churny removal.
        for old_id, surviving_id in merged_pairs.items():
            ctx.merge_pairs.setdefault(old_id, surviving_id)

        # New missing onsets: carried assets whose prior missing_since was null
        # (they *just* went absent this run). Feeds anomaly A2 (mass disappearance).
        new_onsets = len(carry_ids)
        if carry_ids and 'missing_since' in old_df.columns:
            carried_prior = old_df.loc[
                old_df['id'].astype(str).isin({str(i) for i in carry_ids}), 'missing_since']
            new_onsets = int(pd.to_datetime(carried_prior, errors='coerce', utc=True).isna().sum())

        ctx.stats['stages'][self.key] = {
            'carried_forward': len(carry_ids),
            'removed': len(removed_ids),
            'merged_away': len(merged_pairs),
            'grace_days': grace_days,
            'missing_onsets': new_onsets,
        }

        if present_count == 0 and carry_ids:
            # A run that produced zero present assets while prior assets exist is a
            # likely total source outage; grace would otherwise carry everything
            # forward silently. Surface it rather than masking it.
            logger.warning(
                "Grace carry-forward: this run produced ZERO present assets — carrying "
                "all %d prior assets forward (possible total source outage)", len(carry_ids),
            )

        if not carried.empty:
            # Every row persisted this run carries the current run id; stamping the
            # carried frame before the concat avoids a full-width isin() rescan of
            # the combined DataFrame.
            carried['last_generation_run_id'] = str(ctx.generation_run_id)
            ctx.scored_df = pd.concat([ctx.scored_df, carried], ignore_index=True)
            self.service.mapping_repo.refresh_mappings_for_assets(carry_ids)

        logger.info(
            "Grace carry-forward: carried %d absent assets (<%dd), %d past grace → removed",
            len(carry_ids), grace_days, len(removed_ids),
        )
