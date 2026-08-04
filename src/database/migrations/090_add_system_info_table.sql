-- SPDX-License-Identifier: AGPL-3.0-or-later
-- 090_add_system_info_table.sql
-- Tracks installed system version, install metadata, and opt-ins.
-- Single-row table (enforced by CHECK on id=1).
BEGIN;

CREATE TABLE IF NOT EXISTS system_info (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    installed_version   TEXT NOT NULL,
    installed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    runtime             TEXT NOT NULL CHECK (runtime IN ('docker', 'podman')),
    install_mode        TEXT NOT NULL CHECK (install_mode IN ('online', 'offline')),
    opt_in_monitoring   BOOLEAN NOT NULL DEFAULT FALSE,
    opt_in_llm          BOOLEAN NOT NULL DEFAULT FALSE,
    llm_model           TEXT,
    install_dir         TEXT NOT NULL,
    install_log_path    TEXT NOT NULL DEFAULT '/var/log/assetatlas/install.log'
);

-- Seed a placeholder row that update.sh will overwrite on first run after this migration.
-- Use a sentinel value so first-run detection works.
INSERT INTO system_info (id, installed_version, runtime, install_mode, install_dir)
VALUES (1, '0.0.0-pre-system-info', 'docker', 'online', '/opt/assetatlas')
ON CONFLICT (id) DO NOTHING;

COMMIT;
