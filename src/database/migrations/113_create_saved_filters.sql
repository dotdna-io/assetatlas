-- SPDX-License-Identifier: AGPL-3.0-or-later
-- src/database/migrations/113_create_saved_filters.sql
-- Saved Explorer filters: per-user named view snapshots, with a visibility
-- model and per-user share grants. Phase 1 uses only owner-scoped private
-- access; 'link'/'org' visibility and grants are consumed in later phases.
BEGIN;

CREATE TABLE IF NOT EXISTS saved_filters (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    payload     JSONB NOT NULL,
    visibility  TEXT NOT NULL DEFAULT 'private'
                CHECK (visibility IN ('private', 'link', 'org')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ
);

DROP TRIGGER IF EXISTS saved_filters_updated_at ON saved_filters;
CREATE TRIGGER saved_filters_updated_at
    BEFORE UPDATE ON saved_filters
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE INDEX IF NOT EXISTS idx_saved_filters_owner
    ON saved_filters (owner_id, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_saved_filters_org
    ON saved_filters (updated_at DESC)
    WHERE visibility = 'org' AND deleted_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_saved_filters_owner_name
    ON saved_filters (owner_id, lower(name))
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS saved_filter_grants (
    saved_filter_id UUID NOT NULL REFERENCES saved_filters(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    granted_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (saved_filter_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_saved_filter_grants_user
    ON saved_filter_grants (user_id);

COMMIT;
