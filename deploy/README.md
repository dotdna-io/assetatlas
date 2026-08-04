# deploy/

This directory contains everything needed to BUILD, INSTALL, and OPERATE AssetAtlas.
The installer system was overhauled in v2026.18.

**If you are an operator** (installing or updating in production), use the
one-line installer — `curl -fsSL https://get.assetatlas.com | sudo bash` — and
`assetatlas-cli` on the host. This README is for developers modifying the
installer itself.

---

## Layout

| Subdir | Purpose |
|---|---|
| `installer/` | Operator-facing scripts shipped inside artifacts: `install.sh`, `update.sh`, `assetatlas-cli`. |
| `build/` | Build scripts that produce installer artifacts — run in CI on tag push, or locally. |
| `lib/` | Shared bash library sourced by installer + build scripts. One file per responsibility. |
| `compose/` | `docker-compose.yml` + monitoring overlay. App image refs default to ghcr.io; offline mode overrides them via env vars. |
| `systemd/` | Unit files copied to `/etc/systemd/system/` by `install.sh` on systemd hosts. |

---

## lib/ — one file per responsibility

| File | Responsibility |
|---|---|
| `common.sh` | Logging helpers (`log_info`, `log_ok`, `log_warn`, `die`), interactive prompts, audit log. |
| `runtime.sh` | Docker / Podman detection (`detect_runtime`), sets `$RUNTIME` and `$COMPOSE_CMD`. |
| `version.sh` | `YYYY.WW.N` version parser, `system_info` table CRUD (`get_installed_version`, `update_system_info`). |
| `env.sh` | `.env` and `.install-state` read/write helpers (`state_get`, `state_set`, `env_set`). |
| `preflight.sh` | Pre-flight checks: disk, RAM, open ports, OS compatibility, cert expiry, in-flight pipeline, and `jq` where the container health check needs it (`check_jq` — the one hard tool requirement here, everything else degrades; `SKIP_JQ_CHECK=1` overrides). |
| `migrate.sh` | `apply_migrations` (SQL files in `database/migrations/`). |
| `backup.sh` | `backup_db` (pg_dump), `restore_db`, and two separate retentions: `prune_backups` for operator/nightly `backup-*` dumps, `prune_pre_update_backups` for the `pre-update-<version>` rollback snapshots. Keep them apart — sharing one queue let nightlies evict the snapshot `--rollback` restores by exact filename. |
| `rollback.sh` | `snapshot_state` (save .env + compose + image tags + the pre-update container baseline), `restore_snapshot`. |
| `health.sh` | `wait_for_health` (polls `/api/health/live`), `wait_for_db`, `wait_for_all_containers_healthy`, `capture_container_baseline` (records which containers were already unhealthy, so only regressions fail an update). |
| `smtp.sh` | Seeds `system_settings.email` at install time from `--smtp-*` flags. |
| `support.sh` | `collect_support_bundle` — gathers logs, state, redacted env into a tarball. |

All lib files are sourced, not executed. They define functions only — no side effects at
source time.

---

## build/ — artifact producers

| Script | Output | Size | When to run |
|---|---|---|---|
| `build-online.sh [output-path]` | `assetatlas-online-VERSION.sh` | ~100 KB | Any release; app images pulled from ghcr.io at install time |
| `build-offline.sh [output-path]` | `assetatlas-offline-VERSION.tar.gz` | ~3 GB | Air-gapped targets; all images bundled |
| `build-monitoring-pack.sh` | `assetatlas-monitoring-images-VERSION.tar.gz` | ~300-400 MB | When monitoring overlay images change |
| `self-extract-header.sh` | (helper, not called directly) | — | Sourced by `build-online.sh` to prepend the self-extract header |

CI calls `build-online.sh` and `build-offline.sh` on every version tag push
(see `.github/workflows/release.yml`). The monitoring pack is built separately
and attached to releases as additional assets.

---

## Where to add things

**A new preflight check** (e.g. minimum kernel version): add to `lib/preflight.sh`.

**A new `assetatlas-cli` subcommand**: add a `cmd_<name>()` function in
`installer/assetatlas-cli` and a case entry in the main dispatch block.

**A new install-time option** (e.g. `--with-backup-s3`): add the flag to `installer/install.sh`,
persist the choice via `state_set` to `.install-state`, and handle it in
`installer/assetatlas-cli` for day-2 operations.

**A new shared helper**: add it to the appropriate `lib/` file. If no file fits, create a new
`lib/<responsibility>.sh` and source it from `install.sh`, `update.sh`, and `assetatlas-cli`.

**A new optional add-on** (e.g. a SIEM forwarder): follow the monitoring pack pattern —
a compose overlay in `compose/`, a build script in `build/`, and `enable`/`disable` handlers
in `assetatlas-cli`.

---

## Testing changes locally

### Shellcheck (CI enforced)

```bash
shellcheck deploy/installer/install.sh
shellcheck deploy/installer/update.sh
shellcheck deploy/installer/assetatlas-cli
shellcheck deploy/lib/*.sh
shellcheck deploy/build/*.sh
```

## Building artifacts locally

```bash
# Online installer (fast, ~1 min)
./deploy/build/build-online.sh dist/assetatlas-online-$(cat VERSION).sh

# Offline bundle (slow, ~30-60 min — builds all Docker images)
./deploy/build/build-offline.sh dist/assetatlas-offline-$(cat VERSION).tar.gz

# Monitoring image pack
./deploy/build/build-monitoring-pack.sh
```

Artifacts land in `dist/` by default. Set `SKIP_IMAGE_BUILD=1` or `SKIP_VENDOR_PULL=1`
(offline script) to speed up iteration when images haven't changed.

---

## Architecture invariants

These won't change without epic-level discussion:

- **Two distribution artifacts** (online `.sh`, offline `.tar.gz`) sharing one installer
  body. The `INSTALL_MODE` file inside the artifact selects the image source.
- **Runtime detection** (`lib/runtime.sh`): Docker checked first, Podman fallback. Compose
  command set accordingly. Never hard-code `docker compose`.
- **Atomic update with auto-rollback**: snapshot before any destructive change; health-check
  window after compose-up; auto-restore on failure. Manual rollback via `--rollback`.
- **Single install directory** (`/opt/assetatlas` default): all state, backups, snapshots,
  and compose files live under one tree. No files scattered across `/etc` except systemd units.
- **Idempotent install**: re-running `install.sh` on an existing install dispatches to
  `update.sh`. No separate "reinstall" concept.

---

