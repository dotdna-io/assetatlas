# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Generation Service - Main orchestrator for asset generation pipeline.

This module is responsible for:
- Orchestrating the complete asset generation workflow
- Coordinating domain services, repositories, and database operations
- Managing the end-to-end pipeline from raw data to final assets
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Dict, List, Optional, Any, Tuple
from uuid import UUID, uuid4, uuid5

import numpy as np
import pandas as pd

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.asset_generation.domain.agg_utils import extract_scalar
from src.modules.asset_generation.domain.asset_identifier import AssetIdentifier
from src.modules.asset_generation.domain.asset_validator import AssetValidator
from src.modules.asset_generation.domain.asset_enricher import AssetEnricher
from src.modules.asset_generation.domain.asset_scorer import AssetScorer
from src.modules.asset_generation.domain.catboost_predictor import CatBoostPredictor
from src.modules.asset_generation.domain.device_knowledge_base import DeviceKnowledgeBase
from src.modules.asset_generation.domain.conflict_detector import ConflictDetector
from src.modules.asset_generation.domain.mac_utils import filter_virtual_macs
from src.modules.asset_generation.domain.sn_validation_enhanced import are_sns_similar
from src.modules.asset_generation.domain.manufacturer_normalizer import normalize_manufacturer, normalize_manufacturer_list
from src.modules.asset_generation.domain.os_selector import OSSelector
from src.modules.asset_generation.repositories.asset_repository import AssetRepository
from src.modules.asset_generation.repositories.mapping_repository import MappingRepository
from src.modules.asset_generation.repositories.conflict_repository import ConflictRepository
from src.modules.asset_generation.repositories.category_override_repository import CategoryOverrideRepository
from src.modules.asset_generation.repositories.asset_label_override_repository import (
    AssetLabelOverrideRepository,
)
from src.modules.asset_generation.repositories.provider_repository import ProviderRepository
from src.modules.asset_generation.repositories.excluded_records_repository import ExcludedRecordsRepository
from src.modules.asset_generation.services.chronicle_service import (
    ChronicleService,
    PIPELINE_MERGE_CAUSATION,
)
from src.modules.asset_generation.domain.category_prediction import CategoryPrediction
from src.modules.asset_generation.domain.category_selector import CategorySelector
from src.modules.tag_rules.integration import execute_tag_rules_after_asset_generation

logger = logging.getLogger(__name__)

# Dedicated logger for pipeline stage timings — always logs at WARNING
# level so that per-stage durations appear in production logs regardless
# of the application's LOG_LEVEL setting.


