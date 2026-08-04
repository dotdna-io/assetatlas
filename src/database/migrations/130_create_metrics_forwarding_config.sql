-- SPDX-License-Identifier: AGPL-3.0-or-later
-- src/database/migrations/130_create_metrics_forwarding_config.sql
-- Metrics forwarding runtime config (#922 Phase 2b): the customer Prometheus-native
-- remote_write target for the opt-in metrics-forwarder agent (Phase 2a). A SINGLE-ROW
-- table (id pinned to 1) — the API renders this into the agent's shared config file and
-- hot-reloads the agent via POST /-/reload, so ops can retarget forwarding from
-- Settings -> Monitoring without editing env/compose. The bearer token / basic-auth
-- password is Fernet-encrypted at rest (auth_secret), mirroring alert_channels; the API
-- never returns it in plaintext (masked to a has_secret boolean on read).
BEGIN;

CREATE TABLE IF NOT EXISTS metrics_forwarding_config (
    -- Single-row table: id is pinned to 1 so there is exactly one config row.
    id              INTEGER PRIMARY KEY DEFAULT 1,
    endpoint_url    TEXT,
    auth_type       TEXT NOT NULL DEFAULT 'none',    -- none | bearer | basic
    auth_secret     TEXT,                            -- ENC:-encrypted bearer token or basic password
    username        TEXT,                            -- basic-auth username (plaintext; not a secret)
    external_labels JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled         BOOLEAN NOT NULL DEFAULT false,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      TEXT,
    CONSTRAINT metrics_forwarding_config_singleton CHECK (id = 1),
    CONSTRAINT metrics_forwarding_config_auth_type CHECK (auth_type IN ('none', 'bearer', 'basic'))
);

-- Seed the singleton row so the API always reads a config (disabled by default).
INSERT INTO metrics_forwarding_config (id, auth_type, enabled)
VALUES (1, 'none', false)
ON CONFLICT (id) DO NOTHING;

COMMIT;
