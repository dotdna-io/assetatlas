-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Add connection test tracking fields to integrations table
-- Date: 2025-11-10
-- Description: Adds fields to track connection test results performed during setup

-- Add connection test fields
ALTER TABLE integrations
ADD COLUMN IF NOT EXISTS last_connection_test_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS last_connection_test_status TEXT,
ADD COLUMN IF NOT EXISTS last_connection_test_message TEXT;

-- Add index for efficient queries
CREATE INDEX IF NOT EXISTS idx_integrations_connection_test
ON integrations(last_connection_test_at)
WHERE last_connection_test_at IS NOT NULL;

-- Add comments
COMMENT ON COLUMN integrations.last_connection_test_at IS 'Timestamp of the most recent connection test';
COMMENT ON COLUMN integrations.last_connection_test_status IS 'Status of last connection test: success, error';
COMMENT ON COLUMN integrations.last_connection_test_message IS 'Message or error details from last connection test';

-- Add check constraint for valid status values
ALTER TABLE integrations
ADD CONSTRAINT integrations_connection_test_status_check
CHECK (last_connection_test_status IS NULL OR last_connection_test_status IN ('success', 'error'));

-- Verification query
SELECT
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_name = 'integrations'
    AND column_name IN (
        'last_connection_test_at',
        'last_connection_test_status',
        'last_connection_test_message'
    )
ORDER BY column_name;