class AssetGenerationService:
    """
    Main orchestrator for the asset generation pipeline.

    Coordinates all components to transform raw asset data into enriched,
    scored, and validated assets with full change tracking.
    """

    MAX_INPUT_ROWS = 500_000
    MAX_UNIQUE_ASSETS = 100_000
    MAX_COMPONENTS_PER_ASSET = 50
    MAX_MANUFACTURER_FAMILIES = 2
    SLOW_PIPELINE_THRESHOLD_SECONDS = 120  # Warn if pipeline takes > 2 minutes
    SLOW_STAGE_RATIO = 0.30  # Warn if any stage takes > 30% of total time

    def __init__(
        self,
        db_adapter: PostgreSQLAdapter,
        use_uuid: bool = True,
        ml_models_path: Optional[Path] = None,
        enable_ml_prediction: bool = False,
        enable_rule_based_prediction: bool = True,
        enable_category_overrides: bool = False,
        enable_asset_label_overrides: bool = False,
        enable_provider_tracking: bool = False,
        enable_knowledge_base: bool = True,
        rule_confidence_threshold: float = 0.85,
        ml_confidence_threshold: float = 0.70
    ):
        """
        Initialize asset generation service.

        Args:
            db_adapter: PostgreSQL database adapter
            use_uuid: Whether to use UUID-based asset IDs (vs numeric)
            ml_models_path: Path to ML models directory (for category prediction)
            enable_ml_prediction: Whether to enable ML-based category prediction
            enable_rule_based_prediction: Whether to enable rule-based category prediction
            enable_category_overrides: Whether to enable manual category overrides
            enable_asset_label_overrides: Whether to re-apply manual asset_label overrides post-truncate
            enable_provider_tracking: Whether to save provider data
            enable_knowledge_base: Whether to enable KB lookups for tiered fallback
            rule_confidence_threshold: Min confidence for a rule prediction to be considered resolved
            ml_confidence_threshold: Min confidence for a CatBoost prediction to be accepted
        """
        self.db = db_adapter
        self.use_uuid = use_uuid
        self.enable_ml_prediction = enable_ml_prediction
        self.enable_rule_based_prediction = enable_rule_based_prediction
        self.enable_category_overrides = enable_category_overrides
        self.enable_provider_tracking = enable_provider_tracking
        self.enable_knowledge_base = enable_knowledge_base
        self.rule_confidence_threshold = rule_confidence_threshold
        self.ml_confidence_threshold = ml_confidence_threshold

        # Initialize device knowledge base for tiered fallback (#342)
        if enable_knowledge_base:
            self.knowledge_base = DeviceKnowledgeBase()
        else:
            self.knowledge_base = None

        # Initialize all domain services and repositories
        self.identifier = AssetIdentifier()
        self.validator = AssetValidator(device_kb=self.knowledge_base)
        self.enricher = AssetEnricher(postgres_adapter=db_adapter)  # Pass db_adapter for EOL enrichment
        self.scorer = AssetScorer()
        self.os_selector = OSSelector()
        self.category_selector = CategorySelector()

        # Initialize ML classifier if enabled
        if enable_ml_prediction and ml_models_path:
            self.catboost_predictor = CatBoostPredictor(model_path=ml_models_path)
        else:
            self.catboost_predictor = None

        # Initialize rule-based category predictor (enabled by default)
        if enable_rule_based_prediction:
            self.category_predictor = CategoryPrediction()
        else:
            self.category_predictor = None

        self.asset_repo = AssetRepository(db_adapter)
        self.mapping_repo = MappingRepository(db_adapter)
        self.conflict_repo = ConflictRepository(db_adapter)
        self.excluded_records_repo = ExcludedRecordsRepository(db_adapter)
        self.chronicle_service = ChronicleService(db_adapter)

        # Initialize optional repositories
        if enable_category_overrides:
            self.category_override_repo = CategoryOverrideRepository(db_adapter)
        else:
            self.category_override_repo = None

        self.enable_asset_label_overrides = enable_asset_label_overrides
        if enable_asset_label_overrides:
            self.asset_label_override_repo = AssetLabelOverrideRepository(db_adapter)
        else:
            self.asset_label_override_repo = None

        if enable_provider_tracking:
            self.provider_repo = ProviderRepository(db_adapter)
        else:
            self.provider_repo = None

        # Background thread pool for async I/O operations
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._excluded_records_future: Optional[Future] = None

        logger.info(
            f"AssetGenerationService initialized "
            f"(Rule-based prediction: {enable_rule_based_prediction}, "
            f"ML prediction: {enable_ml_prediction}, "
            f"Category overrides: {enable_category_overrides}, "
            f"asset_label overrides: {enable_asset_label_overrides}, "
            f"Provider tracking: {enable_provider_tracking})"
        )

    def _get_setting(self, category: str, key: str):
        """Read a setting from system_settings, falling back to schema default."""
        import json
        from src.api.modules.settings.settings_schema import get_default_value
        try:
            rows = self.db.execute_query(
                "SELECT value FROM system_settings WHERE category = %(category)s AND key = %(key)s",
                {'category': category, 'key': key}
            )
            if rows:
                val = rows[0]['value']
                if isinstance(val, str):
                    return json.loads(val)
                return val
            return get_default_value(category, key)
        except Exception:
            return get_default_value(category, key)

    # =========================================================================
    # INPUT VALIDATION
    # =========================================================================

    def _validate_input(self, df: pd.DataFrame) -> None:
        """Validate raw input DataFrame before pipeline processing."""
        if df is None or df.empty:
            raise ValueError("Input DataFrame is empty or None")

        required_cols = {'hostname', 'ip', 'mac', 'sn', 'data_source'}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        dupes = df.columns[df.columns.duplicated()].tolist()
        if dupes:
            raise ValueError(f"Duplicate columns found: {dupes}")

        if len(df) > self.MAX_INPUT_ROWS:
            raise ValueError(
                f"Input too large ({len(df)} rows, max {self.MAX_INPUT_ROWS})"
            )

    def _persist_with_scope(
        self,
        scored_df: pd.DataFrame,
        generation_run_id,
        providers_df_prepared,
    ) -> Dict[str, Any]:
        """
        Persist all Stage 9 data atomically using a connection scope.

        Wraps archive, asset insert, mapping save, and provider save in a
        single database transaction. If any step fails, the entire transaction
        is rolled back.
        """
        conn_manager = self.db.conn_manager
        # Only own the scope if one isn't already active (e.g., from generate_assets)
        owned_scope = not conn_manager.is_scope_active()
        if owned_scope:
            conn_manager.begin_scope()
        error = None
        try:
            # 1. Archive existing + truncate + insert new assets
            assets_saved = self.asset_repo.save_assets(
                scored_df, generation_run_id=generation_run_id
            )
            logger.info(f"  ✓ Saved {assets_saved} assets to database")

            # 2. Save updated mappings
            new_mappings = self.identifier.get_all_mappings()
            mappings_saved = self.mapping_repo.save_mappings_bulk(
                new_mappings, use_uuid=self.use_uuid
            )
            logger.info(f"  ✓ Saved {mappings_saved} asset ID mappings")

            # 3. Save provider data
            providers_saved = 0
            if (self.enable_provider_tracking and self.provider_repo
                    and providers_df_prepared is not None):
                logger.info("  Saving provider records (prepared in Stage 6.8)...")
                providers_df_prepared['generation_run_id'] = str(generation_run_id)
                providers_saved = self.provider_repo.save_providers(
                    providers_df_prepared
                )
                logger.info(
                    f"  ✓ Saved {providers_saved} provider records with generation_run_id"
                )

            # Note: No explicit commit here — the outer scope in generate_assets()
            # commits after all stages (9.5 tag rules, 10 chronicle, 11 lifecycle,
            # 12 alerts) complete. This ensures all-or-nothing atomicity. (#104)
            logger.info("  ✓ Stage 9 data written (commit deferred to outer scope)")

            return {
                'assets_saved': assets_saved,
                'mappings_saved': mappings_saved,
                'providers_saved': providers_saved
                    if self.enable_provider_tracking else 'disabled',
            }

        except Exception as e:
            error = e
            raise
        finally:
            if owned_scope:
                conn_manager.end_scope(error=error)

    # =========================================================================
    # MAIN ORCHESTRATION — removed (now in PipelineOrchestrator)
    # See: src/modules/asset_generation/services/pipeline_orchestrator.py
    # The monolithic generate_assets() method (836 lines) has been replaced
    # by 21 composable PipelineStage classes that delegate to the private
    # methods below.
    # =========================================================================

    # =========================================================================
    # PIPELINE STAGES (called by PipelineOrchestrator via stage classes)
    # =========================================================================

    def _preprocess_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Preprocess raw asset data.

        Args:
            df: Raw asset data

        Returns:
            Preprocessed DataFrame
        """
        logger.info(f"Preprocessing {len(df)} records...")

        # Use enricher's preprocessing
        preprocessed = self.enricher.preprocess(df)

        logger.info(f"Preprocessing complete: {len(preprocessed)} records")
        return preprocessed

    def _validate_data(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Validate asset data quality.

        Args:
            df: DataFrame to validate

        Returns:
            Validation statistics
        """
        logger.info("Validating data quality...")

        validation_stats = self.validator.get_validation_stats(df)

        logger.info(
            f"Validation complete: "
            f"{validation_stats.get('suspicious_sn_count', 0)} suspicious SNs found"
        )

        return validation_stats

    def _assign_asset_ids(self, df: pd.DataFrame) -> tuple:
        """
        Assign asset IDs using UnionFind algorithm.

        Args:
            df: DataFrame with asset data

        Returns:
            Tuple of (DataFrame with assigned IDs, merge_pairs dict).
            merge_pairs maps {removed_id: surviving_id} for assets merged
            by UnionFind during stale-ID resolution.
        """
        logger.info("Assigning asset IDs...")
        merge_pairs = {}

        # Initialize ID column
        if 'id' not in df.columns:
            df['id'] = pd.NA
        df['id'] = df['id'].astype(str)

        # Build SN quality map for validation
        sn_quality_map = {}
        sns_to_exclude = set()
        hostnames_with_multiple_sns = set()

        if 'hostname' in df.columns and 'sn' in df.columns:
            df_sn_check = df.dropna(subset=['hostname', 'sn'])
            if not df_sn_check.empty:
                sn_host_counts = df_sn_check.groupby('sn')['hostname'].nunique()

                for sn, host_count in sn_host_counts.items():
                    quality_score, is_migration = self.validator.evaluate_sn_quality(
                        sn, host_count
                    )
                    sn_quality_map[sn] = (quality_score, is_migration, host_count)

                    # Exclude low quality SNs
                    if quality_score < 0.5 or host_count > self.validator.sn_overuse_threshold:
                        sns_to_exclude.add(str(sn).lower())

                # Identify hostnames with multiple SNs
                # BUGFIX (#99): Skip generic hostnames (iPhone, iPad, etc.) — they are
                # EXPECTED to have multiple SNs (each device is different). Mirrors the
                # bug #8 fix in _build_exclusion_sets(). Without this, valid SNs on rows
                # with generic hostnames get stripped, causing the record to be excluded.
                host_sn_counts = df_sn_check.groupby('hostname')['sn'].nunique()
                all_hostnames_with_multiple_sns = set(
                    host_sn_counts[host_sn_counts > 1].index
                )
                generic_hostnames = self.validator.generic_hostnames
                hostnames_with_multiple_sns = {
                    hn for hn in all_hostnames_with_multiple_sns
                    if str(hn).strip().lower() not in generic_hostnames
                }

                generic_skipped = all_hostnames_with_multiple_sns - hostnames_with_multiple_sns
                if generic_skipped:
                    logger.info(
                        f"Skipped {len(generic_skipped)} generic hostnames from "
                        f"multi-SN filter (SNs preserved): {sorted(generic_skipped)}"
                    )

                logger.info(
                    f"SN Analysis: {len(sns_to_exclude)} excluded, "
                    f"{len(hostnames_with_multiple_sns)} hostnames with multiple SNs"
                )

        # Pre-compute parsed components (vectorized string splitting)
        df['_parsed_components'] = df['asset'].fillna('').astype(str).str.split('|').apply(
            lambda parts: sorted(set(s.strip() for s in parts if s.strip()))
        )

        # Pre-compute hostname flags vectorized (avoid per-row string ops)
        if 'hostname' in df.columns:
            _hn_raw = df['hostname'].fillna('').astype(str).str.strip()
            df['_hostname_lower'] = _hn_raw.str.lower()
            df['_row_has_problematic_hostname'] = _hn_raw.isin(hostnames_with_multiple_sns)
        else:
            df['_hostname_lower'] = ''
            df['_row_has_problematic_hostname'] = False

        freq_excluded_macs = self._compute_frequency_exclusions(df, 'mac')
        freq_excluded_sns = self._compute_frequency_exclusions(df, 'sn')
        sns_to_exclude.update(freq_excluded_sns)

        # --- Pre-compute final components for each row outside the UnionFind loop ---
        # This moves all filtering/validation into a tight loop that benefits from
        # lru_cache on validator methods, while keeping UnionFind mutation sequential.
        is_valid_ip = self.validator.is_valid_ip_address
        is_virtual_mac = self.validator.is_virtual_mac

        def _classify_row(components, hostname_lower, has_problematic_hostname):
            """Classify and filter components for a single row.

            Returns a tuple of (final_components, reasons):
              - final_components: list of components for UnionFind, or None/[]
              - reasons: list of strings describing why components were removed
            """
            reasons = []
            if not components:
                return None, reasons

            # Step 1: Filter excluded SNs and problematic hostnames
            filtered = []
            for comp in components:
                if comp.startswith("sn:"):
                    sn_value = comp.split(":", 1)[1] if ":" in comp else comp
                    if sn_value in sns_to_exclude:
                        reasons.append(f"SN '{sn_value}' excluded (associated with multiple hostnames)")
                        continue
                    filtered.append(comp)
                elif has_problematic_hostname and comp == hostname_lower:
                    reasons.append(f"Hostname '{hostname_lower}' excluded (associated with multiple serial numbers)")
                    continue
                else:
                    filtered.append(comp)

            if not filtered:
                return None, reasons

            # Single pass: filter transient components and check for at least one persistent
            final = []
            has_persistent = False
            for comp in filtered:
                cv = comp.strip()
                if is_valid_ip(cv):
                    reasons.append(f"IP '{cv}' excluded (transient identifier)")
                    continue
                if ':' in cv and len(cv.split(':')) == 6:
                    virt, vendor = is_virtual_mac(cv)
                    if virt:
                        reasons.append(f"MAC '{cv}' excluded (virtual - {vendor})")
                        continue
                    if cv.lower() in freq_excluded_macs:
                        reasons.append(f"MAC '{cv}' excluded (shared across multiple hostnames)")
                        continue
                final.append(comp)
                has_persistent = True

            if not has_persistent or not final:
                return [] if filtered else None, reasons
            return final, reasons

        # Classify all rows in a tight loop (no iterrows overhead)
        _all_components = df['_parsed_components'].tolist()
        _all_hn_lower = df['_hostname_lower'].tolist()
        _all_hn_problematic = df['_row_has_problematic_hostname'].tolist()

        _classified = [
            _classify_row(comps, hn, prob)
            for comps, hn, prob in zip(_all_components, _all_hn_lower, _all_hn_problematic)
        ]

        # Initialize _filter_reasons column to store per-row rejection details
        df['_filter_reasons'] = None

        # --- UnionFind loop: only stateful group_components calls ---
        id_col_idx = df.columns.get_loc('id')
        empty_after_filter_count = 0
        for i, (final, reasons) in enumerate(_classified):
            if reasons:
                df.at[df.index[i], '_filter_reasons'] = reasons
            if final is None:
                df.iat[i, id_col_idx] = "<NA>"
                continue
            if not final:
                # All components removed by transient filtering
                empty_after_filter_count += 1
                df.iat[i, id_col_idx] = "<NA>"
                continue
            asset_id = self.identifier.group_components(final)
            df.iat[i, id_col_idx] = str(asset_id)

        if empty_after_filter_count > 0:
            logger.warning(
                f"{empty_after_filter_count} rows had no components after transient filtering"
            )

        # Clean up pre-computation columns
        df.drop(columns=['_hostname_lower', '_row_has_problematic_hostname'], inplace=True, errors='ignore')

        # BUGFIX: Resolve all asset IDs to their current roots
        # When merges happen during processing, earlier records may have stale IDs from before the merge
        # We need to resolve all IDs through UnionFind.find() to get their current roots
        # This ensures deterministic results regardless of processing order
        logger.info("Resolving asset IDs to current roots after merges...")
        stale_id_count = 0
        # Vectorized stale ID resolution
        valid_mask = (df['id'].notna()) & (df['id'] != '<NA>')
        if valid_mask.any():
            valid_ids = df.loc[valid_mask, 'id']
            resolved_ids = valid_ids.map(
                lambda x: self.identifier.union_find.find(x, allow_missing=True)
            )
            # Log errors for None results (shouldn't happen)
            none_mask = resolved_ids.isna()
            if none_mask.any():
                for bad_id in valid_ids[none_mask]:
                    logger.error(f"Asset ID {bad_id} not found in UnionFind - this shouldn't happen!")
            # Count stale before updating
            changed_mask = (resolved_ids != valid_ids) & resolved_ids.notna()
            stale_id_count = int(changed_mask.sum())
            # Collect merge pairs for chronicle linkage
            if changed_mask.any():
                for old_id, new_id in zip(
                    valid_ids[changed_mask], resolved_ids[changed_mask]
                ):
                    if old_id != new_id and old_id not in merge_pairs:
                        merge_pairs[old_id] = new_id
            # Apply resolved IDs (keep original if resolution returned None)
            df.loc[valid_mask, 'id'] = resolved_ids.where(resolved_ids.notna(), valid_ids)

        if stale_id_count > 0:
            logger.info(f"Resolved {stale_id_count} stale asset IDs to their current roots")

        # Post-merge safety valve: split assets that show signs of over-merging
        # (too many hostnames or too many manufacturer families)
        df = self._post_merge_safety_check(df, self.identifier)

        # IMPROVEMENT: Post-merge SN conflict detection and resolution
        # Detect assets with multiple different SNs (different physical devices incorrectly merged)
        # and split them into separate assets
        logger.info("Checking for SN conflicts in merged assets...")
        sn_conflicts_fixed = self._detect_and_fix_sn_conflicts(df)

        if sn_conflicts_fixed > 0:
            logger.warning(f"Fixed {sn_conflicts_fixed} SN conflicts by splitting incorrectly merged assets")

        # Note: UUID conversion is not needed because UnionFind.get_new_raw_id() already generates UUIDs (uuid4)
        # The use_uuid parameter is maintained for backwards compatibility but has no effect
        # since the asset identifier always uses UUIDs internally

        # Clean up temporary pre-computed column
        df.drop(columns=['_parsed_components'], inplace=True, errors='ignore')

        logger.info(f"ID assignment complete")
        return df, merge_pairs

    # Stable namespace for deterministic SN-conflict split UUIDs.
    # uuid5(this, sn_value) produces the same ID every run for the same SN.
    _SN_SPLIT_NAMESPACE = UUID('a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d')

    # Stable namespace for deterministic post-merge safety split UUIDs.
    # uuid5(this, hostname) produces the same ID every run for the same hostname.
    _SAFETY_SPLIT_NAMESPACE = UUID('f0e1d2c3-b4a5-4968-87f6-0e1d2c3b4a59')

    @staticmethod
    def _compute_frequency_exclusions(df, col, hostname_col='hostname', threshold=3):
        """Compute identifiers that appear on too many distinct hostnames."""
        if col not in df.columns or hostname_col not in df.columns:
            return set()
        pair_df = df.dropna(subset=[col, hostname_col])[[col, hostname_col]].drop_duplicates()
        if pair_df.empty:
            return set()
        counts = pair_df.groupby(col)[hostname_col].size()
        excluded = set(counts[counts > threshold].index.str.lower())
        if excluded:
            logger.info(
                f"Frequency exclusion: {len(excluded)} {col}s appear on >{threshold} hostnames"
            )
        return excluded

    def _post_merge_safety_check(self, df: pd.DataFrame, identifier) -> pd.DataFrame:
        """Split assets that show signs of over-merging.

        Checks after UnionFind:
        - Assets with >15 distinct hostnames
        - Assets with >2 distinct manufacturer families (after normalization)
        """
        max_hostnames = AssetIdentifier.MAX_HOSTNAMES_PER_GROUP
        max_manufacturer_families = self.MAX_MANUFACTURER_FAMILIES

        valid_mask = (df['id'].notna()) & (df['id'] != '<NA>')
        if not valid_mask.any():
            return df

        asset_groups = df.loc[valid_mask].groupby('id', sort=False)

        assets_to_split = []
        for asset_id, group in asset_groups:
            # Check hostname count
            if 'hostname' in group.columns:
                hostnames = group['hostname'].dropna().str.lower().unique()
                if len(hostnames) > max_hostnames:
                    assets_to_split.append((asset_id, f"{len(hostnames)} hostnames"))
                    continue

            # Check manufacturer families
            if 'manufacturer' in group.columns:
                mfrs = group['manufacturer'].dropna().unique()
                families = set()
                for m in mfrs:
                    n = normalize_manufacturer(str(m))
                    if n:
                        families.add(n)
                if len(families) > max_manufacturer_families:
                    assets_to_split.append((asset_id, f"{len(families)} manufacturer families: {families}"))

        if not assets_to_split:
            return df

        logger.warning(f"Post-merge safety: splitting {len(assets_to_split)} over-merged assets")

        for asset_id, reason in assets_to_split:
            logger.warning(f"  Splitting asset {asset_id}: {reason}")
            mask = df['id'] == asset_id

            # Assign deterministic asset IDs per hostname (uuid5 = same hostname → same ID every run)
            group = df[mask]
            if 'hostname' in group.columns:
                for hostname in group['hostname'].dropna().unique():
                    hostname_mask = mask & (df['hostname'] == hostname)
                    new_id = str(uuid5(self._SAFETY_SPLIT_NAMESPACE, str(hostname).lower()))
                    df.loc[hostname_mask, 'id'] = new_id

            # Rows with no hostname in a split group are not meaningful assets — exclude them
            no_hostname_mask = mask & df['hostname'].isna()
            if no_hostname_mask.any():
                count = no_hostname_mask.sum()
                logger.info(f"  Excluding {count} no-hostname rows from split asset {asset_id}")
                df.loc[no_hostname_mask, 'id'] = '<NA>'

        return df

    def _detect_and_fix_sn_conflicts(self, df: pd.DataFrame) -> int:
        """
        Detect and fix serial number conflicts in merged assets.

        If an asset has multiple different serial numbers, it means different
        physical devices were incorrectly merged. Split them into separate assets.

        Uses deterministic UUIDs (uuid5 based on SN) so the same SN always
        produces the same asset ID across runs, preventing ID churn.

        Only considers SNs that passed validation (not suspicious). SNs that were
        excluded during asset field creation or ID assignment should not trigger splits.

        Args:
            df: DataFrame with assigned asset IDs

        Returns:
            Number of conflicts fixed (assets split)
        """
        conflicts_fixed = 0

        # Pre-group by asset ID once — O(N log N) instead of O(M*N) per-asset scans
        valid_mask = df['id'] != '<NA>'
        grouped = df.loc[valid_mask].groupby('id', sort=False)

        for asset_id, asset_records in grouped:
            # Get all distinct SNs for this asset (excluding None/NaN and suspicious SNs)
            sns_in_asset = set()
            for sn in asset_records['sn'].dropna().unique():
                if pd.notna(sn) and str(sn).strip() != '':
                    sn_str = str(sn).strip()
                    # Only consider SNs that are NOT suspicious
                    if not self.validator.is_sn_suspicious(sn_str):
                        sns_in_asset.add(sn_str.lower())

            # If multiple different SNs, we may have a conflict
            if len(sns_in_asset) > 1:
                # Check if all SNs are similar (typos/format differences).
                # If every pair is similar, keep the asset merged.
                sns_list = sorted(sns_in_asset)
                all_similar = all(
                    are_sns_similar(sns_list[i], sns_list[j])
                    for i in range(len(sns_list))
                    for j in range(i + 1, len(sns_list))
                )
                if all_similar:
                    logger.info(
                        f"SN variants in asset {asset_id} are similar, "
                        f"keeping merged: {sns_list}"
                    )
                    continue

                logger.warning(
                    f"SN CONFLICT detected in asset {asset_id}: "
                    f"{len(sns_in_asset)} different SNs: {sns_list}"
                )

                # Split by SN - create new asset for each SN
                # First, build a map of which components are exclusive to each SN
                # Operate on asset_records (pre-grouped) instead of re-scanning full df
                sn_to_components = {}  # sn -> set of non-SN components
                for sn in sns_in_asset:
                    sn_mask_check = (
                        asset_records['sn'].notna() &
                        (asset_records['sn'].str.lower() == sn)
                    )
                    sn_components = set()
                    for idx in asset_records[sn_mask_check].index:
                        parsed = df.at[idx, '_parsed_components']
                        if parsed:
                            for comp in parsed:
                                if not comp.startswith('sn:'):
                                    sn_components.add(comp)
                    sn_to_components[sn] = sn_components

                # Find components that appear in multiple SN groups (ambiguous)
                all_comp_sets = list(sn_to_components.values())
                shared_components = set()
                for i, comp_set_a in enumerate(all_comp_sets):
                    for comp_set_b in all_comp_sets[i+1:]:
                        shared_components |= (comp_set_a & comp_set_b)

                for sn in sns_in_asset:
                    # Find records with this SN, scoped to this asset's rows in original df
                    sn_mask = (
                        df.index.isin(asset_records.index) &
                        (df['sn'].notna()) &
                        (df['sn'].str.lower() == sn)
                    )

                    if sn_mask.any():
                        # Deterministic asset ID from SN — same SN always gets
                        # the same UUID, preventing ID churn across runs.
                        new_asset_id = str(uuid5(self._SN_SPLIT_NAMESPACE, sn))

                        # Update all records with this SN
                        df.loc[sn_mask, 'id'] = new_asset_id

                        # Update UnionFind: SN component + rank
                        sn_component = f"sn:{sn}"
                        self.identifier.union_find.parent[sn_component] = new_asset_id
                        self.identifier.union_find.parent[new_asset_id] = new_asset_id
                        self.identifier.union_find.rank[new_asset_id] = 0

                        # Update UnionFind: exclusive non-SN components
                        # Normalize components to match UnionFind keys (lowercase)
                        exclusive_components = sn_to_components.get(sn, set()) - shared_components
                        for comp in exclusive_components:
                            normalized = self.identifier.normalize_component(comp)
                            self.identifier.union_find.parent[normalized] = new_asset_id

                        remapped_count = len(exclusive_components)
                        logger.info(
                            f"  Created new asset {new_asset_id} for SN: {sn} "
                            f"(remapped {remapped_count} exclusive components)"
                        )

                # Remove shared components from UnionFind so they don't persist
                # as stale bridges between the split groups on the next run.
                # Each run re-discovers them during ID assignment anyway.
                if shared_components:
                    for comp in shared_components:
                        normalized = self.identifier.normalize_component(comp)
                        self.identifier.union_find.parent.pop(normalized, None)
                    logger.debug(
                        f"  Removed {len(shared_components)} shared components from mappings "
                        f"for asset {asset_id}: {sorted(shared_components)}"
                    )

                # Assign any remaining records still on the original asset_id
                # to the first SN group. This catches:
                # - Records with no SN (null/empty)
                # - Records with suspicious SNs (excluded from sns_in_asset)
                # - Records with whitespace-only SNs
                # Leaving them creates orphan assets that re-merge on the next run.
                first_sn = sorted(sns_in_asset)[0]
                first_sn_id = str(uuid5(self._SN_SPLIT_NAMESPACE, first_sn))
                remaining_mask = (
                    df.index.isin(asset_records.index) &
                    (df['id'] == asset_id)
                )
                if remaining_mask.any():
                    df.loc[remaining_mask, 'id'] = first_sn_id
                    logger.info(
                        f"  Assigned {remaining_mask.sum()} remaining records to asset {first_sn_id} (SN: {first_sn})"
                    )

                conflicts_fixed += 1

        return conflicts_fixed

    def _prepare_excluded_records(
        self,
        df: pd.DataFrame,
        generation_run_id: UUID
    ) -> Tuple[List[Dict], int]:
        """
        Prepare excluded records for database persistence.

        Filters, computes exclusion metadata, and builds the records list
        without performing the actual database write.

        Args:
            df: DataFrame with 'id' column (where '<NA>' = excluded)
            generation_run_id: UUID of current generation run

        Returns:
            Tuple of (excluded_records list, excluded_count)
        """
        # Filter to excluded records only
        excluded_df = df[df['id'] == '<NA>'].copy()

        if len(excluded_df) == 0:
            logger.info("No excluded records to save")
            return [], 0

        logger.info(f"Preparing {len(excluded_df)} excluded records...")

        # Resolve exclusion sets once (not per-row) for _determine_exclusion_reason
        exc_sets = self.validator.last_exclusion_sets or {
            'hostnames': set(), 'fqdns': set(), 'ips': set(),
            'macs': set(), 'sns': set(),
        }

        # Compute exclusion metadata using apply (C-level iteration, faster than iterrows)
        # Use specific filter reasons from _classify_row when available,
        # fall back to _determine_exclusion_reason for records without them.
        def _resolve_exclusion_reason(row):
            filter_reasons = row.get('_filter_reasons')
            if filter_reasons:
                return "; ".join(filter_reasons)
            return self._determine_exclusion_reason(row, exc_sets)

        excluded_df['exclusion_reason'] = excluded_df.apply(
            _resolve_exclusion_reason, axis=1
        )
        excluded_df['exclusion_category'] = excluded_df['exclusion_reason'].map(
            self._determine_exclusion_category
        )
        excluded_df['exclusion_details'] = excluded_df.apply(
            self._build_exclusion_details, axis=1
        )

        # Build boolean columns vectorized instead of per-row
        excluded_df['has_hostname'] = (
            excluded_df['hostname'].notna() & (excluded_df['hostname'].astype(str).str.strip() != '')
        ) if 'hostname' in excluded_df.columns else False
        excluded_df['has_ip'] = excluded_df['ip'].notna() if 'ip' in excluded_df.columns else False
        excluded_df['has_mac'] = excluded_df['mac'].notna() if 'mac' in excluded_df.columns else False
        excluded_df['has_sn'] = (
            excluded_df['sn'].notna() & (excluded_df['sn'].astype(str).str.strip() != '')
        ) if 'sn' in excluded_df.columns else False

        # Prepare IP and MAC as strings (vectorized)
        if 'ip' in excluded_df.columns:
            excluded_df['ip_str'] = excluded_df['ip'].where(excluded_df['ip'].notna()).astype(str).where(excluded_df['ip'].notna())
        else:
            excluded_df['ip_str'] = None
        if 'mac' in excluded_df.columns:
            excluded_df['mac_str'] = excluded_df['mac'].where(excluded_df['mac'].notna()).astype(str).where(excluded_df['mac'].notna())
        else:
            excluded_df['mac_str'] = None

        # Build records using itertuples (10-100x faster than iterrows for dict building)
        now = datetime.now(timezone.utc)
        has_id_original = 'id_original' in excluded_df.columns
        excluded_records = []
        for row in excluded_df.itertuples(index=False):
            excluded_records.append({
                'generation_run_id': generation_run_id,
                'excluded_at': now,
                'raw_record_id': getattr(row, 'id_original', None) if has_id_original else None,
                'integration_id': getattr(row, 'integration_id', None),
                'raw_source_id': getattr(row, 'raw_source_id', None),
                'data_source': getattr(row, 'data_source', None),
                'batch_id': getattr(row, 'batch_id', None),
                'hostname': getattr(row, 'hostname', None),
                'fqdn': getattr(row, 'fqdn', None),
                'ip': getattr(row, 'ip_str', None),
                'mac': getattr(row, 'mac_str', None),
                'sn': getattr(row, 'sn', None),
                'os': getattr(row, 'os', None),
                'manufacturer': getattr(row, 'manufacturer', None),
                'model': getattr(row, 'model', None),
                'vendor_category': getattr(row, 'vendor_category', None),
                'exclusion_reason': getattr(row, 'exclusion_reason', None),
                'exclusion_category': getattr(row, 'exclusion_category', None),
                'has_hostname': getattr(row, 'has_hostname', False),
                'has_ip': getattr(row, 'has_ip', False),
                'has_mac': getattr(row, 'has_mac', False),
                'has_sn': getattr(row, 'has_sn', False),
                'extended_fields': getattr(row, 'extended_fields', None),
                'exclusion_details': getattr(row, 'exclusion_details', None),
            })

        return excluded_records, len(excluded_df)

    def _write_excluded_records_background(
        self,
        excluded_records: List[Dict],
        generation_run_id: UUID,
        record_count: int
    ) -> int:
        """Write excluded records to database (runs in background thread).

        This method gets its own DB connection from the pool — it does NOT
        share the main pipeline's connection.
        """
        try:
            saved_count = self.excluded_records_repo.save_excluded_records(
                excluded_records,
                generation_run_id
            )
            logger.info(f"Background: Saved {saved_count}/{record_count} excluded records")
            return saved_count
        except Exception as e:
            logger.error(f"Background: Failed to save excluded records: {e}", exc_info=True)
            return 0

    def _determine_exclusion_reason(self, row: pd.Series, exc_sets: dict) -> str:
        """Determine why a record was excluded.

        Checks each component against validation rules AND cross-reference
        exclusion sets (#287) to produce specific sub-categorized reasons
        instead of the catch-all "Insufficient data quality".

        Args:
            row: Excluded record row.
            exc_sets: Cross-reference exclusion sets from create_asset_field.
        """
        hostname = row.get('hostname')
        ip = row.get('ip')
        mac = row.get('mac')
        sn = row.get('sn')
        asset_field = row.get('asset')

        # Check if asset field is empty or invalid
        if pd.isna(asset_field) or str(asset_field).strip() == '':
            reasons = []

            has_hostname = pd.notna(hostname) and str(hostname).strip() != ''
            has_sn = pd.notna(sn) and str(sn).strip() != ''
            has_mac = pd.notna(mac)
            has_ip = pd.notna(ip)

            # --- Hostname checks ---
            if has_hostname:
                hostname_str = str(hostname)
                hostname_lower = hostname_str.lower().strip()
                if not self.validator.is_valid_hostname(hostname_str):
                    if len(hostname_lower) < self.validator.min_hostname_length:
                        reasons.append(f"Hostname too short ('{hostname}', {len(hostname_lower)} chars)")
                    elif hostname_lower in self.validator.generic_hostnames:
                        reasons.append(f"Generic placeholder hostname ('{hostname}')")
                    elif not self.validator._hostname_has_letter_pattern.search(hostname_str):
                        reasons.append(f"Hostname has no letters ('{hostname}')")
                    elif self.validator._hostname_date_pattern.search(hostname_str):
                        reasons.append(f"Hostname contains date pattern ('{hostname}')")
                    else:
                        reasons.append(f"Hostname matched placeholder pattern ('{hostname}')")
                elif not self.validator.is_valid_component_value(hostname_lower, 'hostname'):
                    reasons.append(f"Hostname failed format validation ('{hostname}')")
                elif hostname_lower in exc_sets['hostnames']:
                    reasons.append("Hostname excluded (associated with multiple serial numbers)")
                else:
                    # Check manufacturer-name match (#26 Issue 4)
                    manufacturer_val = row.get('manufacturer')
                    if pd.notna(manufacturer_val) and hostname_lower == str(manufacturer_val).strip().lower():
                        reasons.append(f"Hostname matches manufacturer name ('{hostname}')")

            # --- Serial number checks ---
            if has_sn:
                sn_str = str(sn).strip()
                sn_lower = sn_str.lower()
                # Pass lowercase to match _build_exclusion_sets normalization
                if self.validator.is_sn_suspicious(sn_lower, exc_sets['sns']):
                    if sn_lower in exc_sets['sns']:
                        reasons.append("Serial number excluded (overused across hostnames)")
                    else:
                        reasons.append("Serial number failed validation (suspicious/placeholder)")
                elif not self.validator.is_valid_component_value(sn_lower, 'sn'):
                    reasons.append("Serial number failed format validation")

            # --- IP checks ---
            if has_ip:
                ip_str = str(ip).strip()
                if not self.validator.is_valid_ip(ip_str):
                    if ip_str.startswith(self.validator.invalid_ip_prefixes):
                        reasons.append(f"Invalid IP address ('{ip}' - loopback/APIPA)")
                    elif ip_str in self.validator.invalid_ips:
                        reasons.append(f"Placeholder IP address ('{ip}')")
                    else:
                        reasons.append(f"IP address failed validation ('{ip}')")
                elif ip_str in exc_sets['ips']:
                    reasons.append("IP excluded (associated with multiple hostnames)")

            # --- MAC checks ---
            if has_mac:
                mac_str = str(mac).strip()
                mac_lower = mac_str.lower()
                if not self.validator.is_valid_mac(mac_str):
                    mac_normalized = mac_lower.replace('-', ':')
                    if mac_normalized in self.validator.invalid_macs:
                        reasons.append(f"Invalid MAC address ('{mac}' - broadcast/zero)")
                    else:
                        reasons.append(f"MAC address failed validation ('{mac}')")
                elif mac_lower in exc_sets['macs']:
                    reasons.append("MAC excluded (associated with multiple hostnames)")
                else:
                    is_virtual, vendor = self.validator.is_virtual_mac(mac_str)
                    if is_virtual:
                        reasons.append(f"Virtual MAC address ({vendor})")

            # --- Aggregate checks ---
            if not has_hostname and not has_ip and not has_mac and not has_sn:
                reasons.append("All identifier fields null")

            if not has_hostname and not has_sn and not has_mac and has_ip:
                reasons.append("Only IP address (transient identifier)")

            if not has_hostname and not has_sn and (has_mac or has_ip):
                if "Only IP address (transient identifier)" not in reasons:
                    reasons.append("Missing hostname and serial number")

            if reasons:
                return "; ".join(reasons)
            else:
                return "Insufficient data quality"
        else:
            return "Components filtered during validation"

    def _determine_exclusion_category(self, exclusion_reason: str) -> str:
        """Determine the category of exclusion from the reason string."""
        reason_lower = exclusion_reason.lower()

        if "missing hostname" in reason_lower:
            return "insufficient_data"
        elif "hostname" in reason_lower and ("short" in reason_lower or "generic" in reason_lower or "date" in reason_lower or "no letters" in reason_lower or "placeholder pattern" in reason_lower or "manufacturer" in reason_lower or "format" in reason_lower):
            return "hostname_quality"
        elif "hostname excluded" in reason_lower:
            return "cross_reference_exclusion"
        elif "serial number" in reason_lower:
            return "sn_quality"
        elif "sn " in reason_lower and "excluded" in reason_lower:
            return "cross_reference_exclusion"
        elif "ip" in reason_lower and ("loopback" in reason_lower or "apipa" in reason_lower or "placeholder" in reason_lower or "failed validation" in reason_lower):
            return "ip_quality"
        elif "ip" in reason_lower and "transient" in reason_lower:
            return "transient_only"
        elif "ip excluded" in reason_lower:
            return "cross_reference_exclusion"
        elif "mac" in reason_lower and ("broadcast" in reason_lower or "zero" in reason_lower or "failed validation" in reason_lower or "virtual" in reason_lower):
            return "mac_quality"
        elif "mac excluded" in reason_lower and "shared" in reason_lower:
            return "cross_reference_exclusion"
        elif "mac excluded" in reason_lower:
            return "cross_reference_exclusion"
        elif "transient" in reason_lower:
            return "transient_only"
        elif "all identifier" in reason_lower:
            return "all_null"
        elif "missing" in reason_lower:
            return "insufficient_data"
        else:
            return "other"

    def _build_exclusion_details(self, row: pd.Series) -> Dict[str, Any]:
        """Build detailed exclusion information."""
        details = {}

        hostname = row.get('hostname')
        if pd.notna(hostname):
            hostname_str = str(hostname)
            details['hostname'] = {
                'value': hostname_str,
                'length': len(hostname_str),
                'is_valid': self.validator.is_valid_hostname(hostname_str),
                'is_generic': hostname_str.lower() in self.validator.generic_hostnames,
                'has_letters': bool(self.validator._hostname_has_letter_pattern.search(hostname_str)),
                'has_date_pattern': bool(self.validator._hostname_date_pattern.search(hostname_str)),
            }

        ip = row.get('ip')
        if pd.notna(ip):
            ip_str = str(ip)
            details['ip'] = {
                'value': ip_str,
                'is_valid': self.validator.is_valid_ip(ip_str),
                'is_loopback': ip_str.startswith('127.'),
                'is_apipa': ip_str.startswith('169.254.'),
            }

        mac = row.get('mac')
        if pd.notna(mac):
            mac_str = str(mac)
            is_virtual, vendor = self.validator.is_virtual_mac(mac_str)
            details['mac'] = {
                'value': mac_str,
                'is_valid': self.validator.is_valid_mac(mac_str),
                'is_virtual': is_virtual,
                'vendor': vendor,
            }

        sn = row.get('sn')
        if pd.notna(sn):
            sn_str = str(sn)
            details['sn'] = {
                'value': sn_str,
                'is_suspicious': self.validator.is_sn_suspicious(sn_str),
            }

        return details

    def _detect_conflicts(self, generation_run_id: UUID, providers_df: pd.DataFrame = None) -> Dict:
        """Detect business-level conflicts in asset data.

        Args:
            generation_run_id: ID of current generation run
            providers_df: Optional providers DataFrame with asset_id,
                         os_normalized, manufacturer, hostname columns

        Returns:
            Dictionary with conflict statistics
        """
        detector = ConflictDetector(generation_run_id)

        if providers_df is not None and not providers_df.empty:
            return detector.detect_conflicts(providers_df)

        return {'total_conflicts': 0, 'has_critical': False}

    def _expand_rows_by_ids(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Expand rows that have multiple IDs.

        Args:
            df: DataFrame with potentially multiple IDs per row

        Returns:
            Expanded DataFrame with one ID per row
        """
        logger.info("Expanding rows by IDs...")

        # Vectorized approach for single IDs
        mask_single = ~df["id"].astype(str).str.contains(",", na=False)
        single_id_rows = df[mask_single]

        # Process multiple IDs
        multi_id_rows = df[~mask_single]
        if not multi_id_rows.empty:
            expanded_rows = []
            for _, row in multi_id_rows.iterrows():
                ids = str(row["id"]).split(",")
                # Convert row to dict to avoid pandas indexing issues with dict values
                row_dict = row.to_dict()
                for id_val in ids:
                    new_row = row_dict.copy()
                    new_row["id"] = id_val.strip()
                    expanded_rows.append(new_row)

            multi_id_expanded = pd.DataFrame(expanded_rows)
            result = pd.concat([single_id_rows, multi_id_expanded], ignore_index=True)
        else:
            result = single_id_rows.reset_index(drop=True)

        logger.info(f"Expansion complete: {len(result)} rows")
        return result

    def _enrich_data(
        self,
        df: pd.DataFrame,
        mac_vendor_df: Optional[pd.DataFrame] = None,
        ipam_df: Optional[pd.DataFrame] = None
    ) -> pd.DataFrame:
        """
        Enrich asset data with vendor information and normalizations.

        Args:
            df: DataFrame to enrich
            mac_vendor_df: MAC vendor ranges
            ipam_df: IPAM networks

        Returns:
            Enriched DataFrame
        """
        logger.info("Enriching asset data...")

        enriched = df.copy()

        # MAC vendor enrichment
        if mac_vendor_df is not None and 'mac' in enriched.columns:
            enriched = self.enricher.enrich_mac_vendor(
                enriched, mac_vendor_df, mac_field='mac'
            )
        if 'mac_vendor_enriched' not in enriched.columns:
            enriched['mac_vendor_enriched'] = None

        # OS normalization
        if 'os' in enriched.columns:
            enriched = self.enricher.normalize_os(
                enriched, os_column='os', include_cpe=True
            )

        # OS EOL enrichment REMOVED - moved to post-aggregation (Stage 7.5)
        # Reason: EOL data must match the SELECTED OS, not per-row OS values
        # See: docs/issues/os-eol-alignment-issue.md

        # IPAM location matching
        if ipam_df is not None and 'ip' in enriched.columns:
            enriched = self.enricher.match_ipam_location(
                enriched, ipam_df, ip_column='ip'
            )

        logger.info("Data enrichment complete")
        return enriched

    def _aggregate_assets(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate asset data by ID.

        Args:
            df: Expanded DataFrame

        Returns:
            Aggregated DataFrame
        """
        logger.info("Aggregating assets by ID...")

        # Map predicted_category/predicted_subcategory to category_all/subcategory_all for aggregation
        # These will be aggregated into arrays showing all predictions
        # category_best/subcategory_best (added by CategorySelector) will be the single selected values
        cols_to_drop = []
        if 'predicted_category' in df.columns:
            df['predicted_category_agg'] = df['predicted_category']
            cols_to_drop.append('predicted_category')
            logger.info("Mapped predicted_category for aggregation")

        if 'predicted_subcategory' in df.columns:
            df['predicted_subcategory_agg'] = df['predicted_subcategory']
            cols_to_drop.append('predicted_subcategory')
            logger.info("Mapped predicted_subcategory for aggregation")

        # Also preserve metadata for CategorySelector
        if 'category_confidence' in df.columns:
            df['category_confidence_agg'] = df['category_confidence']
            cols_to_drop.append('category_confidence')
            logger.info("Mapped category_confidence for aggregation")

        if 'category_rule_type' in df.columns:
            df['category_rule_type_agg'] = df['category_rule_type']
            cols_to_drop.append('category_rule_type')
            logger.info("Mapped category_rule_type for aggregation")

        # Drop the original columns to avoid duplicates after post-aggregation prediction
        if cols_to_drop:
            df = df.drop(columns=cols_to_drop)
            logger.info(f"Dropped {len(cols_to_drop)} pre-aggregation prediction columns to prevent duplicates")

        # Pair extended_fields with data_source for proper aggregation
        # This allows us to know which data source each extended field came from
        if 'extended_fields' in df.columns and 'data_source' in df.columns:
            # Vectorized construction via zip instead of per-row apply
            paired = [
                {"__source__": ds if pd.notna(ds) else 'unknown', "__fields__": ef}
                if isinstance(ef, dict) and ef
                else None
                for ef, ds in zip(df['extended_fields'], df['data_source'])
            ]
            df['_extended_fields_paired'] = paired
            logger.info("Paired extended_fields with data_source for aggregation")

        def unique_non_empty_values(series: pd.Series) -> List:
            """
            Get unique non-empty values with case-insensitive deduplication.

            For string values, keeps the first occurrence when duplicates differ only by case.
            Example: ['hostname', 'HOSTNAME'] -> ['hostname']
            """
            # Filter out NA/None values — use .values for speed
            non_null = series.dropna()
            n = len(non_null)
            if n == 0:
                return []

            # Early exit for single value (most common case in aggregation)
            if n == 1:
                val = str(non_null.iloc[0]).strip()
                return [val] if val else []

            # Fast path: 2 values (second most common case)
            if n == 2:
                a = str(non_null.iloc[0]).strip()
                b = str(non_null.iloc[1]).strip()
                if not a and not b:
                    return []
                if not a:
                    return [b]
                if not b:
                    return [a]
                if a.lower() == b.lower():
                    return [a]
                return sorted([a, b], key=str.lower)

            # General case: filter empty strings and strip whitespace
            vals = [str(v).strip() for v in non_null.values]
            vals = [v for v in vals if v]

            if not vals:
                return []

            # Case-insensitive deduplication
            seen_lower = {}
            result = []
            for val in sorted(vals, key=str.lower):
                val_lower = val.lower()
                if val_lower not in seen_lower:
                    seen_lower[val_lower] = val
                    result.append(val)

            return result

        def aggregate_special_column(series: pd.Series) -> List:
            """Aggregate column with pipe separator."""
            all_values = []
            for val in series.dropna():
                all_values.extend(str(val).split('|'))
            return sorted(set(all_values))

        # Build aggregation functions
        # Exclude 'id' and handle special column types
        aggregatable_columns = []
        jsonb_columns = []  # Track JSONB columns (dicts) separately
        single_value_columns = []  # Columns that already have a single value selected

        for col in df.columns:
            if col == 'id':
                continue

            # Skip the paired column - we'll handle it specially
            if col == '_extended_fields_paired':
                continue

            # OS best columns are already single-valued, just take first
            # os is an array (all raw values), os_orig is single value (best raw value)
            if col in ['os_normalized_best', 'os_enrichment_best', 'os_best_source', 'os_orig_best', 'os_orig']:
                single_value_columns.append(col)
                continue

            # os array column - keep as array
            if col == 'os':
                single_value_columns.append(col)
                continue

            # Category best columns are already single-valued per asset (from CategorySelector)
            if col in ['category_best', 'subcategory_best', 'category_confidence_best',
                       'category_rule_type_best', 'category_best_source']:
                single_value_columns.append(col)
                continue

            # OS EOL columns - NO LONGER HANDLED HERE
            # Reason: EOL enrichment now happens post-aggregation, so these columns won't exist yet
            # They will be added in Stage 7.5 after aggregation completes
            # See: docs/issues/os-eol-alignment-issue.md
            if col in ['os_eol_status', 'os_eol_date', 'os_support_end_date',
                       'os_extended_support_date', 'os_is_lts', 'os_eol_checked_at']:
                # These columns should not exist at aggregation time anymore
                logger.warning(f"Found EOL column '{col}' during aggregation - should not exist!")
                continue

            # Detect column type by sampling first non-null value (#104)
            first_val = df[col].dropna().iloc[0] if df[col].notna().any() else None
            if first_val is None:
                aggregatable_columns.append(col)
                continue
            if isinstance(first_val, dict):
                jsonb_columns.append(col)
                continue
            if isinstance(first_val, list):
                logger.debug(f"Skipping list column: {col}")
                continue

            aggregatable_columns.append(col)

        # =====================================================================
        # Optimized aggregation: manual iteration with numpy group boundaries
        # Bypasses pandas groupby().agg() Python UDF overhead (~5-10x faster)
        # =====================================================================

        agg_start = time.time()

        # Sort by id for efficient boundary detection
        df_sorted = df.sort_values('id', kind='mergesort').reset_index(drop=True)
        ids_arr = df_sorted['id'].values
        unique_ids, group_starts = np.unique(ids_arr, return_index=True)
        group_ends = np.append(group_starts[1:], len(ids_arr))
        n_groups = len(unique_ids)

        logger.info(f"  Aggregation: {len(df_sorted)} rows → {n_groups} groups (sort+boundary: {time.time() - agg_start:.2f}s)")

        # Vectorized string preprocessing for regular columns (Approach C)
        # Convert to str+stripped upfront — eliminates per-group str()/strip() calls
        # Skip datetime columns — fillna('').astype(str) formats them differently than str()
        prep_start = time.time()
        special_cols = {'first_time', 'last_time', 'asset'}
        regular_agg_cols = [c for c in aggregatable_columns if c not in special_cols]

        str_prepped = {}  # Vectorized-prepped columns (safe for str preprocessing)
        raw_agg_cols = []  # Columns that need per-value str() (datetime, etc.)
        for col in regular_agg_cols:
            dtype = df_sorted[col].dtype
            if pd.api.types.is_datetime64_any_dtype(dtype) or dtype == 'object' and df_sorted[col].dropna().apply(type).eq(type(pd.Timestamp(0))).any():
                raw_agg_cols.append(col)
            else:
                str_prepped[col] = df_sorted[col].fillna('').astype(str).str.strip().values

        # Filter placeholder SNs from aggregation — same check used in UnionFind (#59 Item 12)
        if 'sn' in str_prepped:
            sn_arr = str_prepped['sn']
            filtered_count = 0
            for i in range(len(sn_arr)):
                if sn_arr[i] and self.validator.is_sn_suspicious(sn_arr[i]):
                    sn_arr[i] = ''
                    filtered_count += 1
            if filtered_count:
                logger.info(f"  Aggregation: filtered {filtered_count} suspicious SNs from asset arrays")

        logger.info(f"  Aggregation: vectorized string prep for {len(str_prepped)} cols, {len(raw_agg_cols)} raw cols ({time.time() - prep_start:.2f}s)")

        # Build result dict starting with id
        result_data = {'id': unique_ids}
        iter_start = time.time()

        # 1a. Pre-processed string columns — manual case-insensitive dedup
        for col in str_prepped:
            raw = str_prepped[col]
            col_result = [None] * n_groups
            for g in range(n_groups):
                s, e = group_starts[g], group_ends[g]
                if e - s == 1:
                    v = raw[s]
                    col_result[g] = [v] if v else []
                elif e - s == 2:
                    a, b = raw[s], raw[s + 1]
                    if not a and not b:
                        col_result[g] = []
                    elif not a:
                        col_result[g] = [b]
                    elif not b:
                        col_result[g] = [a]
                    elif a.lower() == b.lower():
                        col_result[g] = [a]
                    else:
                        col_result[g] = sorted([a, b], key=str.lower)
                else:
                    vals = [v for v in raw[s:e] if v]
                    if not vals:
                        col_result[g] = []
                    elif len(vals) == 1:
                        col_result[g] = vals
                    else:
                        seen = {}
                        deduped = []
                        for v in sorted(vals, key=str.lower):
                            vl = v.lower()
                            if vl not in seen:
                                seen[vl] = True
                                deduped.append(v)
                        col_result[g] = deduped
            result_data[col] = col_result

        # 1a-post. Normalize manufacturer names to canonical forms
        if 'manufacturer' in result_data:
            result_data['manufacturer'] = [
                normalize_manufacturer_list(m) for m in result_data['manufacturer']
            ]

        # 1a-post. Filter virtual/locally-administered MACs from asset records
        if 'mac' in result_data:
            result_data['mac'] = [
                filter_virtual_macs(m) for m in result_data['mac']
            ]

        # 1a-post. Filter loopback/APIPA/placeholder IPs from asset records —
        # the persisted-array counterpart of the merge-key IP hygiene; mirrors
        # the virtual-MAC filter above. (#681 D3 / GP-1)
        if 'ip' in result_data:
            result_data['ip'] = [
                self.validator.filter_invalid_ips(ips) for ips in result_data['ip']
            ]

        # 1b. Raw columns (datetime, etc.) — match unique_non_empty_values exactly:
        #     n==1/n==2: uses .iloc (→ pd.Timestamp str with +00:00)
        #     n>2: uses .values (→ numpy datetime64 str with T separator)
        for col in raw_agg_cols:
            raw_series = df_sorted[col]
            raw_np = raw_series.values  # numpy array for n>2 path
            col_result = [None] * n_groups
            for g in range(n_groups):
                s, e = group_starts[g], group_ends[g]
                n_rows = e - s
                # Count non-null values
                non_null_indices = [i for i in range(s, e) if pd.notna(raw_np[i])]
                n_non_null = len(non_null_indices)
                if n_non_null == 0:
                    col_result[g] = []
                elif n_non_null == 1:
                    # Match .iloc path: returns pd.Timestamp
                    v = str(raw_series.iat[non_null_indices[0]]).strip()
                    col_result[g] = [v] if v else []
                elif n_non_null == 2:
                    # Match .iloc path: returns pd.Timestamp
                    a = str(raw_series.iat[non_null_indices[0]]).strip()
                    b = str(raw_series.iat[non_null_indices[1]]).strip()
                    if not a and not b:
                        col_result[g] = []
                    elif not a:
                        col_result[g] = [b]
                    elif not b:
                        col_result[g] = [a]
                    elif a.lower() == b.lower():
                        col_result[g] = [a]
                    else:
                        col_result[g] = sorted([a, b], key=str.lower)
                else:
                    # Match .values path: returns numpy datetime64
                    vals = [str(raw_np[i]).strip() for i in non_null_indices]
                    vals = [v for v in vals if v]
                    if not vals:
                        col_result[g] = []
                    elif len(vals) == 1:
                        col_result[g] = vals
                    else:
                        seen = {}
                        deduped = []
                        for v in sorted(vals, key=str.lower):
                            vl = v.lower()
                            if vl not in seen:
                                seen[vl] = True
                                deduped.append(v)
                        col_result[g] = deduped
            result_data[col] = col_result

        # 2. 'asset' column — pipe-split dedup
        if 'asset' in aggregatable_columns:
            raw = df_sorted['asset'].fillna('').astype(str).str.strip().values
            col_result = [None] * n_groups
            for g in range(n_groups):
                all_vals = []
                for v in raw[group_starts[g]:group_ends[g]]:
                    if v:
                        all_vals.extend(v.split('|'))
                col_result[g] = sorted(set(all_vals)) if all_vals else []
            result_data['asset'] = col_result

        # 3. first_time / last_time — min/max via pandas Series to preserve tz
        for col, agg_name in [('first_time', 'min'), ('last_time', 'max')]:
            if col in aggregatable_columns:
                raw_series = df_sorted[col]
                col_result = [None] * n_groups
                for g in range(n_groups):
                    s = raw_series.iloc[group_starts[g]:group_ends[g]]
                    val = s.min() if agg_name == 'min' else s.max()
                    col_result[g] = [str(val)] if pd.notna(val) else []
                result_data[col] = col_result

        # 4. Single-value columns — take first non-NaN value per group
        for col in single_value_columns:
            raw = df_sorted[col].values
            col_result = [None] * n_groups
            for g in range(n_groups):
                for idx in range(group_starts[g], group_ends[g]):
                    v = raw[idx]
                    if pd.notna(v):
                        col_result[g] = v
                        break
            result_data[col] = col_result

        # 5. JSONB columns — merge dicts by data_source
        for col in jsonb_columns:
            raw = df_sorted[col].values
            col_result = [None] * n_groups
            for g in range(n_groups):
                merged = {}
                for record_dict in raw[group_starts[g]:group_ends[g]]:
                    if not isinstance(record_dict, dict) or not record_dict:
                        continue
                    for ds, fields in record_dict.items():
                        if not isinstance(fields, dict):
                            continue
                        if ds in merged:
                            merged[ds].update(fields)
                        else:
                            merged[ds] = fields.copy()
                col_result[g] = merged if merged else None
            result_data[col] = col_result

        # 6. Extended fields paired — merge by __source__
        if '_extended_fields_paired' in df_sorted.columns:
            raw = df_sorted['_extended_fields_paired'].values
            col_result = [None] * n_groups
            for g in range(n_groups):
                merged = {}
                for item in raw[group_starts[g]:group_ends[g]]:
                    if not isinstance(item, dict):
                        continue
                    ds = item.get('__source__')
                    fields = item.get('__fields__')
                    if not ds or not isinstance(fields, dict):
                        continue
                    if ds in merged:
                        merged[ds].update(fields)
                    else:
                        merged[ds] = fields.copy()
                col_result[g] = merged if merged else None
            result_data['_extended_fields_paired'] = col_result

        logger.info(f"  Aggregation: manual iteration ({time.time() - iter_start:.2f}s)")

        result = pd.DataFrame(result_data)

        logger.info(f"  Aggregation total: {time.time() - agg_start:.2f}s")

        # Add ip_primary: most recently seen IP for each asset (#49)
        if 'ip' in df.columns:
            ip_df = df[['id', 'ip']].copy()
            if 'last_time' in df.columns:
                ip_df['last_time'] = df['last_time']
                ip_df['last_time'] = pd.to_datetime(ip_df['last_time'], errors='coerce')
                # Drop rows with no IP or no timestamp
                ip_df = ip_df.dropna(subset=['ip', 'last_time'])
                ip_df = ip_df[ip_df['ip'].astype(str).str.strip() != '']
                # #681 D3: ip_primary is a persisted scalar (assets.ip_primary);
                # apply the same loopback/APIPA/placeholder hygiene as the ip[]
                # array so the primary IP can't be an invalid address the array
                # excludes. Selecting from valid rows yields the latest *valid*
                # IP; an asset whose IPs are all invalid drops out -> ip_primary
                # stays NaN -> coerced to None below, matching the empty ip[].
                ip_df = ip_df[ip_df['ip'].apply(self.validator.is_valid_ip)]
                if not ip_df.empty:
                    # Get the row with the latest time for each asset
                    idx = ip_df.groupby('id')['last_time'].idxmax()
                    idx = idx.dropna()  # Remove NaN indices (groups with all-NaN timestamps)
                    if not idx.empty:
                        ip_primary = ip_df.loc[idx, ['id', 'ip']].rename(columns={'ip': 'ip_primary'})
                        result = result.merge(ip_primary, on='id', how='left')
            else:
                # Fallback: use last non-empty IP per group
                ip_df = ip_df.dropna(subset=['ip'])
                ip_df = ip_df[ip_df['ip'].astype(str).str.strip() != '']
                # #681 D3: same ip_primary hygiene as the last_time branch above.
                ip_df = ip_df[ip_df['ip'].apply(self.validator.is_valid_ip)]
                if not ip_df.empty:
                    ip_primary = ip_df.groupby('id')['ip'].last().reset_index().rename(columns={'ip': 'ip_primary'})
                    result = result.merge(ip_primary, on='id', how='left')

        # Replace NaN in ip_primary with None to avoid invalid JSON serialization
        if 'ip_primary' in result.columns:
            result['ip_primary'] = result['ip_primary'].where(result['ip_primary'].notna(), None)

        # Rename _extended_fields_paired back to extended_fields
        if '_extended_fields_paired' in result.columns:
            # Drop the old aggregated extended_fields column if it exists to avoid duplication
            if 'extended_fields' in result.columns:
                result = result.drop(columns=['extended_fields'])
                logger.info("Dropped old aggregated extended_fields column before rename")

            result = result.rename(columns={'_extended_fields_paired': 'extended_fields'})
            logger.info("Renamed aggregated extended_fields_paired to extended_fields")

        # Rename category aggregated columns to names expected by CategorySelector
        category_renames = {
            'predicted_category_agg': 'predicted_category',
            'predicted_subcategory_agg': 'predicted_subcategory',
            'category_confidence_agg': 'category_confidence',
            'category_rule_type_agg': 'category_rule_type'
        }
        category_renames = {k: v for k, v in category_renames.items() if k in result.columns}
        if category_renames:
            result = result.rename(columns=category_renames)
            logger.info(f"Renamed {len(category_renames)} category metadata columns for CategorySelector")

        logger.info(f"Aggregation complete: {len(result)} unique assets")

        # Note: EOL columns are NOT present here - they are added in Stage 8.5 (post-aggregation)
        # This ensures EOL data matches the selected OS from Stage 6.7

        # Convert array columns to scalars for fields that should be single-valued
        result = self._convert_arrays_to_scalars(result)

        return result

    def _convert_arrays_to_scalars(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Unwrap aggregation's 1-element timestamp arrays back to scalars.

        `_aggregate_assets` runs min/max on `first_time`/`last_time` and stores
        the result as a 1-element list (e.g. `['2024-01-01 00:00:00+00:00']`)
        for shape consistency with the array columns. The `assets` table stores
        these as `timestamp with time zone`, not arrays, so we extract the
        single element here before persistence.
        """
        def unwrap(arr):
            if not isinstance(arr, list) or len(arr) == 0:
                return None
            valid = [x for x in arr if x]
            return valid[0] if valid else None

        for field in ('first_time', 'last_time'):
            if field in df.columns:
                df[field] = df[field].apply(unwrap)

        return df

    def _calculate_scores(
        self,
        assets_df: pd.DataFrame,
        providers_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Calculate confidence scores.

        Args:
            assets_df: Aggregated assets
            providers_df: Provider data for confidence calculation

        Returns:
            Scored DataFrame
        """
        logger.info("Calculating confidence scores...")

        # Load integration group mapping for scorer pre-processing
        group_mapping = {}
        try:
            from src.modules.integrations.repositories.integration_group_repository import IntegrationGroupRepository
            group_repo = IntegrationGroupRepository(self.db)
            group_mapping = group_repo.get_group_mapping()
            if group_mapping:
                logger.info(f"Loaded {len(group_mapping)} integration-to-group mappings for scoring")
        except Exception as e:
            logger.warning(f"Could not load integration groups for scoring: {e}")

        scored = self.scorer.calculate_confidence(assets_df, providers_df, group_mapping=group_mapping)

        logger.info("Scoring complete")
        return scored

    def _populate_lifecycle_metadata(
        self,
        scored_df: pd.DataFrame,
        generation_run_id: UUID
    ) -> pd.DataFrame:
        """
        Populate lifecycle tracking columns before saving assets.

        For each asset:
        - first_seen: MIN(historical first_seen, source first_time) — reflects earliest observation
        - last_seen: Source last_time (most recent observation in source data)
        - last_generation_run_id: Set to current generation_run_id

        Falls back to NOW() when source timestamps are missing or NaN.

        Args:
            scored_df: DataFrame with scored assets
            generation_run_id: UUID of current generation run

        Returns:
            DataFrame with lifecycle columns populated
        """
        from datetime import datetime

        logger.info("Loading first_seen dates from history for lifecycle tracking...")

        # Load first_seen dates for all assets that ever existed
        first_seen_map = self.asset_repo.get_first_seen_dates()

        logger.info(f"Found {len(first_seen_map)} assets with historical first_seen dates")

        # Current timestamp for first_seen fallback
        current_time = datetime.now(timezone.utc)
        current_time_ts = pd.Timestamp(current_time)

        # --- last_seen: derived from last_time (max timestamp across providers) ---
        # last_time flows through: integration field_mapper → enricher → aggregation(max)
        # → _convert_arrays_to_scalars → here. If no integration maps last_time, values
        # will be NaT, and the scorer applies a 0.95 unknown-freshness penalty.
        if 'last_time' in scored_df.columns:
            # format='ISO8601' handles mixed precision (with/without microseconds)
            # from str(Timestamp) — plain errors='coerce' infers a fixed format from
            # the first value and silently drops timestamps whose precision differs.
            scored_df['last_seen'] = pd.to_datetime(
                scored_df['last_time'], errors='coerce', utc=True, format='ISO8601'
            )
        else:
            scored_df['last_seen'] = pd.NaT

        # --- first_seen: vectorized ---
        asset_ids = scored_df['id'].astype(str)

        # Map historical first_seen dates and parse vectorized
        historical_series = asset_ids.map(first_seen_map)
        historical_parsed = pd.to_datetime(historical_series, errors='coerce', utc=True)

        # Parse source first_time vectorized
        if 'first_time' in scored_df.columns:
            source_first_parsed = pd.to_datetime(
                scored_df['first_time'], errors='coerce', utc=True, format='ISO8601'
            )
        else:
            source_first_parsed = pd.Series(pd.NaT, index=scored_df.index)

        has_history = historical_parsed.notna()
        has_source_first = source_first_parsed.notna()

        # Start with current_time as default for all rows
        first_seen = pd.Series(current_time_ts, index=scored_df.index)

        # New assets (no history): use source first_time if available
        new_mask = ~has_history & has_source_first
        first_seen[new_mask] = source_first_parsed[new_mask]

        # History-only assets: use historical value (already parsed, fallback is current_time default)
        history_only_mask = has_history & ~has_source_first
        first_seen[history_only_mask] = historical_parsed[history_only_mask]

        # Both history and source first_time: take element-wise minimum
        both_mask = has_history & has_source_first
        if both_mask.any():
            hist_vals = historical_parsed[both_mask]
            src_vals = source_first_parsed[both_mask]
            first_seen[both_mask] = pd.concat([hist_vals, src_vals], axis=1).min(axis=1)

        scored_df['first_seen'] = first_seen

        # --- last_generation_run_id: single scalar assignment ---
        scored_df['last_generation_run_id'] = str(generation_run_id)

        # Log statistics
        historical_ids = set(first_seen_map.keys())
        existing_assets = asset_ids.isin(historical_ids).sum()
        new_assets = len(scored_df) - existing_assets

        logger.info(f"Lifecycle metadata populated: {existing_assets} existing assets, {new_assets} new assets")

        return scored_df

    def _track_changes(self, old_assets_df: pd.DataFrame, new_assets_df: pd.DataFrame, generation_run_id: uuid4, providers_df=None, historical_ids=None, merge_pairs=None) -> Dict[str, Any]:
        """
        Track changes between current and new asset states.

        Args:
            old_assets_df: Previous asset state (before save)
            new_assets_df: New asset state (after processing)
            generation_run_id: UUID of this pipeline run
            providers_df: Optional provider DataFrame for source attribution
            historical_ids: Pre-loaded historical asset IDs for re-add detection.
                If None, loads from DB (fallback for direct callers).
            merge_pairs: Mapping of {removed_asset_id: surviving_asset_id} from pipeline
                merge resolution. Links removal and update events with shared operation_id.

        Returns:
            Chronicle statistics
        """
        logger.info("Generating chronicle events...")

        # Filter out invalid assets (with NA or NaN IDs) before processing
        # Reason: Prevents chronicle from attempting to process invalid data
        invalid_count_old = 0
        invalid_count_new = 0

        if not old_assets_df.empty:
            invalid_count_old = old_assets_df['id'].isna().sum()
            old_assets_df = old_assets_df[old_assets_df['id'].notna()].copy()

        if not new_assets_df.empty:
            invalid_count_new = new_assets_df['id'].isna().sum()
            new_assets_df = new_assets_df[new_assets_df['id'].notna()].copy()

        if invalid_count_old > 0 or invalid_count_new > 0:
            logger.warning(
                f"Filtered {invalid_count_old} invalid assets from old state, "
                f"{invalid_count_new} from new state before chronicle processing"
            )

        # Convert to dictionaries for comparison, pre-stripping excluded fields
        # This avoids checking excluded_fields per-key-per-asset during comparison
        excluded = self.chronicle_service.excluded_fields
        if not old_assets_df.empty:
            # Normalize IDs to strings — psycopg2 returns uuid.UUID objects,
            # but scored_df uses strings. Without this, set comparison in
            # compare_and_generate_events() finds zero overlap (UUID != str).
            old_assets_df['id'] = old_assets_df['id'].astype(str)
            keep_cols = [c for c in old_assets_df.columns if c not in excluded and c != 'id']
            current_assets = old_assets_df.set_index('id')[keep_cols].to_dict('index')
        else:
            current_assets = {}
        keep_cols_new = [c for c in new_assets_df.columns if c not in excluded and c != 'id']
        new_assets = new_assets_df.set_index('id')[keep_cols_new].to_dict('index')

        # Log comparison stats for performance monitoring
        current_id_set = set(current_assets.keys())
        new_id_set = set(new_assets.keys())
        logger.info(
            f"Chronicle comparison: {len(current_assets)} old, {len(new_assets)} new, "
            f"{len(current_id_set & new_id_set)} to compare, "
            f"{len(new_id_set - current_id_set)} created, "
            f"{len(current_id_set - new_id_set)} removed"
        )

        if current_id_set == new_id_set and not merge_pairs:
            logger.info(
                "Chronicle: same %d asset IDs in old and new — "
                "checking attribute changes only (no creations/removals)",
                len(current_id_set)
            )

        # Get list of existing asset IDs from history (for re-add detection)
        # This includes assets that previously existed but were removed
        if historical_ids is not None:
            existing_ids = historical_ids
        else:
            existing_ids = self.asset_repo.get_all_historical_asset_ids()
        logger.info(f"Loaded {len(existing_ids)} historical asset IDs for re-add detection")

        # Build provider map for source attribution (optional)
        provider_map = None
        if providers_df is not None and not providers_df.empty:
            provider_map = self._build_provider_map(providers_df)
            logger.info(f"Built provider map for {len(provider_map)} assets")

        # Generate events, count by type, then write
        events = self.chronicle_service.compare_and_generate_events(
            current_assets, new_assets, existing_ids,
            generation_run_id=str(generation_run_id),
            provider_map=provider_map,
            merge_pairs=merge_pairs
        )

        # Count events by type. Track pipeline_merge removals separately so the
        # run summary can distinguish identity consolidations (an asset absorbed
        # into another) from genuine removals — the homepage butterfly already
        # excludes them on the same basis.
        event_type_counts = {}
        merged_assets = 0
        for event in events:
            et = event.get('event_type', 'unknown')
            event_type_counts[et] = event_type_counts.get(et, 0) + 1
            if (et == 'asset_removed'
                    and event.get('causation') == PIPELINE_MERGE_CAUSATION):
                merged_assets += 1

        events_written = self.chronicle_service.write_events(events)

        return {
            'events_written': events_written,
            'current_asset_count': len(current_assets),
            'new_asset_count': len(new_assets),
            'event_type_counts': event_type_counts,
            'merged_assets': merged_assets,
        }

    def _build_provider_map(self, providers_df) -> Dict[str, Dict[str, str]]:
        """
        Build {asset_id: {attribute: source_name}} map from providers DataFrame.

        Used for source attribution in chronicle events.
        """
        tracked_attrs = ['hostname', 'ip', 'mac', 'sn', 'os', 'fqdn', 'model', 'manufacturer']

        if providers_df.empty:
            return {}

        # Determine column names (schema varies between callers)
        id_col = 'asset_id' if 'asset_id' in providers_df.columns else 'id'
        source_col = 'data_source' if 'data_source' in providers_df.columns else 'source_name'

        if id_col not in providers_df.columns:
            return {}

        # Ensure string asset IDs and drop blanks
        df = providers_df.copy()
        df[id_col] = df[id_col].astype(str)
        df = df[df[id_col].ne('') & df[id_col].ne('None') & df[id_col].ne('nan')]

        if df.empty:
            return {}

        # Fill missing source column
        if source_col not in df.columns:
            df[source_col] = 'Unknown'
        else:
            df[source_col] = df[source_col].fillna('Unknown')

        # Only keep columns we need
        keep_cols = [id_col, source_col] + [a for a in tracked_attrs if a in df.columns]
        df = df[keep_cols]

        # Vectorized approach: melt tracked attrs → groupby (asset, attr) → aggregate sources
        available_attrs = [a for a in tracked_attrs if a in df.columns]
        if not available_attrs:
            return {}

        provider_map = {}
        for attr in available_attrs:
            # Filter to rows where this attr is non-null
            mask = df[attr].notna()
            if not mask.any():
                continue
            # Group by asset_id to get unique sources per asset for this attr
            attr_sources = (
                df.loc[mask, [id_col, source_col]]
                .groupby(id_col, sort=False)[source_col]
                .apply(lambda x: ', '.join(x.unique()))
            )
            for asset_id, source_str in attr_sources.items():
                if asset_id not in provider_map:
                    provider_map[asset_id] = {}
                provider_map[asset_id][attr] = source_str

        return provider_map

    # =========================================================================
    # ENHANCEMENT METHODS
    # =========================================================================

    def _predict_categories_rule_based(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Predict categories and subcategories using rule-based patterns.

        This runs after OS normalization and MAC vendor enrichment, using:
        - os_normalized
        - manufacturer
        - model
        - mac_vendor_enriched

        Fallback: category="miscellaneous", subcategory="unknown"

        Args:
            df: DataFrame with enriched asset data

        Returns:
            DataFrame with prediction columns added:
            - predicted_category
            - predicted_subcategory
            - category_confidence
            - category_rule_type
        """
        logger.info("Running rule-based category/subcategory prediction...")

        df = self.category_predictor.predict_categories(
            df,
            os_column='os_normalized',
            manufacturer_column='manufacturer',
            model_column='model',
            mac_vendor_column='mac_vendor_enriched',
            hostname_column='hostname'
        )

        logger.info("Rule-based predictions complete")
        return df

    def _predict_categories_ml(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Predict categories and subcategories using ML models.

        Args:
            df: DataFrame with enriched asset data

        Returns:
            DataFrame with predictions added
        """
        logger.info("Running ML category/subcategory prediction...")

        # Predict category
        df = self.catboost_predictor.predict_category(
            df,
            category_column='category',
            score_column='category_prediction_score'
        )

        # Predict subcategory
        df = self.catboost_predictor.predict_subcategory(
            df,
            subcategory_column='subcategory',
            score_column='subcategory_prediction_score'
        )

        logger.info("ML predictions complete")
        return df

    def _select_best_categories(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Select best category and subcategory based on weighted scores.

        Args:
            df: Aggregated DataFrame with predictions

        Returns:
            DataFrame with _best columns added
        """
        logger.info("Selecting best categories from predictions...")

        # Select best category
        df = self.catboost_predictor.select_best_category(
            df,
            id_field='id',
            category_field='category',
            score_field='category_prediction_score'
        )

        # Select best subcategory
        df = self.catboost_predictor.select_best_category(
            df,
            id_field='id',
            category_field='subcategory',
            score_field='subcategory_prediction_score'
        )

        logger.info("Best category selection complete")
        return df

    def _apply_tiered_fallback(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply Tier 2 (KB) and Tier 3 (CatBoost) on unresolved assets.

        Only touches assets where rules returned fallback or low confidence.
        Resolved assets are never modified (no regression guarantee).
        """
        if df.empty:
            return df

        # Find unresolved assets
        mask = (
            (df['category_best'].isin(['miscellaneous', 'unknown'])) |
            (df['category_confidence_best'] < self.rule_confidence_threshold)
        )
        unresolved_indices = df.index[mask]
        if len(unresolved_indices) == 0:
            return df

        resolved_by_kb = set()

        # Tier 2: Knowledge base lookup
        if self.enable_knowledge_base and self.knowledge_base:
            for idx in unresolved_indices:
                row = df.loc[idx]
                result = self.knowledge_base.lookup(
                    extract_scalar(row.get('manufacturer', '')),
                    extract_scalar(row.get('model', '')),
                    extract_scalar(row.get('mac_vendor', ''))
                )
                if result:
                    cat, subcat, conf = result
                    df.at[idx, 'category_best'] = cat
                    df.at[idx, 'subcategory_best'] = subcat
                    df.at[idx, 'category_confidence_best'] = conf
                    df.at[idx, 'category_rule_type_best'] = 'knowledge_base'
                    resolved_by_kb.add(idx)

        # Tier 3: CatBoost (vectorized write-back)
        still_unresolved = [i for i in unresolved_indices if i not in resolved_by_kb]
        if self.enable_ml_prediction and self.catboost_predictor and len(still_unresolved) > 0:
            unresolved_df = df.loc[still_unresolved]
            ml_results = self.catboost_predictor.predict_batch(unresolved_df)
            accepted = ml_results['ml_confidence'] >= self.ml_confidence_threshold
            accepted_idx = ml_results.index[accepted]
            if len(accepted_idx) > 0:
                df.loc[accepted_idx, 'category_best'] = ml_results.loc[accepted_idx, 'ml_category']
                df.loc[accepted_idx, 'subcategory_best'] = ml_results.loc[accepted_idx, 'ml_subcategory']
                df.loc[accepted_idx, 'category_confidence_best'] = ml_results.loc[accepted_idx, 'ml_confidence']
                df.loc[accepted_idx, 'category_rule_type_best'] = 'ml_catboost'

        return df

    def _apply_category_overrides(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply manual category overrides.

        Args:
            df: DataFrame with assets

        Returns:
            DataFrame with overrides applied
        """
        logger.info("Applying category overrides...")

        # Load overrides from database
        overrides = self.category_override_repo.get_all_overrides()

        # Apply overrides
        df = self.category_override_repo.apply_overrides(
            df,
            overrides,
            category_column='category_best' if 'category_best' in df.columns else 'category'
        )

        logger.info("Category overrides applied")
        return df

    @staticmethod
    def _disambiguate_ios_os(df: pd.DataFrame) -> pd.DataFrame:
        """
        Disambiguate bare 'ios X.Y' OS values using manufacturer context.

        Runs post-aggregation where manufacturer[] is available.
        Prefixes with 'apple' or 'cisco' based on manufacturer match.

        Args:
            df: Aggregated assets DataFrame with os_normalized and manufacturer columns

        Returns:
            DataFrame with disambiguated os_normalized values
        """
        if 'os_normalized' not in df.columns or 'manufacturer' not in df.columns:
            return df

        mask = df['os_normalized'].notna() & (
            df['os_normalized'].str.startswith('ios ', na=False)
            | (df['os_normalized'] == 'ios')
        )
        # Exclude already-disambiguated values
        mask = mask & ~df['os_normalized'].str.startswith('ios xe', na=False)
        mask = mask & ~df['os_normalized'].str.startswith('cisco ios', na=False)
        mask = mask & ~df['os_normalized'].str.startswith('apple ios', na=False)

        if not mask.any():
            return df

        def _get_prefix(mfr_list):
            if not mfr_list or (isinstance(mfr_list, float) and pd.isna(mfr_list)):
                return ''
            if isinstance(mfr_list, str):
                mfr_list = [mfr_list]
            normalized = [normalize_manufacturer(str(m).lower()) for m in mfr_list if pd.notna(m)]
            if 'Apple' in normalized:
                return 'apple '
            if 'Cisco' in normalized:
                return 'cisco '
            return ''

        prefixes = df.loc[mask, 'manufacturer'].apply(_get_prefix)
        non_empty = prefixes != ''
        if non_empty.any():
            df.loc[prefixes.index[non_empty], 'os_normalized'] = (
                prefixes[non_empty] + df.loc[prefixes.index[non_empty], 'os_normalized']
            )

        return df

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def get_pipeline_stats(self) -> Dict[str, Any]:
        """
        Get statistics from all components.

        Returns:
            Dictionary with component statistics
        """
        return {
            'identifier': self.identifier.get_stats(),
            'validator': {
                'sn_overuse_threshold': self.validator.sn_overuse_threshold,
                'sn_migration_threshold': self.validator.sn_migration_threshold
            },
            'enricher': self.enricher.get_enrichment_stats(),
            'scorer': self.scorer.get_scoring_stats(),
            'chronicle': self.chronicle_service.get_stats()
        }

    def clear_caches(self) -> None:
        """Clear all internal caches."""
        self.identifier.clear_cache()
        self.validator.clear_cache()
        self.enricher.clear_caches()
        logger.info("All caches cleared")
