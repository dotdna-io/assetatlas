# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operator-view pipeline phase service.

Aggregates per-stage durations from `pipeline_stage_durations` into the
four operator phases and flags sub-stages whose duration exceeded a
rolling baseline (2× p95 over the last 30 runs, with a ≥5-sample warm-up).

Stage status logic (intentionally minimal for v1):
- run.status = 'completed': every phase reports `success`.
- run.status = 'failed':
    * a phase with rows for ALL its expected stages → `success`
      (the failure happened in a later phase)
    * a phase with rows for SOME stages → `failed`
      (it crashed mid-execution)
    * a phase with no rows → `not_run`
- run.status in ('pending','running'): every phase = `running`.
- Skipped stages don't write rows. In rare runs where a phase contains
  skipped stages, the status may report `failed` instead of `success`.
  Acceptable for v1 — UI also shows the run-level status which is the
  source of truth.
"""

from typing import Any, Dict, List, Optional
from uuid import UUID

from src.modules.pipeline.phase_mapping import PHASES, STAGE_TO_PHASE


SLOW_RATIO = 2.0
WINDOW_RUNS = 30
MIN_SAMPLES = 5


_RUN_META_SQL = """
SELECT status, current_stage
FROM pipeline_runs
WHERE id = %s::uuid
"""


_STAGES_WITH_HISTORY_SQL = """
WITH this_run AS (
    SELECT stage_key, phase, duration_seconds
    FROM pipeline_stage_durations
    WHERE run_id = %s::uuid
),
ranked_history AS (
    SELECT stage_key, duration_seconds,
           ROW_NUMBER() OVER (
               PARTITION BY stage_key ORDER BY started_at DESC
           ) AS rn
    FROM pipeline_stage_durations
    WHERE run_id <> %s::uuid
),
history AS (
    SELECT stage_key,
           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_seconds)
               AS p95,
           COUNT(*) AS samples
    FROM ranked_history
    WHERE rn <= %s
    GROUP BY stage_key
)
SELECT t.stage_key,
       t.phase,
       t.duration_seconds::float8 AS duration_seconds,
       h.p95::float8 AS p95,
       COALESCE(h.samples, 0)::int AS samples
FROM this_run t
LEFT JOIN history h USING (stage_key)
ORDER BY t.phase, t.stage_key
"""


class PipelinePhaseService:
    def __init__(self, db_adapter):
        self._db = db_adapter

    def get_phases_for_run(self, run_id: UUID) -> Optional[Dict[str, Any]]:
        run_id_str = str(run_id)
        meta_rows = self._db.execute_query(
            _RUN_META_SQL, (run_id_str,), fetch=True,
        ) or []
        if not meta_rows:
            return None

        run_status = meta_rows[0]["status"]

        stage_rows = self._db.execute_query(
            _STAGES_WITH_HISTORY_SQL,
            (run_id_str, run_id_str, WINDOW_RUNS),
            fetch=True,
        ) or []

        stages_by_phase: Dict[str, List[Dict[str, Any]]] = {
            k: [] for k in PHASES
        }
        for row in stage_rows:
            phase = row["phase"]
            if phase in stages_by_phase:
                stages_by_phase[phase].append(row)

        phases_out: List[Dict[str, Any]] = []
        for phase_key, meta in sorted(
            PHASES.items(), key=lambda kv: kv[1]["order"]
        ):
            rows = stages_by_phase[phase_key]
            total = round(sum(r["duration_seconds"] for r in rows), 3)

            sub_stages = []
            has_slow = False
            for r in rows:
                p95 = r.get("p95")
                samples = r.get("samples") or 0
                duration = r["duration_seconds"]
                is_slow = bool(
                    p95
                    and p95 > 0
                    and samples >= MIN_SAMPLES
                    and duration > SLOW_RATIO * p95
                )
                if is_slow:
                    has_slow = True
                sub_stages.append({
                    "stage_key": r["stage_key"],
                    "duration_seconds": round(duration, 3),
                    "p95_seconds": round(float(p95), 3) if p95 else None,
                    "samples": samples,
                    "is_slow": is_slow,
                })

            phases_out.append({
                "key": phase_key,
                "label": meta["label"],
                "duration_seconds": total,
                "status": _phase_status(run_status, phase_key, rows),
                "sub_stages": sub_stages,
                "has_slow_stages": has_slow,
            })

        return {
            "run_id": run_id_str,
            "status": run_status,
            "phases": phases_out,
        }


def _expected_stage_count(phase_key: str) -> int:
    return sum(1 for p in STAGE_TO_PHASE.values() if p == phase_key)


def _phase_status(
    run_status: str, phase_key: str, rows: List[Dict[str, Any]]
) -> str:
    rows_count = len(rows)
    if run_status == "completed":
        return "success"
    if run_status in ("pending", "running"):
        return "running"
    if run_status == "failed":
        if rows_count == 0:
            return "not_run"
        if rows_count >= _expected_stage_count(phase_key):
            return "success"
        return "failed"
    return "unknown"
