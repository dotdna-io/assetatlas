# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persist per-run, per-stage pipeline durations.

Called from the asset-generation orchestrator after `detect_slow_stages` so
the operator-facing Pipeline view can compute rolling baselines.

The function is intentionally tolerant: any failure here is logged and
swallowed — a stats-table problem must never kill an otherwise-successful
pipeline run.
"""

import logging
from datetime import datetime
from typing import Any, Dict
from uuid import UUID

from src.modules.pipeline.phase_mapping import STAGE_TO_PHASE

logger = logging.getLogger(__name__)


_INSERT_SQL = """
INSERT INTO pipeline_stage_durations
    (run_id, stage_key, phase, duration_seconds, started_at)
VALUES (%s::uuid, %s, %s, %s, %s)
ON CONFLICT (run_id, stage_key, started_at) DO NOTHING
"""


def record_stage_durations(
    db,
    run_id: UUID,
    started_at: datetime,
    stats: Dict[str, Any],
) -> None:
    """Insert one row per timed stage.

    Skips stages without a `duration` (skipped or pre-error). Skips stages
    whose key is not in STAGE_TO_PHASE — logs a warning so a forgotten
    phase mapping is visible without blocking the pipeline.
    """
    stages = stats.get("stages", {})
    for stage_key, sdata in stages.items():
        if not isinstance(sdata, dict):
            continue
        if "duration" not in sdata:
            continue

        phase = STAGE_TO_PHASE.get(stage_key)
        if phase is None:
            logger.warning(
                "Stage %r has no phase mapping — skipping persistence. "
                "Add it to src/modules/pipeline/phase_mapping.py.",
                stage_key,
            )
            continue

        try:
            db.execute_query(
                _INSERT_SQL,
                (str(run_id), stage_key, phase,
                 float(sdata["duration"]), started_at),
                fetch=False,
            )
        except Exception:
            # Stats-table problems must not fail a successful run. Log and
            # move on — the next stage's insert is independent.
            logger.exception(
                "Failed to persist duration for stage %s (run %s)",
                stage_key, run_id,
            )
