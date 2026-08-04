-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration 099: Persist per-run, per-stage pipeline durations
--
-- The asset-generation orchestrator already logs `stats.stages` at the end of
-- a run, but those numbers vanish into stdout. To answer "is the pipeline
-- healthy and where is time going?" we need a queryable history so the
-- Settings → Pipeline view can compare a run's sub-stages against a rolling
-- baseline (last 30 runs, p95).
--
-- TimescaleDB hypertable on `started_at` with a 90-day retention policy —
-- enough history to compute a stable baseline without unbounded growth.
--
-- Idempotent: safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS pipeline_stage_durations (
    run_id           uuid        NOT NULL,
    stage_key        text        NOT NULL,
    phase            text        NOT NULL,
    duration_seconds numeric     NOT NULL,
    started_at       timestamptz NOT NULL,
    PRIMARY KEY (run_id, stage_key, started_at)
);

-- Convert to hypertable. `migrate_data` only matters if rows already exist
-- (e.g. re-running this migration after a partial earlier attempt).
SELECT create_hypertable(
    'pipeline_stage_durations',
    'started_at',
    if_not_exists => TRUE,
    migrate_data  => TRUE
);

-- 90-day retention — phase view reads the last 30 runs for baselines, and
-- a daily pipeline produces ~30 runs in a month, so 90 days is a healthy
-- cushion without unbounded growth.
SELECT add_retention_policy(
    'pipeline_stage_durations',
    INTERVAL '90 days',
    if_not_exists => TRUE
);

-- Lookups: "p95 of stage X over last 30 runs" → (stage_key, started_at DESC).
CREATE INDEX IF NOT EXISTS idx_psd_stage_started
    ON pipeline_stage_durations (stage_key, started_at DESC);

COMMIT;
