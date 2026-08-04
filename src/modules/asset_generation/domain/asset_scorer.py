# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Scorer V2 - Advanced confidence scoring with gates and gradients.

This module implements an improved confidence scoring algorithm that separates
confidence factors into GRADIENTS (weighted contributions) and GATES (caps/penalties).

Key improvements over V1:
- Consistency penalties are severe (not just weighted averages)
- Identifier quality sets hard caps on max confidence
- Diminishing returns for source count (sqrt function)
- Recency penalties for stale data
- Weighted data completeness (critical fields > nice-to-have)
- Cross-validation bonuses for multiple authoritative sources

Algorithm: confidence = BASE_SCORE × consistency_penalty × recency_penalty × identifier_cap

Where:
- BASE_SCORE = 40% source_reliability + 30% source_count + 30% data_completeness
- Source count is normalized per asset category (e.g. mobile sources ≠ workstation sources)
- Consistency penalty = multiplier based on field disagreements (0.3-1.0)
- Recency penalty = multiplier based on data age (0.4-1.0)
- Identifier cap = maximum confidence based on identifier strength (0.3-1.0)
"""

import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class AssetScorer:
    """
    Advanced confidence scorer using gates and gradients approach.

    Philosophy:
    - Some factors are GRADIENTS: they contribute to base score with weights
    - Some factors are GATES: they cap or multiply the final score
    - This creates more realistic confidence ranges and severe penalties for issues
    """

    # Source reliability scores by data source category
    DEFAULT_SOURCE_SCORES = {
        # Tier 1: On-device agents (highest authority)
        'endpoint security': 0.95,                  # Defender, CrowdStrike, SentinelOne

        # Tier 2: Infrastructure truth
        'cloud platform': 0.90,                     # Azure, AWS, GCP resource APIs
        'mobile device management': 0.85,           # Intune, Jamf, Workspace ONE

        # Tier 3: Network, identity & control planes
        'network configuration management': 0.80,   # Panorama, FortiManager, Cisco DNA, SCCM, Ansible Tower
        'network traffic': 0.75,                    # NetFlow, Darktrace, ExtraHop
        'identity and access management': 0.75,     # Active Directory, Entra ID

        # Tier 4: Scanning & observational
        'vulnerability management': 0.70,           # Qualys, Tenable, Rapid7
        'observability platform': 0.70,             # Log Analytics, Splunk, Datadog, Elastic
        'telemetry derived': 0.70,                  # SIEM/log-scraped inventory (honest provenance label)

        # Tier 5: Inventory & discovery
        'cmdb': 0.65,                               # ServiceNow CMDB, Device42, Flexera
        'asset discovery': 0.60,                    # Armis, Lansweeper, Rumble

        # Fallback
        'other': 0.50,                              # Uncategorized sources
    }

    # Field importance tiers for completeness calculation
    FIELD_TIERS = {
        'tier1': ['sn', 'hostname', 'fqdn', 'mac'],  # 3 points each
        'tier2': ['ip', 'os_normalized', 'manufacturer', 'model'],  # 2 points each
        'tier3': ['location', 'owner', 'category', 'os_eol_date', 'vendor_category'],  # 1 point each
    }

    # Consistency check field severity (lower = harsher penalty)
    CONSISTENCY_SEVERITY = {
        'sn': 0.30,           # Critical: 70% penalty if disagree
        'hostname': 0.60,     # Major: 40% penalty
        'fqdn': 0.65,         # Major: 35% penalty
        'mac': 0.95,          # Minor: 5% penalty (multi-NIC, wired+wireless)
        'os_normalized': 0.80,  # Moderate: 20% penalty
        'category': 0.90,     # Minor: 10% penalty
        'ip': 0.95,           # Minor: 5% penalty (IPs change)
    }

    # Source-count-aware consistency floor (#279)
    CONSISTENCY_FLOOR_MAX = 0.50        # Multiplier never drops below this for 5+ sources
    CONSISTENCY_FLOOR_PER_SOURCE = 0.10  # Floor increment per unique source

    # Base score weights: {component: weight} — must sum to 1.0 (#286)
    WEIGHTS_STANDARD = {
        'source_reliability': 0.40,
        'source_count': 0.30,
        'data_completeness': 0.30,
    }
    WEIGHTS_SINGLE_SOURCE = {
        'source_reliability': 0.40,
        'source_count': 0.15,
        'data_completeness': 0.45,
    }

    # Default calibration curve: stretches the compressed raw distribution
    # (typically 0.2-0.8) into an intuitive 0-1 range.  Monotonic piecewise-linear.
    DEFAULT_CALIBRATION_CURVE = {
        0.0: 0.05,
        0.2: 0.15,
        0.3: 0.30,
        0.4: 0.45,
        0.5: 0.60,
        0.6: 0.72,
        0.7: 0.82,
        0.8: 0.90,
        0.9: 0.95,
        1.0: 1.00,
    }

    # Low-quality field values that shouldn't count as "has data"
    LOW_QUALITY_VALUES = {
        'hostname': {'localhost', 'computer', 'pc', 'unknown', 'none', 'desktop', 'laptop'},
        'ip': {'0.0.0.0', '127.0.0.1', '255.255.255.255', '::1'},
        'mac': {'00:00:00:00:00:00', 'ff:ff:ff:ff:ff:ff'},
        'sn': {'unknown', 'none', 'n/a', 'na', 'default string', 'to be filled by o.e.m.', 'system serial number'},
        'model': {'virtual machine', 'standard pc', 'system product name', 'to be filled by o.e.m.',
                  'unknown', 'none', 'n/a', 'default string', 'virtual', 'vmware virtual platform',
                  'bochs', 'kvm', 'qemu standard pc'},
    }

    def __init__(
        self,
        source_reliability_scores: Dict[str, float] = None,
        enable_recency_penalty: bool = True,
        enable_identifier_cap: bool = True,
        enable_consistency_penalty: bool = True,
        calibration_curve: Optional[Dict[float, float]] = None,
        enable_calibration: bool = True
    ):
        """
        Initialize V2 scorer.

        Args:
            source_reliability_scores: Custom reliability scores per data source category
            enable_recency_penalty: Whether to apply recency-based penalty
            enable_identifier_cap: Whether to cap confidence by identifier quality
            enable_consistency_penalty: Whether to penalize field disagreements
            calibration_curve: Optional mapping of raw confidence bucket (0.0-1.0)
                to calibrated confidence. Overrides the default curve.
            enable_calibration: Whether to apply calibration (default: True).
                Set to False to get raw multiplicative scores.
        """
        self.source_reliability_scores = source_reliability_scores or self.DEFAULT_SOURCE_SCORES
        self.enable_recency = enable_recency_penalty
        self.enable_identifier_cap = enable_identifier_cap
        self.enable_consistency = enable_consistency_penalty
        if enable_calibration:
            self.calibration_curve = calibration_curve or self.DEFAULT_CALIBRATION_CURVE
        else:
            self.calibration_curve = None

        if self.calibration_curve:
            sorted_keys = sorted(self.calibration_curve.keys())
            self._cal_xp = sorted_keys
            self._cal_fp = [self.calibration_curve[k] for k in sorted_keys]
        else:
            self._cal_xp = []
            self._cal_fp = []

        logger.info(
            f"AssetScorer initialized (recency={enable_recency_penalty}, "
            f"id_cap={enable_identifier_cap}, consistency={enable_consistency_penalty})"
        )

    # =========================================================================
    # MAIN CONFIDENCE CALCULATION
    # =========================================================================

    # =========================================================================
    # ENVIRONMENT STATS - Per-category source normalization
    # =========================================================================

    def _collapse_groups(
        self,
        assets_df: pd.DataFrame,
        group_mapping: Dict[str, str]
    ) -> pd.DataFrame:
        """
        Replace grouped integration names with their group name and deduplicate.

        For each asset row:
        - data_source: ['Entra 1', 'Entra 2', 'CrowdStrike'] -> ['All Entra', 'CrowdStrike']
        - data_source_category: deduplicates accordingly (all Entra share same category)

        Args:
            assets_df: DataFrame with data_source and data_source_category list columns
            group_mapping: {integration_name: group_name} for all grouped integrations

        Returns:
            DataFrame with collapsed columns (copy, not in-place)
        """
        if not group_mapping:
            return assets_df.copy()

        df = assets_df.copy()

        new_sources = []
        new_categories = []

        for _, row in df.iterrows():
            sources = row.get('data_source', [])
            categories = row.get('data_source_category', [])

            # Ensure list types
            if not isinstance(sources, list):
                sources = [sources] if sources is not None and pd.notna(sources) else []
            if not isinstance(categories, list):
                categories = [categories] if categories is not None and pd.notna(categories) else []

            # Pad the shorter list so zip doesn't silently drop entries
            max_len = max(len(sources), len(categories))
            if len(sources) < max_len:
                sources = sources + [None] * (max_len - len(sources))
            if len(categories) < max_len:
                categories = categories + [None] * (max_len - len(categories))

            seen = set()
            collapsed_src = []
            collapsed_cat = []

            for src, cat in zip(sources, categories):
                resolved = group_mapping.get(src, src) if src is not None else src
                if resolved in seen:
                    continue
                seen.add(resolved)
                collapsed_src.append(resolved)
                collapsed_cat.append(cat)

            new_sources.append(collapsed_src)
            new_categories.append(collapsed_cat)

        df['data_source'] = new_sources
        df['data_source_category'] = new_categories

        logger.info(
            f"Group collapse applied: {len(group_mapping)} member mappings across "
            f"{len(set(group_mapping.values()))} groups"
        )

        return df

    def _compute_environment_stats(self, assets_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Derive per-category source availability from the dataset.

        Scans all assets to build a map of which data sources and source
        categories actually report each asset category. This allows
        normalization against what's possible in the environment rather
        than a hardcoded ideal.

        Args:
            assets_df: Full DataFrame of assets being scored

        Returns:
            Dict with:
                sources_per_category: {asset_cat: num_distinct_sources}
                source_cats_per_category: {asset_cat: num_distinct_source_categories}
                auth_cats_per_category: {asset_cat: num_distinct_authoritative_categories}
                global_sources: total distinct sources across all categories
                global_source_cats: total distinct source categories
        """
        authoritative = {
            'endpoint security', 'cloud platform',
            'mobile device management', 'mdm', 'virtualization'
        }

        sources_per_cat: Dict[str, set] = {}
        source_cats_per_cat: Dict[str, set] = {}
        auth_cats_per_cat: Dict[str, set] = {}
        source_counts_per_cat: Dict[str, List[int]] = {}  # per-asset source counts
        all_sources: set = set()
        all_source_cats: set = set()

        # Use column-based iteration instead of iterrows for better performance
        # Prefer category_best (scalar, from CategorySelector) over category (array)
        has_category_best = 'category_best' in assets_df.columns
        cat_col = assets_df['category_best'] if has_category_best else assets_df.get('category')
        ds_col = assets_df.get('data_source')
        dsc_col = assets_df.get('data_source_category')

        # Iterate via zip over raw column values (avoids Series construction per row)
        cat_values = cat_col.values if cat_col is not None else [None] * len(assets_df)
        ds_values = ds_col.values if ds_col is not None else [None] * len(assets_df)
        dsc_values = dsc_col.values if dsc_col is not None else [None] * len(assets_df)

        for raw_cat, data_sources, data_source_cats in zip(cat_values, ds_values, dsc_values):
            category = self._safe_scalar(raw_cat, 'unknown').lower()

            if not isinstance(data_sources, list):
                data_sources = [data_sources] if data_sources is not None and pd.notna(data_sources) else []

            if not isinstance(data_source_cats, list):
                data_source_cats = [data_source_cats] if data_source_cats is not None and pd.notna(data_source_cats) else []

            if category not in sources_per_cat:
                sources_per_cat[category] = set()
                source_cats_per_cat[category] = set()
                auth_cats_per_cat[category] = set()
                source_counts_per_cat[category] = []

            # Track how many sources this individual asset has
            valid_sources = [s for s in data_sources if pd.notna(s)]
            source_counts_per_cat[category].append(len(valid_sources))

            for src in data_sources:
                if pd.notna(src):
                    sources_per_cat[category].add(str(src).lower())
                    all_sources.add(str(src).lower())

            for cat in data_source_cats:
                if pd.notna(cat):
                    cat_lower = str(cat).lower()
                    source_cats_per_cat[category].add(cat_lower)
                    all_source_cats.add(cat_lower)
                    if cat_lower in authoritative:
                        auth_cats_per_cat[category].add(cat_lower)

        # Compute 75th percentile (p75) of per-asset source counts as denominator.
        # p75 reflects the source coverage of well-monitored assets in each category,
        # reducing the saturation problem where p50 = 1 for categories with >50%
        # single-source assets. This provides meaningful differentiation between
        # 2-source and 10-source assets within the same category.
        all_source_counts = []
        p75_per_cat = {}
        for cat, counts in source_counts_per_cat.items():
            p75 = int(np.percentile(counts, 75)) if counts else 1
            p75_per_cat[cat] = max(p75, 2)  # Floor at 2 to avoid division issues
            all_source_counts.extend(counts)
        global_p75 = int(np.percentile(all_source_counts, 75)) if all_source_counts else 1
        global_p75 = max(global_p75, 2)

        stats = {
            'sources_per_category': {k: len(v) for k, v in sources_per_cat.items()},
            'source_cats_per_category': {k: len(v) for k, v in source_cats_per_cat.items()},
            'auth_cats_per_category': {k: len(v) for k, v in auth_cats_per_cat.items()},
            'p75_sources_per_category': p75_per_cat,
            'global_sources': len(all_sources),
            'global_source_cats': len(all_source_cats),
            'global_p75_sources': global_p75,
        }

        logger.info(
            f"Environment stats: {stats['global_sources']} total sources, "
            f"{stats['global_source_cats']} categories across "
            f"{len(sources_per_cat)} asset types"
        )
        for cat, count in sorted(stats['sources_per_category'].items()):
            cats = stats['source_cats_per_category'].get(cat, 0)
            auth = stats['auth_cats_per_category'].get(cat, 0)
            p75 = stats['p75_sources_per_category'].get(cat, 1)
            logger.info(f"  {cat}: {count} sources (p75={p75}), {cats} categories ({auth} authoritative)")

        return stats

    def calculate_confidence(
        self,
        assets_df: pd.DataFrame,
        providers_df: pd.DataFrame,
        group_mapping: Optional[Dict[str, str]] = None
    ) -> pd.DataFrame:
        """
        Calculate confidence scores using V2 algorithm.

        Args:
            assets_df: DataFrame with aggregated asset data
            providers_df: DataFrame with asset provider information (for consistency checks)

        Returns:
            DataFrame with confidence column added
        """
        logger.info(f"Calculating V2 confidence for {len(assets_df)} assets...")

        # Ensure last_seen column exists for recency calculation.
        # last_seen is normally populated from last_time by _populate_lifecycle_metadata()
        # before scoring. If missing (e.g. standalone scorer usage), use NaT so unknown
        # freshness gets the standard 0.95 penalty rather than pretending all assets
        # were just seen (which would disable the penalty entirely).
        if 'last_seen' not in assets_df.columns and self.enable_recency:
            logger.warning("last_seen column not found - using NaT (unknown freshness penalty)")
            assets_df['last_seen'] = pd.NaT

        # Collapse integration groups into logical sources
        if group_mapping:
            assets_df = self._collapse_groups(assets_df, group_mapping)

        # Compute environment stats for per-category normalization
        env_stats = self._compute_environment_stats(assets_df)

        # Pre-group providers by asset ID for O(1) lookup (avoids O(N) scan per asset)
        providers_grouped = {}
        if self.enable_consistency and not providers_df.empty:
            for asset_id, group in providers_df.groupby('id'):
                providers_grouped[asset_id] = group

        # Capture current time once (avoid 69K syscalls + ensures deterministic scoring)
        now = datetime.now(timezone.utc)

        # Collect scores in lists for bulk assignment (avoids per-row .at[] writes)
        confidence_vals = []
        base_vals = []
        consistency_vals = []
        recency_vals = []
        id_cap_vals = []
        breakdown_vals = []

        # Pre-check which columns exist (avoid per-row 'in' checks)
        has_category_best = 'category_best' in assets_df.columns
        has_last_seen = 'last_seen' in assets_df.columns

        # Calculate confidence for each asset
        for idx, row in assets_df.iterrows():
            # Determine available sources for this asset's category
            raw_cat = row.get('category_best') if has_category_best else row.get('category')
            asset_category = self._safe_scalar(raw_cat, 'unknown').lower()
            available_sources = env_stats['p75_sources_per_category'].get(asset_category, env_stats['global_p75_sources'])
            available_cats = env_stats['source_cats_per_category'].get(asset_category, env_stats['global_source_cats'])
            available_auth = env_stats['auth_cats_per_category'].get(asset_category, 0)

            # Step 1: Calculate BASE_SCORE from gradients
            source_reliability = self._calculate_source_reliability(row, available_cats, available_auth)
            source_count_score = self._calculate_source_count_score(row, available_sources)

            # Cache field-presence checks (used by completeness, missing_fields,
            # identifier_cap, and identifier_type — avoids 4x redundant evaluation)
            field_has_data = {}
            for tier_fields in self.FIELD_TIERS.values():
                for field in tier_fields:
                    field_has_data[field] = self._has_data(row, field)

            has_valid = {}
            for field in ('sn', 'hostname', 'fqdn', 'mac', 'ip'):
                has_valid[field] = self._has_valid_value(row, field)

            # Check physical MAC once (used by identifier_cap + identifier_type)
            mac_value = row.get('mac')
            if isinstance(mac_value, (list, np.ndarray)) and len(mac_value) > 0:
                mac_value = mac_value[0]
            has_physical_mac = has_valid['mac'] and not self._is_virtual_mac(mac_value)

            data_completeness = self._calculate_data_completeness_cached(field_has_data)

            # Step 2: Apply gates/multipliers
            consistency_penalty = 1.0
            conflicting_fields = []
            if self.enable_consistency:
                asset_ids = row['id'] if not isinstance(row['id'], list) else row['id']
                if not isinstance(asset_ids, list):
                    asset_ids = [asset_ids]
                # Combined call: compute penalty + conflicting fields in one pass
                consistency_penalty, conflicting_fields = self._calculate_consistency_and_conflicts(
                    providers_grouped, asset_ids
                )

            # Reduce completeness credit when fields have conflicting values (#57)
            if consistency_penalty < 0.95:  # Meaningful conflicts exist
                # Scale reduction by severity of conflicts
                conflict_reduction = 1.0 - ((1.0 - consistency_penalty) * 0.3)
                data_completeness *= conflict_reduction

            # Parse data_sources early for weight selection and breakdown dict
            data_sources = row.get('data_source', [])
            if not isinstance(data_sources, list):
                data_sources = [data_sources] if pd.notna(data_sources) else []

            # Rebalance weights for single-source assets (#286)
            # source_count_score is a near-constant for single-source assets
            # (sqrt(1)/sqrt(N)), so shift weight to completeness where
            # actual differentiation lives.
            if len(data_sources) == 1:
                w = self.WEIGHTS_SINGLE_SOURCE
                weights_used = 'single_source'
            else:
                w = self.WEIGHTS_STANDARD
                weights_used = 'standard'

            base_score = (
                w['source_reliability'] * source_reliability +
                w['source_count'] * source_count_score +
                w['data_completeness'] * data_completeness
            )

            recency_penalty = 1.0
            if self.enable_recency and has_last_seen:
                recency_penalty = self._calculate_recency_penalty_with_now(row['last_seen'], now)

            identifier_cap = self._calculate_identifier_cap_cached(
                has_valid, has_physical_mac
            ) if self.enable_identifier_cap else 1.0

            # Final confidence (raw multiplicative score, then calibrate)
            raw_confidence = base_score * consistency_penalty * recency_penalty * identifier_cap
            confidence = self._apply_calibration(raw_confidence)

            # Collect scores for bulk assignment
            confidence_vals.append(float(confidence))
            base_vals.append(float(base_score))
            consistency_vals.append(float(consistency_penalty))
            recency_vals.append(float(recency_penalty))
            id_cap_vals.append(float(identifier_cap))

            # Build structured confidence breakdown dict

            # Compute days_since_seen using pre-captured now
            days_since_seen = None
            if has_last_seen and pd.notna(row['last_seen']):
                last_seen_val = row['last_seen']
                if hasattr(last_seen_val, 'year') and last_seen_val.year < 2000:
                    pass  # Pre-2000 dates treated as unknown
                else:
                    if hasattr(last_seen_val, 'tzinfo') and last_seen_val.tzinfo is None:
                        last_seen_val = last_seen_val.replace(tzinfo=timezone.utc)
                    days_since_seen = (now - last_seen_val).days

            # Reuse cached field checks for identifier_type and missing_fields
            identifier_type = self._get_identifier_type_cached(has_valid, has_physical_mac)
            missing_fields = [f for f, present in field_has_data.items() if not present]

            breakdown_vals.append({
                'version': 2,
                'raw_confidence': round(float(raw_confidence), 4),
                'calibrated': self.calibration_curve is not None,
                'base_score': round(float(base_score), 4),
                'source_reliability': round(float(source_reliability), 4),
                'source_count_score': round(float(source_count_score), 4),
                'data_completeness': round(float(data_completeness), 4),
                'consistency_penalty': round(float(consistency_penalty), 4),
                'recency_penalty': round(float(recency_penalty), 4),
                'identifier_cap': round(float(identifier_cap), 4),
                'source_count': len(data_sources),
                'weights_used': weights_used,
                'available_sources': available_sources,
                'identifier_type': identifier_type,
                'missing_fields': missing_fields,
                'conflicting_fields': conflicting_fields,
                'days_since_seen': days_since_seen,
            })

        # Bulk-assign all score columns (avoids per-row .at[] dtype checking)
        assets_df['confidence'] = confidence_vals
        assets_df['confidence_base'] = base_vals
        assets_df['confidence_consistency_penalty'] = consistency_vals
        assets_df['confidence_recency_penalty'] = recency_vals
        assets_df['confidence_identifier_cap'] = id_cap_vals
        assets_df['confidence_breakdown'] = breakdown_vals

        # Log statistics
        confidence_values = assets_df['confidence']
        logger.info(
            f"V2 Confidence scores: "
            f"mean={confidence_values.mean():.3f}, "
            f"median={confidence_values.median():.3f}, "
            f"min={confidence_values.min():.3f}, "
            f"max={confidence_values.max():.3f}"
        )

        return assets_df

    # =========================================================================
    # GRADIENTS - BASE SCORE COMPONENTS
    # =========================================================================

    def _calculate_source_reliability(
        self, row: pd.Series, available_cats: int = 5, available_auth: int = 2
    ) -> float:
        """
        Calculate source reliability score with environment-aware bonuses.

        Factors:
        - Base: Weighted average of source reliability scores
        - Bonus: Cross-validation (+10% for 2+ authoritative, or +5% if
          only 1 authoritative type exists for this category and the asset has it)
        - Bonus: Source diversity (+5% when asset has all or nearly all
          available source categories)

        Args:
            row: Asset row with data_source and data_source_category fields
            available_cats: Number of distinct source categories available
                for this asset's category (from environment stats)
            available_auth: Number of distinct authoritative source categories
                available for this asset's category

        Returns:
            Source reliability score (0.0-1.0)
        """
        data_sources = row.get('data_source', [])
        data_source_categories = row.get('data_source_category', [])

        if not isinstance(data_sources, list):
            data_sources = [data_sources] if pd.notna(data_sources) else []
        if not isinstance(data_source_categories, list):
            data_source_categories = [data_source_categories] if pd.notna(data_source_categories) else []

        if len(data_source_categories) == 0:
            return 0.5  # Neutral score if no source info

        # Base: weighted average of source scores
        scores = []
        for cat in data_source_categories:
            cat_lower = str(cat).lower() if pd.notna(cat) else ''
            score = self.source_reliability_scores.get(cat_lower, 0.5)
            scores.append(score)

        base = np.mean(scores) if scores else 0.5

        # Bonuses: apply additively, cap once at end (#56)
        authoritative = [
            'endpoint security',         # On-device agents (Defender, SentinelOne)
            'cloud platform',            # Infrastructure sources (Azure)
            'mobile device management',  # MDM systems (Intune)
            'mdm',                       # Legacy alias
            'virtualization'             # Legacy alias
        ]
        auth_count = sum(
            1 for cat in data_source_categories
            if str(cat).lower() in authoritative
        )

        # Cross-validation bonus — environment-aware
        if available_auth >= 2 and auth_count >= 2:
            # Multiple authoritative sources corroborate: full bonus
            base += 0.10
        elif available_auth <= 1 and auth_count >= 1 and len(data_sources) >= 2:
            # Only 1 authoritative type exists for this category and asset has it
            # plus at least one other source: partial bonus
            base += 0.05

        # Diversity bonus — environment-aware threshold
        unique_categories = len(set(str(c).lower() for c in data_source_categories))
        # Require covering most available categories (at least available-1, minimum 2)
        diversity_threshold = max(2, available_cats - 1) if available_cats >= 2 else available_cats
        if unique_categories >= diversity_threshold:
            base += 0.05

        return min(1.0, base)

    def _calculate_source_count_score(self, row: pd.Series, available_sources: int = 5) -> float:
        """
        Calculate source count score with diminishing returns, normalized
        to the number of sources available for this asset's category.

        Uses sqrt function to model diminishing returns:
        - n/available = 1.0 when asset has all available sources
        - Partial coverage scales via sqrt for diminishing returns

        Args:
            row: Asset row with data_source field
            available_sources: Number of distinct sources that report
                this asset's category (derived from environment stats)

        Returns:
            Source count score (0.0-1.0)
        """
        data_sources = row.get('data_source', [])
        if not isinstance(data_sources, list):
            data_sources = [data_sources] if pd.notna(data_sources) else []

        num_sources = len(data_sources)
        if num_sources == 0:
            return 0.0

        # Normalize to available sources for this category
        # max(available, 1) prevents division by zero
        denominator = max(available_sources, 1)
        score = min(1.0, math.sqrt(num_sources) / math.sqrt(denominator))

        return score

    def _calculate_data_completeness(self, row: pd.Series) -> float:
        """
        Calculate data completeness with weighted field importance.

        Field tiers:
        - Tier 1 (3 points): sn, hostname, fqdn, mac
        - Tier 2 (2 points): ip, os_normalized, manufacturer, model
        - Tier 3 (1 point): location, owner, category, os_eol_date

        Args:
            row: Asset row

        Returns:
            Completeness score (0.0-1.0)
        """
        total_possible = (
            len(self.FIELD_TIERS['tier1']) * 3 +
            len(self.FIELD_TIERS['tier2']) * 2 +
            len(self.FIELD_TIERS['tier3']) * 1
        )

        actual_score = 0

        # Tier 1: Critical identifiers
        for field in self.FIELD_TIERS['tier1']:
            if self._has_data(row, field):
                actual_score += 3

        # Tier 2: Important attributes
        for field in self.FIELD_TIERS['tier2']:
            if self._has_data(row, field):
                actual_score += 2

        # Tier 3: Contextual fields
        for field in self.FIELD_TIERS['tier3']:
            if self._has_data(row, field):
                actual_score += 1

        return actual_score / total_possible if total_possible > 0 else 0.0

    def _calculate_data_completeness_cached(self, field_has_data: Dict[str, bool]) -> float:
        """Calculate data completeness using pre-computed field presence checks."""
        total_possible = (
            len(self.FIELD_TIERS['tier1']) * 3 +
            len(self.FIELD_TIERS['tier2']) * 2 +
            len(self.FIELD_TIERS['tier3']) * 1
        )

        actual_score = 0
        for field in self.FIELD_TIERS['tier1']:
            if field_has_data.get(field, False):
                actual_score += 3
        for field in self.FIELD_TIERS['tier2']:
            if field_has_data.get(field, False):
                actual_score += 2
        for field in self.FIELD_TIERS['tier3']:
            if field_has_data.get(field, False):
                actual_score += 1

        return actual_score / total_possible if total_possible > 0 else 0.0

    # =========================================================================
    # GATES - PENALTY/CAP MULTIPLIERS
    # =========================================================================

    def _calculate_consistency_penalty(
        self,
        providers_df: pd.DataFrame,
        asset_ids: List[str]
    ) -> float:
        """
        Calculate consistency penalty based on field disagreements.

        Uses graduated agreement strength: the penalty scales based on what
        fraction of sources agree with the majority value.

        Per-field max penalties (CONSISTENCY_SEVERITY):
        - SN mismatch: up to 70% penalty (multiply by 0.30)
        - Hostname mismatch: up to 40% penalty (multiply by 0.60)
        - FQDN mismatch: up to 35% penalty (multiply by 0.65)
        - MAC mismatch: up to 25% penalty (multiply by 0.75)
        - OS mismatch: up to 20% penalty (multiply by 0.80)

        Agreement ratio scaling:
        - >= 80% agree (e.g. 4/5): apply 30% of max penalty
        - >= 60% agree (e.g. 2/3): apply 60% of max penalty
        - < 60% agree (all differ): apply full penalty

        Reliability weighting:
        When data_source_category is available, if the majority group has
        higher average reliability than the minority, a bonus (up to +0.15)
        is added to the effective penalty, reducing its severity.

        Args:
            providers_df: Provider data with individual source records
            asset_ids: List of asset IDs (components) for this asset

        Returns:
            Consistency multiplier (0.3-1.0)
        """
        # Get providers for this asset
        asset_providers = providers_df[providers_df['id'].isin(asset_ids)]

        if len(asset_providers) <= 1:
            return 1.0  # Single source = no disagreement possible

        multiplier = 1.0

        for field, penalty_multiplier in self.CONSISTENCY_SEVERITY.items():
            if field not in asset_providers.columns:
                continue

            # Get non-null values for this field
            field_values = asset_providers[field].dropna()

            if len(field_values) == 0:
                continue

            # Normalize string fields to lowercase for case-insensitive comparison
            # This prevents false mismatches like "hostname" vs "HOSTNAME"
            if field in ['hostname', 'fqdn', 'os_normalized', 'category', 'subcategory']:
                # Convert to lowercase for comparison
                field_values = field_values.astype(str).str.lower()

            # FQDN prefix matching: if a source reports just the hostname
            # (e.g. "ncopbib1") while others report the full FQDN
            # (e.g. "ncopbib1.primprod.net"), treat them as agreeing.
            # The source simply didn't include the domain suffix.
            if field == 'fqdn' and field_values.nunique() > 1:
                field_values = self._normalize_fqdn_prefixes(field_values)

            # Count unique normalized values
            unique_values = field_values.nunique()

            if unique_values > 1:
                # Calculate agreement ratio — what fraction agrees with majority
                total_values = len(field_values)
                most_common_count = field_values.value_counts().iloc[0]
                agreement_ratio = most_common_count / total_values

                # Scale penalty by agreement strength
                # penalty_multiplier is the max penalty (e.g., 0.60 for hostname = 40% penalty)
                # max_penalty = 1.0 - penalty_multiplier (e.g., 0.40)
                max_penalty = 1.0 - penalty_multiplier

                if agreement_ratio >= 0.80:
                    # Strong majority (4/5+ agree) — apply 30% of max penalty
                    effective_penalty = 1.0 - (max_penalty * 0.3)
                elif agreement_ratio >= 0.60:
                    # Simple majority (2/3 agree) — apply 60% of max penalty
                    effective_penalty = 1.0 - (max_penalty * 0.6)
                else:
                    # Weak/no majority (all differ) — apply full penalty
                    effective_penalty = penalty_multiplier

                # Multi-IP tolerance: when sources report IPs from different
                # ranges (e.g. private 10.x + public IP), this is normal
                # (cloud VMs, VPN, dual-stack) — not a real conflict.
                # Halve the penalty in these cases.
                if field == 'ip' and self._has_mixed_ip_ranges(field_values):
                    current_penalty = 1.0 - effective_penalty
                    effective_penalty = 1.0 - (current_penalty * 0.5)

                # Weight by source reliability if data_source_category available
                if 'data_source_category' in asset_providers.columns:
                    majority_value = field_values.value_counts().index[0]
                    majority_mask = field_values == majority_value

                    categories = asset_providers.loc[field_values.index, 'data_source_category']
                    majority_reliability = categories[majority_mask].map(
                        self.source_reliability_scores
                    ).fillna(0.5).mean()
                    minority_reliability = categories[~majority_mask].map(
                        self.source_reliability_scores
                    ).fillna(0.5).mean() if (~majority_mask).any() else 0.0

                    # If majority is backed by higher-reliability sources, reduce penalty
                    if majority_reliability > minority_reliability:
                        reliability_bonus = min(0.15, (majority_reliability - minority_reliability) * 0.2)
                        effective_penalty = min(1.0, effective_penalty + reliability_bonus)

                multiplier *= effective_penalty

        return multiplier

    def _calculate_consistency_penalty_fast(
        self,
        providers_grouped: Dict[str, pd.DataFrame],
        asset_ids: List[str]
    ) -> float:
        """
        Calculate consistency penalty using pre-grouped providers for O(1) lookup.

        Identical logic to _calculate_consistency_penalty() but avoids the O(N)
        scan of providers_df per asset by using a pre-computed groupby dict.

        Args:
            providers_grouped: Dict mapping asset ID to its provider DataFrame group
            asset_ids: List of asset IDs (components) for this asset

        Returns:
            Consistency multiplier (0.3-1.0)
        """
        # Get providers for this asset via O(1) dict lookups
        groups = [providers_grouped[aid] for aid in asset_ids if aid in providers_grouped]
        if not groups:
            return 1.0

        asset_providers = pd.concat(groups) if len(groups) > 1 else groups[0]

        if len(asset_providers) <= 1:
            return 1.0  # Single source = no disagreement possible

        multiplier = 1.0

        for field, penalty_multiplier in self.CONSISTENCY_SEVERITY.items():
            if field not in asset_providers.columns:
                continue

            # Get non-null values for this field
            field_values = asset_providers[field].dropna()

            if len(field_values) == 0:
                continue

            # Normalize string fields to lowercase for case-insensitive comparison
            # This prevents false mismatches like "hostname" vs "HOSTNAME"
            if field in ['hostname', 'fqdn', 'os_normalized', 'category', 'subcategory']:
                # Convert to lowercase for comparison
                field_values = field_values.astype(str).str.lower()

            # FQDN prefix matching: if a source reports just the hostname
            # (e.g. "ncopbib1") while others report the full FQDN
            # (e.g. "ncopbib1.primprod.net"), treat them as agreeing.
            # The source simply didn't include the domain suffix.
            if field == 'fqdn' and field_values.nunique() > 1:
                field_values = self._normalize_fqdn_prefixes(field_values)

            # Count unique normalized values
            unique_values = field_values.nunique()

            if unique_values > 1:
                # Calculate agreement ratio — what fraction agrees with majority
                total_values = len(field_values)
                most_common_count = field_values.value_counts().iloc[0]
                agreement_ratio = most_common_count / total_values

                # Scale penalty by agreement strength
                # penalty_multiplier is the max penalty (e.g., 0.60 for hostname = 40% penalty)
                # max_penalty = 1.0 - penalty_multiplier (e.g., 0.40)
                max_penalty = 1.0 - penalty_multiplier

                if agreement_ratio >= 0.80:
                    # Strong majority (4/5+ agree) — apply 30% of max penalty
                    effective_penalty = 1.0 - (max_penalty * 0.3)
                elif agreement_ratio >= 0.60:
                    # Simple majority (2/3 agree) — apply 60% of max penalty
                    effective_penalty = 1.0 - (max_penalty * 0.6)
                else:
                    # Weak/no majority (all differ) — apply full penalty
                    effective_penalty = penalty_multiplier

                # Multi-IP tolerance: when sources report IPs from different
                # ranges (e.g. private 10.x + public IP), this is normal
                # (cloud VMs, VPN, dual-stack) — not a real conflict.
                # Halve the penalty in these cases.
                if field == 'ip' and self._has_mixed_ip_ranges(field_values):
                    current_penalty = 1.0 - effective_penalty
                    effective_penalty = 1.0 - (current_penalty * 0.5)

                # Weight by source reliability if data_source_category available
                if 'data_source_category' in asset_providers.columns:
                    majority_value = field_values.value_counts().index[0]
                    majority_mask = field_values == majority_value

                    categories = asset_providers.loc[field_values.index, 'data_source_category']
                    majority_reliability = categories[majority_mask].map(
                        self.source_reliability_scores
                    ).fillna(0.5).mean()
                    minority_reliability = categories[~majority_mask].map(
                        self.source_reliability_scores
                    ).fillna(0.5).mean() if (~majority_mask).any() else 0.0

                    # If majority is backed by higher-reliability sources, reduce penalty
                    if majority_reliability > minority_reliability:
                        reliability_bonus = min(0.15, (majority_reliability - minority_reliability) * 0.2)
                        effective_penalty = min(1.0, effective_penalty + reliability_bonus)

                multiplier *= effective_penalty

        return multiplier

    @staticmethod
    def _normalize_fqdn_prefixes(field_values: pd.Series) -> pd.Series:
        """
        Normalize FQDN values so that bare hostnames are treated as agreeing
        with their fully-qualified counterparts.

        If a shorter value (e.g. "ncopbib1") matches the hostname portion of
        a longer value (e.g. "ncopbib1.primprod.net"), remap the shorter value
        to the longer majority value.

        Only remaps when the shorter value has no dots (i.e. is a bare hostname).
        Values like "host1.domain.com" vs "host1.other.com" remain as conflicts.
        """
        unique_vals = field_values.unique()
        if len(unique_vals) <= 1:
            return field_values

        # Build a mapping: bare hostname → longest matching FQDN
        remap = {}
        fqdns_with_dots = [v for v in unique_vals if '.' in v]
        bare_hostnames = [v for v in unique_vals if '.' not in v]

        for bare in bare_hostnames:
            for fqdn in fqdns_with_dots:
                # Check if the FQDN starts with "bare." (hostname prefix match)
                if fqdn.startswith(bare + '.'):
                    remap[bare] = fqdn
                    break  # First match is sufficient

        if not remap:
            return field_values

        return field_values.replace(remap)

    @staticmethod
    def _has_mixed_ip_ranges(field_values: pd.Series) -> bool:
        """
        Check whether IP values span different network ranges or address families.

        Returns True when disagreements look like multi-interface behavior rather
        than identity conflicts:
        - Private vs public IPs
        - Multiple distinct RFC1918 ranges (10.x vs 172.x vs 192.168.x)
        - IPv4 vs IPv6 mix
        """
        private_ranges = set()  # Track which RFC1918 ranges appear
        has_public = False
        has_ipv4 = False
        has_ipv6 = False

        for ip_str in field_values.unique():
            try:
                ip = str(ip_str).split('/')[0].strip()  # Strip CIDR notation

                if ':' in ip:
                    has_ipv6 = True
                    continue

                has_ipv4 = True

                if ip.startswith('10.'):
                    private_ranges.add('10')
                elif ip.startswith('192.168.'):
                    private_ranges.add('192.168')
                elif (ip.startswith('172.') and
                      16 <= int(ip.split('.')[1]) <= 31):
                    private_ranges.add('172')
                elif ip and not ip.startswith('127.') and not ip.startswith('0.'):
                    has_public = True
            except (ValueError, IndexError):
                continue

        # Multiple distinct private ranges (e.g. VPN + LAN)
        if len(private_ranges) > 1:
            return True
        # Private vs public
        if private_ranges and has_public:
            return True
        # Mixed address families
        if has_ipv4 and has_ipv6:
            return True

        return False

    def _calculate_recency_penalty(self, last_seen: datetime) -> float:
        """
        Calculate recency penalty based on data age using smooth decay.

        Uses exponential decay: penalty = max(0.40, e^(-days/half_life_days * ln2))
        with half_life_days = 90 (score halves every 90 days).

        Approximate values:
        - 0 days: 1.00
        - 7 days: 0.95
        - 30 days: 0.79
        - 60 days: 0.63
        - 90 days: 0.50
        - 180 days: 0.40 (floor)

        Args:
            last_seen: Last time asset was seen

        Returns:
            Recency multiplier (0.4-1.0)
        """
        if pd.isna(last_seen):
            return 0.95  # Unknown freshness — minor penalty

        # Treat impossible dates (pre-2000) as unknown freshness
        if hasattr(last_seen, 'year') and last_seen.year < 2000:
            return 0.90

        # Ensure timezone-aware comparison
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)

        current_time = datetime.now(timezone.utc)
        days_old = (current_time - last_seen).days

        if days_old < 1:
            return 1.0

        # Exponential decay with 90-day half-life, floor at 0.40
        half_life = 90.0
        penalty = math.exp(-days_old * math.log(2) / half_life)
        return max(0.40, penalty)

    def _calculate_recency_penalty_with_now(self, last_seen: datetime, now: datetime) -> float:
        """Calculate recency penalty using a pre-captured 'now' timestamp."""
        if pd.isna(last_seen):
            return 0.95  # Unknown freshness — minor penalty

        if hasattr(last_seen, 'year') and last_seen.year < 2000:
            return 0.90

        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)

        days_old = (now - last_seen).days
        if days_old < 1:
            return 1.0

        half_life = 90.0
        penalty = math.exp(-days_old * math.log(2) / half_life)
        return max(0.40, penalty)

    def _calculate_identifier_cap(self, row: pd.Series) -> float:
        """
        Calculate maximum confidence cap based on identifier quality.

        Identifier strength hierarchy:
        - Serial Number: 1.0 (no cap - gold standard)
        - FQDN + Physical MAC: 0.95
        - FQDN alone: 0.90
        - Hostname + Physical MAC: 0.90
        - Hostname only: 0.80
        - Physical MAC only: 0.70
        - IP only: 0.40 (transient identifier)
        - Minimal/no identifiers: 0.30

        Args:
            row: Asset row

        Returns:
            Maximum confidence cap (0.3-1.0)
        """
        has_sn = self._has_valid_value(row, 'sn')
        has_hostname = self._has_valid_value(row, 'hostname')
        has_fqdn = self._has_valid_value(row, 'fqdn')
        has_mac = self._has_valid_value(row, 'mac')
        has_ip = self._has_valid_value(row, 'ip')

        # Check if MAC is physical (not virtual)
        # Get first MAC from array if it exists
        mac_value = row.get('mac')
        if isinstance(mac_value, (list, np.ndarray)) and len(mac_value) > 0:
            mac_value = mac_value[0]
        has_physical_mac = has_mac and not self._is_virtual_mac(mac_value)

        # Determine cap based on identifier strength
        if has_sn:
            return 1.0  # Serial number = gold standard
        elif has_fqdn and has_physical_mac:
            return 0.95  # FQDN + physical MAC
        elif has_fqdn:
            return 0.90  # FQDN alone
        elif has_hostname and has_physical_mac:
            return 0.90  # Hostname + physical MAC
        elif has_hostname:
            return 0.80  # Hostname alone
        elif has_physical_mac:
            return 0.70  # MAC alone
        elif has_ip and not any([has_sn, has_hostname, has_fqdn, has_physical_mac]):
            return 0.40  # IP-only = transient
        else:
            return 0.30  # Minimal identifiers

    def _calculate_identifier_cap_cached(
        self, has_valid: Dict[str, bool], has_physical_mac: bool
    ) -> float:
        """Calculate identifier cap using pre-computed field validity checks."""
        if has_valid['sn']:
            return 1.0
        elif has_valid['fqdn'] and has_physical_mac:
            return 0.95
        elif has_valid['fqdn']:
            return 0.90
        elif has_valid['hostname'] and has_physical_mac:
            return 0.90
        elif has_valid['hostname']:
            return 0.80
        elif has_physical_mac:
            return 0.70
        elif has_valid['ip'] and not any([has_valid['sn'], has_valid['hostname'],
                                          has_valid['fqdn'], has_physical_mac]):
            return 0.40
        else:
            return 0.30

    def _get_identifier_type_cached(
        self, has_valid: Dict[str, bool], has_physical_mac: bool
    ) -> str:
        """Return identifier type label using pre-computed field validity checks."""
        if has_valid['sn']:
            return 'serial_number'
        elif has_valid['fqdn'] and has_physical_mac:
            return 'fqdn_and_mac'
        elif has_valid['fqdn']:
            return 'fqdn'
        elif has_valid['hostname'] and has_physical_mac:
            return 'hostname_and_mac'
        elif has_valid['hostname']:
            return 'hostname'
        elif has_physical_mac:
            return 'mac'
        elif has_valid['ip'] and not any([has_valid['sn'], has_valid['hostname'],
                                          has_valid['fqdn'], has_physical_mac]):
            return 'ip_only'
        else:
            return 'minimal'

    def _calculate_consistency_and_conflicts(
        self,
        providers_grouped: Dict[str, pd.DataFrame],
        asset_ids: List[str]
    ) -> Tuple[float, List[str]]:
        """
        Calculate consistency penalty AND conflicting fields in a single pass.

        Combines _calculate_consistency_penalty_fast and _get_conflicting_fields_fast
        to avoid double provider concat and double field iteration.

        Returns:
            Tuple of (consistency_multiplier, list_of_conflicting_field_names)
        """
        groups = [providers_grouped[aid] for aid in asset_ids if aid in providers_grouped]
        if not groups:
            return 1.0, []

        asset_providers = pd.concat(groups) if len(groups) > 1 else groups[0]

        if len(asset_providers) <= 1:
            return 1.0, []

        multiplier = 1.0
        conflicting = []

        for field, penalty_multiplier in self.CONSISTENCY_SEVERITY.items():
            if field not in asset_providers.columns:
                continue

            # Hostname and FQDN are correlated — if hostname conflicts,
            # FQDN almost certainly does too. Apply only the harsher penalty.
            if field == 'fqdn' and 'hostname' in conflicting:
                continue

            field_values = asset_providers[field].dropna()

            if len(field_values) == 0:
                continue

            if field in ['hostname', 'fqdn', 'os_normalized', 'category', 'subcategory']:
                field_values = field_values.astype(str).str.lower()

            if field == 'fqdn' and field_values.nunique() > 1:
                field_values = self._normalize_fqdn_prefixes(field_values)

            unique_values = field_values.nunique()

            if unique_values > 1:
                # Track conflicting field
                conflicting.append(field)

                # Calculate penalty (same logic as _calculate_consistency_penalty_fast)
                total_values = len(field_values)
                most_common_count = field_values.value_counts().iloc[0]
                agreement_ratio = most_common_count / total_values

                max_penalty = 1.0 - penalty_multiplier

                if agreement_ratio >= 0.80:
                    effective_penalty = 1.0 - (max_penalty * 0.3)
                elif agreement_ratio >= 0.60:
                    effective_penalty = 1.0 - (max_penalty * 0.6)
                else:
                    effective_penalty = penalty_multiplier

                if field == 'ip' and self._has_mixed_ip_ranges(field_values):
                    current_penalty = 1.0 - effective_penalty
                    effective_penalty = 1.0 - (current_penalty * 0.5)

                if 'data_source_category' in asset_providers.columns:
                    majority_value = field_values.value_counts().index[0]
                    majority_mask = field_values == majority_value

                    categories = asset_providers.loc[field_values.index, 'data_source_category']
                    majority_reliability = categories[majority_mask].map(
                        self.source_reliability_scores
                    ).fillna(0.5).mean()
                    minority_reliability = categories[~majority_mask].map(
                        self.source_reliability_scores
                    ).fillna(0.5).mean() if (~majority_mask).any() else 0.0

                    if majority_reliability > minority_reliability:
                        reliability_bonus = min(0.15, (majority_reliability - minority_reliability) * 0.2)
                        effective_penalty = min(1.0, effective_penalty + reliability_bonus)

                multiplier *= effective_penalty

        # Source-count-aware floor: more sources should never crush confidence (#279)
        if 'data_source' in asset_providers.columns:
            num_sources = asset_providers['data_source'].nunique()
        else:
            num_sources = len(asset_providers)
        source_floor = min(self.CONSISTENCY_FLOOR_MAX, self.CONSISTENCY_FLOOR_PER_SOURCE * num_sources)
        multiplier = max(source_floor, multiplier)

        return multiplier, conflicting

    # =========================================================================
    # CALIBRATION
    # =========================================================================

    def _apply_calibration(self, raw_confidence: float) -> float:
        """Apply calibration curve to adjust confidence score.

        When a calibration curve is provided (from scripts/calibrate_confidence.py),
        this maps raw model confidence to calibrated confidence that better reflects
        actual accuracy.

        Args:
            raw_confidence: Raw confidence score from the scorer

        Returns:
            Calibrated confidence score, or raw_confidence if no curve provided
        """
        if not self.calibration_curve:
            return raw_confidence

        return float(np.interp(raw_confidence, self._cal_xp, self._cal_fp))

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    @staticmethod
    def _safe_scalar(value, default: str = 'unknown') -> str:
        """Extract a scalar string from a value that may be a list, array, or NaN."""
        if isinstance(value, (list, np.ndarray)):
            if len(value) > 0:
                return str(value[0])
            return default
        try:
            if pd.isna(value):
                return default
        except (TypeError, ValueError):
            pass
        return str(value) if value is not None else default

    def _has_data(self, row: pd.Series, field: str) -> bool:
        """Check if field has meaningful data."""
        if field not in row:
            return False

        value = row[field]

        # Check if it's a list or array first (to avoid ambiguous truth value errors)
        if isinstance(value, (list, np.ndarray)):
            return len(value) > 0 and any(pd.notna(v) and str(v).strip() != '' for v in value)

        # For scalar values, check if NaN
        try:
            if pd.isna(value):
                return False
        except (TypeError, ValueError):
            # If pd.isna fails, assume not NaN
            pass

        str_val = str(value).strip()
        if str_val == '':
            return False

        # Check for known low-quality placeholder values
        low_quality = self.LOW_QUALITY_VALUES.get(field)
        if low_quality and str_val.lower() in low_quality:
            return False

        return True

    def _has_valid_value(self, row: pd.Series, field: str) -> bool:
        """Check if field has a valid non-empty value."""
        if field not in row:
            return False

        value = row[field]

        # Check if it's a list or array first (to avoid ambiguous truth value errors)
        if isinstance(value, (list, np.ndarray)):
            # For list fields, check if any element is valid
            return len(value) > 0 and any(pd.notna(v) and str(v).strip() != '' for v in value)

        # For scalar values, check if NaN
        try:
            if pd.isna(value):
                return False
        except (TypeError, ValueError):
            # If pd.isna fails, assume not NaN
            pass

        return str(value).strip() != ''

    def _is_virtual_mac(self, mac_value) -> bool:
        """
        Check if MAC address is virtual (VMware, VirtualBox, Hyper-V, etc.).

        This is a simplified check - could be enhanced with full vendor lookup.
        """
        # Handle None or empty values
        if mac_value is None:
            return False

        # For scalar values, check if NaN
        try:
            if pd.isna(mac_value):
                return False
        except (TypeError, ValueError):
            # If pd.isna fails, assume not NaN
            pass

        mac_str = str(mac_value).upper().replace('-', ':')

        # Common virtual MAC prefixes
        virtual_prefixes = [
            '00:50:56',  # VMware ESXi
            '00:0C:29',  # VMware Workstation
            '00:05:69',  # VMware Player
            '00:1C:14',  # VMware
            '08:00:27',  # VirtualBox
            '00:15:5D',  # Hyper-V
            '52:54:00',  # KVM/QEMU
            '00:16:3E',  # Xen
            '02:42:AC',  # Docker default bridge
            '00:0D:3A',  # Azure VMs
            '42:01:0A',  # Google Cloud
            'BC:24:11',  # Proxmox
        ]

        return any(mac_str.startswith(prefix) for prefix in virtual_prefixes)

    def _get_identifier_type(self, row: pd.Series) -> str:
        """
        Return a human-readable label for the strongest identifier present.

        Uses the same logic as _calculate_identifier_cap but returns a string
        label instead of a float cap value.

        Args:
            row: Asset row

        Returns:
            Identifier type label string
        """
        has_sn = self._has_valid_value(row, 'sn')
        has_hostname = self._has_valid_value(row, 'hostname')
        has_fqdn = self._has_valid_value(row, 'fqdn')
        has_mac = self._has_valid_value(row, 'mac')
        has_ip = self._has_valid_value(row, 'ip')

        mac_value = row.get('mac')
        if isinstance(mac_value, (list, np.ndarray)) and len(mac_value) > 0:
            mac_value = mac_value[0]
        has_physical_mac = has_mac and not self._is_virtual_mac(mac_value)

        if has_sn:
            return 'serial_number'
        elif has_fqdn and has_physical_mac:
            return 'fqdn_and_mac'
        elif has_fqdn:
            return 'fqdn'
        elif has_hostname and has_physical_mac:
            return 'hostname_and_mac'
        elif has_hostname:
            return 'hostname'
        elif has_physical_mac:
            return 'mac'
        elif has_ip and not any([has_sn, has_hostname, has_fqdn, has_physical_mac]):
            return 'ip_only'
        else:
            return 'minimal'

    def _get_missing_fields(self, row: pd.Series) -> List[str]:
        """
        Return list of all field names (tier1 + tier2 + tier3) missing from the row.

        Args:
            row: Asset row

        Returns:
            List of missing field names
        """
        missing = []
        for field in self.FIELD_TIERS['tier1'] + self.FIELD_TIERS['tier2'] + self.FIELD_TIERS['tier3']:
            if not self._has_data(row, field):
                missing.append(field)
        return missing

    def _get_conflicting_fields(
        self,
        providers_df: pd.DataFrame,
        asset_ids: List[str]
    ) -> List[str]:
        """
        Return list of field names that have disagreements across providers.

        Uses the same provider filtering and normalization logic as
        _calculate_consistency_penalty, but only collects field names.

        Args:
            providers_df: Provider data with individual source records
            asset_ids: List of asset IDs (components) for this asset

        Returns:
            List of field names with disagreements
        """
        if not asset_ids:
            return []

        asset_providers = providers_df[providers_df['id'].isin(asset_ids)]

        if len(asset_providers) <= 1:
            return []

        conflicting = []

        for field in self.CONSISTENCY_SEVERITY:
            if field not in asset_providers.columns:
                continue

            field_values = asset_providers[field].dropna()

            if len(field_values) == 0:
                continue

            # Normalize string fields (same as _calculate_consistency_penalty)
            if field in ['hostname', 'fqdn', 'os_normalized', 'category', 'subcategory']:
                field_values = field_values.astype(str).str.lower()

            # Apply FQDN prefix normalization (same as penalty calculation)
            if field == 'fqdn' and field_values.nunique() > 1:
                field_values = self._normalize_fqdn_prefixes(field_values)

            if field_values.nunique() > 1:
                conflicting.append(field)

        return conflicting

    def _get_conflicting_fields_fast(
        self,
        providers_grouped: Dict[str, pd.DataFrame],
        asset_ids: List[str]
    ) -> List[str]:
        """
        Return list of field names with disagreements using pre-grouped providers.

        Identical logic to _get_conflicting_fields() but avoids the O(N) scan
        of providers_df per asset by using a pre-computed groupby dict.

        Args:
            providers_grouped: Dict mapping asset ID to its provider DataFrame group
            asset_ids: List of asset IDs (components) for this asset

        Returns:
            List of field names with disagreements
        """
        if not asset_ids:
            return []

        # Get providers for this asset via O(1) dict lookups
        groups = [providers_grouped[aid] for aid in asset_ids if aid in providers_grouped]
        if not groups:
            return []

        asset_providers = pd.concat(groups) if len(groups) > 1 else groups[0]

        if len(asset_providers) <= 1:
            return []

        conflicting = []

        for field in self.CONSISTENCY_SEVERITY:
            if field not in asset_providers.columns:
                continue

            field_values = asset_providers[field].dropna()

            if len(field_values) == 0:
                continue

            # Normalize string fields (same as _calculate_consistency_penalty)
            if field in ['hostname', 'fqdn', 'os_normalized', 'category', 'subcategory']:
                field_values = field_values.astype(str).str.lower()

            # Apply FQDN prefix normalization (same as penalty calculation)
            if field == 'fqdn' and field_values.nunique() > 1:
                field_values = self._normalize_fqdn_prefixes(field_values)

            if field_values.nunique() > 1:
                conflicting.append(field)

        return conflicting

    def get_scoring_stats(self) -> Dict[str, Any]:
        """
        Get scoring configuration.

        Returns:
            Dictionary with V2 scoring information
        """
        return {
            'version': 2,
            'source_reliability_scores': self.source_reliability_scores,
            'field_tiers': self.FIELD_TIERS,
            'consistency_severity': self.CONSISTENCY_SEVERITY,
            'weights': {
                'standard': dict(self.WEIGHTS_STANDARD),
                'single_source': dict(self.WEIGHTS_SINGLE_SOURCE),
            },
            'gates_enabled': {
                'recency_penalty': self.enable_recency,
                'identifier_cap': self.enable_identifier_cap,
                'consistency_penalty': self.enable_consistency
            }
        }
