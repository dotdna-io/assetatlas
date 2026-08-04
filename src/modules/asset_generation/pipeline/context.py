# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pipeline context — shared state passed between pipeline stages."""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import UUID, uuid4
from concurrent.futures import Future

import pandas as pd


@dataclass
class PipelineContext:
    """
    Mutable state bag carried across all pipeline stages.

    Every stage reads from and writes to this context. The orchestrator
    creates it at pipeline start and reads the final result from it at end.
    """

    # --- Inputs (set once by orchestrator) ---
    raw_data_df: pd.DataFrame
    mac_vendor_df: Optional[pd.DataFrame] = None
    ipam_df: Optional[pd.DataFrame] = None
    geo_reader: Optional[Any] = None  # maxminddb.Reader, opened by orchestrator
    track_changes: bool = False
    on_stage_change: Optional[Callable[[str], None]] = None

    # --- Run identity ---
    generation_run_id: UUID = field(default_factory=uuid4)
    pipeline_run_id: Optional[UUID] = None
    pipeline_start: float = field(default_factory=time.time)
    pipeline_wall_start: float = field(default_factory=time.time)

    # --- DataFrames flowing through stages ---
    preprocessed_df: Optional[pd.DataFrame] = None
    asset_field_df: Optional[pd.DataFrame] = None
    id_assigned_df: Optional[pd.DataFrame] = None
    valid_records_df: Optional[pd.DataFrame] = None
    expanded_df: Optional[pd.DataFrame] = None
    enriched_df: Optional[pd.DataFrame] = None
    aggregated_df: Optional[pd.DataFrame] = None
    scored_df: Optional[pd.DataFrame] = None

    # --- Intermediate data ---
    all_components: set = field(default_factory=set)
    existing_mappings: Optional[list] = None
    excluded_records: Optional[list] = None
    excluded_count: int = 0
    excluded_records_future: Optional[Future] = None
    providers_df_prepared: Optional[pd.DataFrame] = None
    aggregated_predictions: Optional[pd.DataFrame] = None
    old_assets_df: Optional[pd.DataFrame] = None
    historical_ids: Optional[list] = None
    # {removed_id: surviving_id} — set by s04 after stale-ID resolution
    merge_pairs: Dict[str, str] = field(default_factory=dict)

    # --- Stats ---
    stats: Dict[str, Any] = field(default_factory=lambda: {
        'stages': {},
    })

    # --- Result ---
    result_df: Optional[pd.DataFrame] = None

    def notify_stage(self, stage_label: str) -> None:
        """Call the on_stage_change callback if set."""
        if self.on_stage_change:
            self.on_stage_change(stage_label)

    def record_stage(self, stage_key: str, duration: float, **extra) -> None:
        """Record timing and metadata for a stage."""
        self.stats['stages'][stage_key] = {
            'duration': duration,
            **extra,
        }
        self.stats['last_stage'] = stage_key
