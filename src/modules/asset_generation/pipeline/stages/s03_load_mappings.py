# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 3 — load existing component-to-asset-ID mappings."""

import logging

import pandas as pd

from src.modules.asset_generation.domain.asset_validator import AssetValidator
from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class LoadMappingsStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "Stage 3/12: Loading Existing Asset ID Mappings"

    @property
    def key(self) -> str:
        return "load_mappings"

    @property
    def stage_label(self) -> str:
        return "Load Existing Mappings"

    def _execute(self, ctx: PipelineContext) -> None:
        # Extract all components from asset field
        all_components = set()
        for asset_field_value in ctx.asset_field_df['asset']:
            if pd.notna(asset_field_value) and asset_field_value:
                components = [
                    c.strip().lower()
                    for c in str(asset_field_value).split('|')
                    if c.strip()
                ]
                all_components.update(components)

        ctx.all_components = all_components
        logger.info("  Found %d unique components in asset fields", len(all_components))

        # Load ALL mappings (not filtered by dataset) — needed for UnionFind correctness.
        # Then validate against current rules to drop historically invalid entries (#281).
        raw_mappings = self.service.mapping_repo.load_all_mappings()
        denylist = getattr(self.service.validator, "denylist_components", frozenset())
        validated_mappings, skipped = self._validate_mappings(raw_mappings, denylist)
        total_skipped = skipped['invalid_value']

        ctx.existing_mappings = validated_mappings
        self.service.identifier.load_existing_mappings(validated_mappings)

        ctx.stats['stages'][self.key] = {
            'mapping_count': len(validated_mappings),
            'mapping_count_raw': len(raw_mappings),
            'mappings_filtered': total_skipped,
            'mappings_denylisted': skipped['denylisted'],
            'components_in_dataset': len(all_components),
        }

        if total_skipped > 0 or skipped['denylisted'] > 0:
            logger.info(
                "  Filtered %d stale + %d denylisted mappings",
                total_skipped, skipped['denylisted'],
            )

        logger.info(
            "  Loaded %d existing mappings (%d components in current dataset)",
            len(validated_mappings), len(all_components),
        )

    def _validate_mappings(self, raw_mappings, denylist):
        """Validate loaded mappings against current validation rules.

        Historical mappings may contain component values that predate
        current validation rules (e.g. hostname "15" predates the
        min_hostname_length=5 rule).  Filtering them here prevents
        invalid components from acting as merge keys in UnionFind.

        Also drops denylisted components: a component denylisted after it
        was recorded must not keep re-seeding the over-merge it caused,
        even though the row predates the denylist.

        `denylist` is a DenylistMatcher (or a frozenset on Stage 2's fail-open
        path), so an entry may be a pattern — a MAC prefix/range or a
        hostname/fqdn wildcard — as well as an exact component. It has no type
        context to give it here, just the bare component string, so the matcher
        dispatches on canonical shape. This loop runs over every row in
        asset_id_mappings (159k+ on dev, the whole table every run), which is why
        matching must stay O(1) in the number of denylist entries.

        NOTE: Only applies is_valid_component_value (basic sanity check),
        NOT is_valid_hostname. The ID assignment stage uses any non-IP,
        non-virtual-MAC component for grouping regardless of hostname
        validity. Applying stricter hostname rules here would discard
        mappings that the ID stage actually uses, causing new UUIDs
        every run for assets with numeric-only hostnames.
        """
        validated = {}
        skipped = {'invalid_value': 0, 'denylisted': 0}

        for component, asset_id in raw_mappings.items():
            comp_str = str(component).strip().lower() if component else ""
            if not comp_str:
                skipped['invalid_value'] += 1
                continue

            if comp_str in denylist:
                skipped['denylisted'] += 1
                continue

            if not AssetValidator.is_valid_component_value(comp_str):
                skipped['invalid_value'] += 1
                continue

            validated[comp_str] = asset_id

        return validated, skipped
