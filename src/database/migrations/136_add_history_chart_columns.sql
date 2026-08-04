-- SPDX-License-Identifier: AGPL-3.0-or-later
-- 136_add_history_chart_columns.sql
-- Reporting: full historical support for OS-EOL / Tag / Posture chart fields (#1024).
-- assets_history (migration 006 + later ALTERs) never mirrored these columns, so the
-- history_trend charts (Line / Metric Spark / Metric Bar / Chronicle Bar) could not
-- filter or split by them and rejected the attempt with a fail-loud 400. Add the
-- columns so the pipeline archive step can populate them from now on. No backfill:
-- history fills forward per run, so pre-existing snapshots read empty/NULL for these.
BEGIN;

-- OS end-of-life tracking (mirrors assets, migration 021). Plain columns, matching
-- how migration 021 added os_eol_date/os_eol_status to asset_providers_history
-- (no CHECK constraint or indexes on the append-only history table).
ALTER TABLE assets_history ADD COLUMN IF NOT EXISTS os_eol_status VARCHAR(50);
ALTER TABLE assets_history ADD COLUMN IF NOT EXISTS os_eol_date DATE;
ALTER TABLE assets_history ADD COLUMN IF NOT EXISTS os_is_lts BOOLEAN;

-- Tag map (mirrors assets, migration 049) and per-asset posture state (migration 064).
-- DEFAULT '{}' so pre-existing history rows read as an empty object rather than NULL.
ALTER TABLE assets_history ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '{}'::jsonb;
ALTER TABLE assets_history ADD COLUMN IF NOT EXISTS posture_rules JSONB DEFAULT '{}'::jsonb;

COMMIT;
