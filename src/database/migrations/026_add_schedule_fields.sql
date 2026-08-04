-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add scheduler fields to integrations table
-- Date: 2025-11-04
-- Description: Adds schedule configuration fields to enable automated execution

-- Add schedule fields
ALTER TABLE integrations
ADD COLUMN IF NOT EXISTS schedule_enabled BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS schedule_cron VARCHAR(100),
ADD COLUMN IF NOT EXISTS schedule_timezone VARCHAR(50) DEFAULT 'UTC',
ADD COLUMN IF NOT EXISTS next_run_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS is_running BOOLEAN NOT NULL DEFAULT FALSE;

-- Add index for efficient scheduler queries
CREATE INDEX IF NOT EXISTS idx_integrations_schedule
ON integrations(schedule_enabled, next_run_at)
WHERE schedule_enabled = TRUE;

-- Add comment
COMMENT ON COLUMN integrations.schedule_enabled IS 'Whether automated scheduling is enabled';
COMMENT ON COLUMN integrations.schedule_cron IS 'Cron expression for schedule (e.g., "0 * * * *" for hourly)';
COMMENT ON COLUMN integrations.schedule_timezone IS 'Timezone for schedule evaluation';
COMMENT ON COLUMN integrations.next_run_at IS 'Calculated next run time';
COMMENT ON COLUMN integrations.is_running IS 'Whether integration is currently executing';

-- Verification query
SELECT
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_name = 'integrations'
    AND column_name IN (
        'schedule_enabled',
        'schedule_cron',
        'schedule_timezone',
        'next_run_at',
        'is_running'
    )
ORDER BY column_name;
