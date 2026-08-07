#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/installer/install.sh — unified install entry point.
# Replaces deploy/install.sh and deploy/online/install.sh.
#
# Usage:
#   install.sh [--unattended] [--install-dir PATH] [--install-mode online|offline]
#              [--system]                # accepted no-op: system-wide is the only
#                                        # mode (--rootless was removed; passing it is an error)
#              [--with-monitoring | --no-monitoring]
#              [--no-systemd]
#              [--install-docker]   # install Docker if no runtime is present
#              [--no-reference-data] # skip seeding MAC-vendor / OS-EOL data
#              [--with-demo-data]   # seed sample integrations for testing

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$SCRIPT_DIR/../lib"

# shellcheck source=../lib/common.sh
source "$LIB_DIR/common.sh"

# INSTALL_DIR first, before any OTHER lib is sourced — not just before the
# pre-parse loop below. env.sh and preflight.sh (sourced further down) each
# carry their own defensive `: "${INSTALL_DIR:=/opt/assetatlas}"` so that
# sourcing them stand-alone doesn't crash under `set -u`; that default fires
# the moment the file is sourced, not lazily. Running this any later would let
# one of those claim INSTALL_DIR first (`${INSTALL_DIR:-...}` cannot override
# an already-non-empty variable).
#
# The rootless install mode shipped in v2026.32.1 and was removed again:
# system-wide is the only install this script will start. --rootless is
# REFUSED rather than ignored: silently installing system-wide under a flag
# that promises something else is worse than an error. --system stays accepted
# as a no-op so existing docs and automation keep working. The scan runs here
# rather than only in the main parser for the same reason --install-dir is
# pre-parsed further down: the dispatch to update.sh hands over the FULL argv,
# and the refusal must fire before it.
for _arg in "$@"; do
    case "$_arg" in
        --rootless)
            die "the rootless install mode was removed. AssetAtlas now installs system-wide only — re-run as root or under sudo, without --rootless." ;;
        --system) ;;
    esac
done
# The mode only ever supplied a DEFAULT: an explicit INSTALL_DIR or
# --install-dir outranks it (parsed below), exactly as before.
INSTALL_DIR="${INSTALL_DIR:-/opt/assetatlas}"
# Exported HERE, not only in the later "export INSTALL_DIR ..." line below the
# flag parser: an existing install is detected and dispatched to update.sh via
# `exec` further down, BEFORE that later export runs — and exec replaces the
# process image with only the CURRENT environment, so a plain shell variable
# does not cross it.
export INSTALL_DIR

# shellcheck source=../lib/runtime.sh
source "$LIB_DIR/runtime.sh"
# shellcheck source=../lib/env.sh
source "$LIB_DIR/env.sh"
# shellcheck source=../lib/version.sh
source "$LIB_DIR/version.sh"
# shellcheck source=../lib/preflight.sh
source "$LIB_DIR/preflight.sh"
# shellcheck source=../lib/health.sh
source "$LIB_DIR/health.sh"
# shellcheck source=../lib/migrate.sh
source "$LIB_DIR/migrate.sh"
# shellcheck source=../lib/smtp.sh
source "$LIB_DIR/smtp.sh"
# shellcheck source=../lib/update_check.sh
source "$LIB_DIR/update_check.sh"
# shellcheck source=../lib/systemd.sh
source "$LIB_DIR/systemd.sh"
# shellcheck source=../lib/reference_data.sh
source "$LIB_DIR/reference_data.sh"

# Defaults (flags override)
UNATTENDED=0
INSTALL_MODE=""           # auto-detected from artifact type; flag can override
WITH_MONITORING=""        # "" = ask; "1" = yes; "0" = no
NO_SYSTEMD=0
WITH_DEMO_DATA=0          # "1" = seed database/demo/*.sql at install time
INSTALL_DOCKER=0          # "1" = install Docker without prompting when absent
NO_REFERENCE_DATA=0       # "1" = do not seed MAC-vendor / OS-EOL reference data

# An existing install means this is really an update: hand the FULL argument
# list to update.sh *before* the install arg-parser runs, so update-only flags
# (e.g. --allow-version-skip) reach it instead of being rejected here as an
# "Unknown flag". Resolve --install-dir first so we check the right location;
# update.sh parses the rest.
_resolved_install_dir="$INSTALL_DIR"
_prev_arg=""
for _arg in "$@"; do
    [ "$_prev_arg" = "--install-dir" ] && _resolved_install_dir="$_arg"
    _prev_arg="$_arg"
done
if [ -f "$_resolved_install_dir/.install-state" ]; then
    log_warn "Existing install detected at $_resolved_install_dir; running update.sh instead."
    # An existing install means the freshly-extracted bundle is already in place;
    # apply it locally rather than having update.sh fetch a second copy.
    # --confirm because the operator asked to *install*: without it this
    # dispatch is an unattended full update (backup, migrations, pull,
    # recreate). It makes update.sh ask [u/d/c] first; --unattended / -y
    # forwarded in "$@" still proceed without prompting.
    exec "$SCRIPT_DIR/update.sh" --apply-local --confirm "$@"
fi

while [ $# -gt 0 ]; do
    case "$1" in
        --unattended)        UNATTENDED=1 ;;
        # Unreachable — the pre-parse above already refused it — and kept as a
        # second refusal in case that scan is ever narrowed.
        --rootless)          die "the rootless install mode was removed. AssetAtlas now installs system-wide only." ;;
        --system)            ;;   # no-op: system-wide is the only mode
        --install-dir)       INSTALL_DIR="$2"; shift ;;
        --install-mode)      INSTALL_MODE="$2"; shift ;;
        --with-monitoring)   WITH_MONITORING=1 ;;
        --no-monitoring)     WITH_MONITORING=0 ;;
        --no-systemd)        NO_SYSTEMD=1 ;;
        --install-docker)    INSTALL_DOCKER=1 ;;
        --no-reference-data) NO_REFERENCE_DATA=1 ;;
        --with-demo-data)    WITH_DEMO_DATA=1 ;;
        -h|--help)           sed -n '2,16p' "$0"; exit 0 ;;
        *) die "Unknown flag: $1" ;;
    esac
    shift
done

export INSTALL_DIR UNATTENDED INSTALL_DOCKER NO_REFERENCE_DATA

# The ONE version resolution for this script. The banner below, the app-image
# pins the online branch writes at image-setup time, and the system_info
# record at the end all describe the same thing — the bundle being installed —
# so they share this result instead of each carrying their own fallback chain
# (the banner and system_info used to resolve it independently, with two
# DIFFERENT chains). $INSTALL_DIR/VERSION is a valid first choice at every
# point below, image setup included: the self-extracting bundle untars its
# whole tree — VERSION included — into $INSTALL_DIR before it execs this
# script (deploy/build/self-extract-header.sh). From a repo checkout there is
# no $INSTALL_DIR/VERSION on a fresh install, and the repo-root VERSION
# answers instead.
BUNDLE_VERSION=$(cat "$INSTALL_DIR/VERSION" 2>/dev/null \
    || cat "$SCRIPT_DIR/../../VERSION" 2>/dev/null \
    || true)

# No-op when the curl|bash bootstrap already greeted the operator (it exports
# ASSETATLAS_BANNER_SHOWN), so the one-liner path shows the logo exactly once.
print_banner "$BUNDLE_VERSION"

log_info "=== AssetAtlas install starting ==="

# ---------------------------------------------------------------------------
# Detect runtime (sets RUNTIME, COMPOSE_CMD)
# ---------------------------------------------------------------------------
# ensure_ rather than detect_: on a clean host, dying with "Container runtime not
# available." was correct and useless — installing Docker is what the operator
# was going to do next anyway. Offers to do it, and distinguishes a declined
# offer (a decision: print instructions, exit 0) from a genuine failure.
_rt_status=0
ensure_container_runtime || _rt_status=$?
case "$_rt_status" in
    0) ;;
    2) log_info "Re-run this installer once a container runtime is available."
       exit 0 ;;
    *) die "Container runtime not available." ;;
esac

# PSQL_CMD set after compose-up; declare now so later sourced libs see the var.
PSQL_CMD=""
PG_DUMP_CMD=""
export PSQL_CMD PG_DUMP_CMD

# ---------------------------------------------------------------------------
# Resolve INSTALL_MODE from bundle marker or flag
# ---------------------------------------------------------------------------
# The bundle scripts write a plain text file "INSTALL_MODE" next to install.sh.
BUNDLE_MODE_FILE="$SCRIPT_DIR/INSTALL_MODE"
if [ -z "$INSTALL_MODE" ]; then
    if [ -f "$BUNDLE_MODE_FILE" ]; then
        INSTALL_MODE=$(cat "$BUNDLE_MODE_FILE")
        log_info "Install mode from bundle: $INSTALL_MODE"
    else
        # Default to online when no bundle marker (running from repo checkout).
        INSTALL_MODE="online"
        log_info "Install mode: online."
    fi
fi
case "$INSTALL_MODE" in
    online|offline) ;;
    *) die "Invalid install mode: $INSTALL_MODE (must be online or offline)" ;;
esac

# ---------------------------------------------------------------------------
# Locate compose dir and validate compose file exists
# ---------------------------------------------------------------------------
# When running from a bundle: compose files live adjacent to install.sh (../compose/).
# When running from a repo checkout: they live in deploy/compose/.
COMPOSE_DIR="$SCRIPT_DIR/../compose"
if [ ! -d "$COMPOSE_DIR" ]; then
    die "Compose directory not found: $COMPOSE_DIR"
fi
COMPOSE_BASE_FILE="$COMPOSE_DIR/docker-compose.yml"
COMPOSE_MON_FILE="$COMPOSE_DIR/docker-compose.monitoring.yml"
[ -f "$COMPOSE_BASE_FILE" ] || die "Base compose file not found: $COMPOSE_BASE_FILE"

# ---------------------------------------------------------------------------
# Fix up directory paths BEFORE any lib function that uses them
# ---------------------------------------------------------------------------
BACKUP_DIR="$INSTALL_DIR/backups"
SNAPSHOT_DIR="$INSTALL_DIR/snapshots"
INSTALL_STATE_PATH="$INSTALL_DIR/.install-state"
export BACKUP_DIR SNAPSHOT_DIR INSTALL_STATE_PATH

# ---------------------------------------------------------------------------
# Pre-flight (uses HTTPS_PORT / HTTP_PORT defaults — real values collected next)
# Declared here with their defaults so preflight can check port availability.
# ---------------------------------------------------------------------------
HTTPS_PORT="${HTTPS_PORT:-8443}"
HTTP_PORT="${HTTP_PORT:-8080}"
preflight_install

# ---------------------------------------------------------------------------
# Collect configuration (interactively or via defaults in unattended mode)
# ---------------------------------------------------------------------------
log_info "Collecting configuration..."

# DOMAIN — the environment supplies the default, like DATA_DIR and the ports
# below: prompt_value returns its default verbatim under --unattended, so a
# default hardcoded here was unreachable non-interactively and every unattended
# install called itself `localhost` whatever host it was on (#1219). That value
# is not one the operator can correct afterwards — it is the self-signed cert's
# CN and subjectAltName, and it seeds ASSETATLAS_PUBLIC_BASE_URL and the
# SMTP_FROM / SMTP_FRONTEND_URL defaults, so the emails carry links pointing at
# the recipient's own machine.
#
# The validation below is deliberately NOT inside prompt_value: it runs on
# whatever that call returns, so an environment-supplied value is checked on the
# same path as a typed one. It is a whitelist, and a strictly tighter one than
# the blacklist write_env_file re-applies to DOMAIN — no second guard is needed
# here the way DATA_DIR needed one.
_prompt_domain() {
    local val
    val=$(prompt_value "Domain / hostname (e.g. assetatlas.example.com)" "${DOMAIN:-localhost}")
    # Validate: alphanumeric, dots, dashes, colons (for host:port combos)
    case "$val" in
        *[!\*a-zA-Z0-9._:-]*) die "DOMAIN contains invalid characters: $val" ;;
    esac
    # Must not contain shell-unsafe characters (write_env_file's precondition)
    case "$val" in
        *'$'*|*'`'*|*'\'*) die "DOMAIN contains shell-unsafe characters: $val" ;;
    esac
    printf '%s' "$val"
}
DOMAIN=$(_prompt_domain)

# DATA_DIR — where persistent volumes land. The environment supplies the
# default, like the ports below: prompt_value returns its default verbatim under
# --unattended, so a default hardcoded here was unreachable non-interactively and
# every unattended install on a host shared one Postgres data directory whatever
# --install-dir said (#1200).
DATA_DIR=$(prompt_value "Data directory (persistent volumes)" "${DATA_DIR:-/opt/assetatlas/data}")
# write_env_file emits DATA_DIR=<value> UNQUOTED and update.sh sources that file,
# so `$` and a backtick are far from the only live characters: `/srv/aa;reboot`
# is an ordinary-looking value that runs `reboot` on the next update, a quote
# leaves the file unparseable so every later update dies on `. .env`, and
# whitespace truncates the value at the space and runs the rest. Taken whole
# from resolve_admin_password's guard in lib/env.sh rather than trimmed to the
# provably-live subset — and that value at least gets written single-quoted.
case "$DATA_DIR" in
    *'$'*|*'`'*|*'\'*|*';'*|*'&'*|*'|'*|*'('*|*')'*|*'<'*|*'>'*|*'#'*|*"'"*|*'"'*)
        die "DATA_DIR contains shell-unsafe characters: $DATA_DIR" ;;
    *[[:space:]]*)
        die "DATA_DIR must not contain whitespace: $DATA_DIR" ;;
    # Absolute, because the two consumers resolve a relative value against
    # different directories: prepare_data_dirs mkdir -p's it against this
    # script's CWD, while compose resolves a relative bind-mount source against
    # the project directory ($INSTALL_DIR). A relative DATA_DIR therefore
    # pre-creates one set of directories and lets Docker auto-create the real
    # ones as root:root — the uid-1000 "Permission denied" on /app/logs that
    # prepare_data_dirs exists to prevent. Typing one at the prompt was always
    # possible; handing one in from automation is far more likely.
    /*) ;;
    *)  die "DATA_DIR must be an absolute path: $DATA_DIR" ;;
esac

# The pre-flight above measured INSTALL_DIR, and it had to: DATA_DIR is not known
# until the line that just ran. But Postgres, chromadb and every container log
# land HERE, so on a host where the two are different mounts the 20 GB check that
# passed said nothing about the volume that actually fills (#1220) — and DATA_DIR
# taking its default from the environment (#1216) is precisely the case where
# they differ. This is the first point at which the value is final, so this is
# where the check goes; the rest of the list stays in preflight_install, ahead of
# the prompts, so an operator on an unsupported OS is told so before answering
# five questions.
#
# check_data_dir_disk creates nothing, which is what lets it sit on this side of
# the prompts at all: everything between here and prepare_data_dirs runs
# unstubbed under test_install_dispatch.bats' harness, and a probe that made the
# directory would put /opt/assetatlas/data on every host that runs the suite.
# Same threshold as preflight_install's check_disk, so INSTALLER_MIN_DISK_GB
# lowers both together — and the default below must stay equal to the one there.
# The same-filesystem collapse inside the check reports "covered by the check
# above"; raise this default alone and that line starts vouching for a threshold
# nothing ever applied.
check_data_dir_disk "${INSTALLER_MIN_DISK_GB:-20}" || die "Pre-install checks failed (DATA_DIR disk space)."

# Ports (re-prompt so operator can override from preflight defaults)
HTTPS_PORT=$(prompt_value "HTTPS port" "${HTTPS_PORT}")
HTTP_PORT=$(prompt_value "HTTP port" "${HTTP_PORT}")

# Monitoring opt-in
if [ -z "$WITH_MONITORING" ]; then
    if prompt_yn "Enable monitoring stack (Prometheus + Alertmanager + exporters)?" Y; then
        WITH_MONITORING=1
    else
        WITH_MONITORING=0
    fi
fi

# SMTP (optional — operator can also configure post-install via the admin UI)
EMAIL_ENABLED="false"
SMTP_HOST=""
SMTP_PORT="587"
SMTP_USER=""
SMTP_PASSWORD=""
SMTP_FROM=""
SMTP_SECURITY="starttls"
SMTP_DISPLAY_NAME="AssetAtlas"
SMTP_FRONTEND_URL=""
if prompt_yn "Configure SMTP email now? (you can also do this post-install in the admin UI)" N; then
    EMAIL_ENABLED="true"
    SMTP_HOST=$(prompt_value "SMTP host" "smtp.example.com")
    SMTP_PORT=$(prompt_value "SMTP port" "587")
    SMTP_SECURITY=$(prompt_choice "SMTP security" "starttls" "none" "starttls" "tls")
    SMTP_USER=$(prompt_value "SMTP username" "noreply@example.com")
    SMTP_PASSWORD=$(prompt_password "SMTP password" "")
    SMTP_FROM=$(prompt_value "From address" "noreply@${DOMAIN:-example.com}")
    SMTP_DISPLAY_NAME=$(prompt_value "From display name" "AssetAtlas")
    SMTP_FRONTEND_URL=$(prompt_value "Frontend URL (used in email links)" "https://${DOMAIN:-localhost}:${HTTPS_PORT:-8443}")
fi
export EMAIL_ENABLED SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD SMTP_FROM SMTP_SECURITY SMTP_DISPLAY_NAME SMTP_FRONTEND_URL

# Derive monitoring env vars
if [ "$WITH_MONITORING" = "1" ]; then
    MONITORING_ENABLED="true"
    ALERTMANAGER_WEBHOOK_SECRET=$(openssl rand -hex 16)
    PROMETHEUS_RETENTION="${PROMETHEUS_RETENTION:-30d}"
else
    MONITORING_ENABLED="false"
    ALERTMANAGER_WEBHOOK_SECRET=""
    PROMETHEUS_RETENTION="30d"
fi
export MONITORING_ENABLED ALERTMANAGER_WEBHOOK_SECRET PROMETHEUS_RETENTION

# ---------------------------------------------------------------------------
# Generate secrets
# ---------------------------------------------------------------------------
log_info "Generating secrets..."
generate_secrets
# Optional operator-set admin password (interactive only; --unattended auto-gens).
resolve_admin_password
# Save admin password before any potential exit so the operator sees it.
INSTALL_ADMIN_PASSWORD="$ADMIN_PASSWORD"

# ---------------------------------------------------------------------------
# Create install dir and write .env
# ---------------------------------------------------------------------------
mkdir -p "$INSTALL_DIR"
chmod 755 "$INSTALL_DIR"

export DOMAIN DATA_DIR HTTPS_PORT HTTP_PORT

# prepare_data_dirs: pre-create $DATA_DIR subdirs (logs chowned to uid 1000)
# so api/scheduler containers can write — Docker auto-creates missing
# bind-mount targets as root:root.
prepare_data_dirs

# Self-signed TLS for nginx if no cert is already in place.
generate_self_signed_cert

write_env_file

# ---------------------------------------------------------------------------
# Persist choices to .install-state
# ---------------------------------------------------------------------------
state_set install_mode     "$INSTALL_MODE"
state_set runtime          "$RUNTIME"
state_set with_monitoring  "$WITH_MONITORING"
state_set https_port       "$HTTPS_PORT"
state_set http_port        "$HTTP_PORT"
state_set domain           "$DOMAIN"
state_set data_dir         "$DATA_DIR"

# ---------------------------------------------------------------------------
# Copy compose files to INSTALL_DIR
# ---------------------------------------------------------------------------
log_info "Copying compose files to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp "$COMPOSE_BASE_FILE" "$INSTALL_DIR/docker-compose.yml"
if [ -f "$COMPOSE_MON_FILE" ]; then
    cp "$COMPOSE_MON_FILE" "$INSTALL_DIR/docker-compose.monitoring.yml"
fi

# Materialise the nginx config next to the compose file that mounts it. The
# bundle ships it staged under compose/nginx/ so that an UPDATE promotes the two
# together (see refresh_compose_files); a fresh install has to do the same
# promotion by hand, since update.sh is not involved.
# From a repo checkout there is no staging dir — the confs are at docker/nginx/.
NGINX_CONF_SRC="$COMPOSE_DIR/nginx"
[ -d "$NGINX_CONF_SRC" ] || NGINX_CONF_SRC="$SCRIPT_DIR/../../docker/nginx"
[ -d "$NGINX_CONF_SRC" ] || die "nginx config not found (looked in $COMPOSE_DIR/nginx and $SCRIPT_DIR/../../docker/nginx)"
promote_nginx_conf "$NGINX_CONF_SRC"

# Set COMPOSE_FILE to the installed copy going forward.
COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"
export COMPOSE_FILE

# ---------------------------------------------------------------------------
# Image setup
# ---------------------------------------------------------------------------
IMAGES_DIR="$SCRIPT_DIR/../images"

# The compose files whose images this install needs: the base file, plus the
# monitoring overlay when the operator opted into it. Built once and used for
# BOTH the pull and the verification below, so the check can never require an
# image the pull was never asked to fetch.
COMPOSE_IMAGE_FILES=("$COMPOSE_FILE")
COMPOSE_PULL_ARGS=(-f "$COMPOSE_FILE")
if [ "$WITH_MONITORING" = "1" ] && [ -f "$INSTALL_DIR/docker-compose.monitoring.yml" ]; then
    COMPOSE_IMAGE_FILES+=("$INSTALL_DIR/docker-compose.monitoring.yml")
    COMPOSE_PULL_ARGS+=(-f "$INSTALL_DIR/docker-compose.monitoring.yml")
fi

if [ "$INSTALL_MODE" = "offline" ]; then
    log_info "Offline mode: loading bundled images..."
    if [ ! -d "$IMAGES_DIR" ] || [ -z "$(ls -A "$IMAGES_DIR" 2>/dev/null)" ]; then
        die "Offline install requires bundled images in $IMAGES_DIR (none found)."
    fi
    for tar in "$IMAGES_DIR"/*.tar.gz "$IMAGES_DIR"/*.tar; do
        [ -f "$tar" ] || continue
        log_info "Loading image: $(basename "$tar")"
        if [[ "$tar" == *.gz ]]; then
            gunzip -c "$tar" | $RUNTIME load
        else
            $RUNTIME load -i "$tar"
        fi
    done
    # Override app image refs so compose picks up the locally-loaded images
    # rather than the ghcr.io defaults in docker-compose.yml.
    export API_IMAGE="localhost/assetatlas-api:latest"
    export SCHEDULER_IMAGE="localhost/assetatlas-scheduler:latest"
    export MCP_IMAGE="localhost/assetatlas-mcp:latest"
    export TEAMS_BRIDGE_IMAGE="localhost/assetatlas-teams-bridge:latest"
    export UI_IMAGE="localhost/assetatlas-ui:latest"
    # Persist the pins. These exports only live for the duration of this script,
    # but every LATER compose invocation re-renders the same file: the systemd
    # unit's ExecStart (`assetatlas-cli start`, run at boot and by the start
    # below), update.sh, `assetatlas-cli restart`. Without the pins in .env those
    # fall back to the ghcr.io defaults in docker-compose.yml and try to reach a
    # registry an air-gapped host cannot see.
    for _img_var in API_IMAGE SCHEDULER_IMAGE MCP_IMAGE TEAMS_BRIDGE_IMAGE UI_IMAGE; do
        _set_env_var "$_img_var" "${!_img_var}"
    done
    # An air-gapped host cannot recover from a missing image later, so say WHICH
    # one is missing here rather than letting the container fail and the health
    # poll time out 60 s from now. The exports above are in the environment, so
    # the app pins resolve to localhost/...; db, redis and nginx come from the
    # compose defaults and must match what `$RUNTIME load` named the vendor
    # tarballs (it normalises docker-archive RepoTags to docker.io/...).
    verify_compose_images "$COMPOSE_FILE" \
        || die "The bundle did not provide every image this stack needs (listed above). Re-extract the offline bundle into $INSTALL_DIR and re-run."
    # The monitoring images ship in a SEPARATE artifact
    # (assetatlas-monitoring-images-<version>.tar.gz, extracted into $IMAGES_DIR),
    # so their absence means one optional add-on is unavailable — not that this
    # install is broken. Warn and carry on; dying here would throw away a working
    # app install over an opt-in extra.
    if [ "${#COMPOSE_IMAGE_FILES[@]}" -gt 1 ]; then
        verify_compose_images "${COMPOSE_IMAGE_FILES[@]:1}" \
            || log_warn "Monitoring was requested but its images are not loaded. Extract assetatlas-monitoring-images-<version>.tar.gz into $IMAGES_DIR, then run: $INSTALL_DIR/installer/assetatlas-cli enable monitoring"
    fi
else
    log_info "Online mode: pulling images..."
    # Pin the five app images to the bundle version BEFORE the pull: without
    # these lines the compose defaults (${VAR:-ghcr.io/...:latest}) apply, so
    # every online install pulled — and kept running — :latest while
    # system_info recorded the bundle version, and update.sh's
    # set_app_image_pins had no line to rewrite on the next update (#1391).
    # Same shape as the offline branch above; the repo paths mirror the
    # ${VAR:-default}s in deploy/compose/docker-compose.yml.
    [ -n "$BUNDLE_VERSION" ] \
        || die "Cannot resolve the bundle version (neither $INSTALL_DIR/VERSION nor $SCRIPT_DIR/../../VERSION found) — refusing to install with unpinned images."
    log_info "Pinning app images to $BUNDLE_VERSION..."
    export API_IMAGE="ghcr.io/dotdna-io/assetatlas-api:$BUNDLE_VERSION"
    export SCHEDULER_IMAGE="ghcr.io/dotdna-io/assetatlas-scheduler:$BUNDLE_VERSION"
    export MCP_IMAGE="ghcr.io/dotdna-io/assetatlas-mcp:$BUNDLE_VERSION"
    export TEAMS_BRIDGE_IMAGE="ghcr.io/dotdna-io/assetatlas-teams-bridge:$BUNDLE_VERSION"
    export UI_IMAGE="ghcr.io/dotdna-io/assetatlas-ui:$BUNDLE_VERSION"
    # Persist the pins, for the same reason the offline branch does: these
    # exports die with this script, and every LATER compose invocation —
    # the systemd unit's ExecStart, update.sh, `assetatlas-cli restart` —
    # re-reads .env and would otherwise fall back to the moving :latest
    # defaults in docker-compose.yml.
    for _img_var in API_IMAGE SCHEDULER_IMAGE MCP_IMAGE TEAMS_BRIDGE_IMAGE UI_IMAGE; do
        _set_env_var "$_img_var" "${!_img_var}"
    done
    # The exports matter as much as the .env lines for THIS script:
    # verify_compose_images below expands ${VAR:-default} from the process
    # environment, not from .env — without them it would demand the :latest
    # images the pull was never asked to fetch.
    #
    # api, scheduler and mcp are the SAME image published under three names —
    # release.yml retags at manifest level, so there is no rebuild and no second
    # copy. Compose still renders a progress line per service, and `docker
    # images` afterwards reports the full size on three rows (its SIZE column is
    # a per-image total that includes shared layers). That reads as ~3x the real
    # transfer, so state the fact where the confusion happens.
    #
    # teams-bridge is NOT one of them (#1069): it is the internet-facing
    # container and builds from its own python:3.13-slim image, ~10% the size
    # and without the ML/data stack. It really does transfer separately, and the
    # operator can see it finish first — claiming otherwise sends them looking
    # for a bug in something they just watched work correctly.
    log_info "  api, scheduler and mcp are one image under three names:"
    log_info "  it transfers once; the other two reuse the layers already fetched."
    log_info "  teams-bridge is a separate, much smaller image and transfers on its own."
    # The monitoring overlay is pulled here too when it was opted into, rather
    # than left to `up -d` to fetch implicitly: it puts every download in the
    # step the operator is watching, and it is what lets the verification below
    # cover the overlay without demanding an image nothing was asked to fetch.
    $COMPOSE_CMD "${COMPOSE_PULL_ARGS[@]}" pull || die "Image pull failed. Check network and registry credentials."
    log_info "  ('$RUNTIME images' counts shared layers once per row; '$RUNTIME system df' shows real disk usage.)"
    # That `|| die` is not enough on its own: podman-compose 1.0.6 exits 0 having
    # failed to pull anything at all (see verify_compose_images in runtime.sh).
    # Without this the install proceeds to `up -d` and the operator's first
    # signal is an API health timeout 60 s later, pointing them at container logs
    # for what was a registry failure.
    verify_compose_images "${COMPOSE_IMAGE_FILES[@]}" \
        || die "The pull reported success but the images above are missing. Check network and registry credentials — and on Podman, that /etc/containers/registries.conf can resolve these names."
fi

# ---------------------------------------------------------------------------
# Bring up services (base + optional monitoring overlay)
# ---------------------------------------------------------------------------
# cadvisor sits behind the `privileged` profile in the monitoring overlay. This
# array is spliced into both compose invocations below.
#
# Gated on the overlay FILE as well as the opt-in, matching compose_overlay_args
# (the shared version update.sh and assetatlas-cli use): the only service behind
# this profile lives in that overlay, so with the file absent the flag selects
# nothing and merely hands the else-branch's base-only `up` an argument some
# podman-compose releases do not accept.
#
# ...and gated on RUNTIME=docker for the same reason compose_overlay_args is —
# see the long comment there. podman-compose only learned `--profile` in 1.1.0
# and the distro builds are older, so on a podman host the flag is an
# `unrecognized arguments` failure rather than a no-op; and cadvisor is
# configured for the Docker factory in any case, so a podman host has nothing to
# gain from it. Both call sites carry the identical condition on purpose: they
# build the same argv for the same containers, and one of them drifting is how
# an install and its first update disagree about what is in the project.
#
# RUNTIME comes from ensure_container_runtime above, which exports it or exits.
COMPOSE_PROFILE_ARGS=()
if [ "$WITH_MONITORING" = "1" ] \
    && [ -f "$INSTALL_DIR/docker-compose.monitoring.yml" ] \
    && [ "${RUNTIME:-}" = "docker" ]; then
    COMPOSE_PROFILE_ARGS+=(--profile privileged)
fi

# Same condition as COMPOSE_IMAGE_FILES above, and it has to stay that way: the
# image check verifies exactly the file set this `up` starts, so a copy that
# drifts either demands an image nothing runs or skips one that does.
if [ "$WITH_MONITORING" = "1" ] && [ -f "$INSTALL_DIR/docker-compose.monitoring.yml" ]; then
    log_info "Starting services with monitoring overlay..."
    $COMPOSE_CMD \
        "${COMPOSE_PROFILE_ARGS[@]}" \
        -f "$COMPOSE_FILE" \
        -f "$INSTALL_DIR/docker-compose.monitoring.yml" \
        up -d
else
    log_info "Starting services..."
    $COMPOSE_CMD "${COMPOSE_PROFILE_ARGS[@]}" -f "$COMPOSE_FILE" up -d
fi

# ---------------------------------------------------------------------------
# Wait for health: API endpoint, then all containers
# ---------------------------------------------------------------------------
HEALTH_URL="https://localhost:${HTTPS_PORT}/api/health/live"
wait_for_health "$HEALTH_URL" 60 || die "API health check timed out. Inspect: $COMPOSE_CMD -f $COMPOSE_FILE logs"
wait_for_all_containers_healthy 90 || die "Container health timed out. Inspect: $COMPOSE_CMD -f $COMPOSE_FILE ps"

# ---------------------------------------------------------------------------
# DB is now reachable — apply migrations and seeds
# ---------------------------------------------------------------------------
DB_USER="assetatlas"
DB_NAME="assetatlas"
PSQL_CMD="$RUNTIME exec -i assetatlas-db psql -U $DB_USER -d $DB_NAME"
PG_DUMP_CMD="$RUNTIME exec -i assetatlas-db pg_dump -U $DB_USER -d $DB_NAME"
export PSQL_CMD PG_DUMP_CMD DB_USER DB_NAME

MIGRATIONS_DIR="$INSTALL_DIR/database/migrations"
export MIGRATIONS_DIR

log_info "Applying database migrations..."
apply_migrations

# Bcrypt-hash the printed admin password, seed it, and activate the account.
# Migration 025 leaves admin in a locked state with an invalid hash, so without
# this step login is impossible.
log_info "Setting admin password..."
ADMIN_HASH=$(printf '%s' "$INSTALL_ADMIN_PASSWORD" | $RUNTIME exec -i assetatlas-api python -c \
    'import bcrypt, sys
print(bcrypt.hashpw(sys.stdin.buffer.read(), bcrypt.gensalt(rounds=12)).decode())') \
    || die "Could not hash admin password (api container missing bcrypt?)."
[ -n "$ADMIN_HASH" ] || die "Empty admin hash."
# ON_ERROR_STOP=1 is what makes the die below reachable: on stdin psql exits 0
# for a statement that failed, so a rejected upsert used to sail through and the
# summary printed working-looking `admin` credentials for an account that was
# never created.
$PSQL_CMD -v ON_ERROR_STOP=1 -v hash="$ADMIN_HASH" <<'SQL' >/dev/null \
    || die "Could not seed admin user in DB."
INSERT INTO users (username, email, password_hash, full_name, role, is_active, must_change_password, email_verified, created_at)
VALUES ('admin', 'admin@assetatlas.local', :'hash', 'System Administrator', 'super_admin', true, false, true, NOW())
ON CONFLICT (username) DO UPDATE SET
    password_hash = EXCLUDED.password_hash,
    is_active = true,
    must_change_password = EXCLUDED.must_change_password,
    updated_at = NOW();
SQL
log_ok "Admin user seeded with the password shown in the summary."

# Seed MAC-vendor + OS-EOL reference data. Without this a fresh install has ZERO
# mac_vendor_ranges rows and enrichment silently under-resolves. Non-fatal by
# design — see deploy/lib/reference_data.sh.
apply_reference_data install

# Optional demo data — only with explicit opt-in. Real customer installs
# leave the integrations table empty; demo seeds let us exercise the asset
# generation pipeline immediately on a test VM.
if [ "$WITH_DEMO_DATA" = "1" ]; then
    DEMO_DIR="$INSTALL_DIR/database/demo"
    if [ -d "$DEMO_DIR" ]; then
        log_info "Loading demo data from $DEMO_DIR..."
        for f in "$DEMO_DIR"/*.sql; do
            [ -f "$f" ] || continue
            log_info "  applying: $(basename "$f")"
            $PSQL_CMD -v ON_ERROR_STOP=1 < "$f" >/dev/null \
                || log_warn "Demo seed $(basename "$f") failed (non-fatal)."
        done
        log_ok "Demo data loaded — see Settings → Integrations in the UI."
    else
        log_warn "--with-demo-data set but $DEMO_DIR not found in bundle; skipping."
    fi
fi

# Seed SMTP settings into DB if the operator configured them at install time.
if [ "$EMAIL_ENABLED" = "true" ]; then
    log_info "Seeding SMTP settings into system_settings..."
    if seed_smtp_settings; then
        log_ok "SMTP settings persisted (email enabled)."
    else
        log_warn "SMTP seed failed — configure SMTP via the admin UI post-install."
    fi
fi

# Turn the update check on — online installs only. An offline install keeps the
# shipped default (false) so an air-gapped host makes no outbound call at all;
# there the daily job would only ever produce failing requests. Non-fatal: a
# host that never learns about a new release is a nuisance, not a reason to
# abort an otherwise healthy install right before the admin password prints.
if [ "$INSTALL_MODE" = "online" ]; then
    log_info "Enabling the daily update check (online install)..."
    if seed_update_check_setting; then
        # Phrased to stay true in both cases: the upsert deliberately skips a
        # row an operator has already set, so claiming "enabled" outright would
        # be wrong when re-installing over a database that survived.
        log_ok "Update check enabled (system.enable_update_check; an existing operator choice is left alone)."
    else
        log_warn "Update-check seed failed — enable it via the admin UI post-install."
    fi
fi

# ---------------------------------------------------------------------------
# Record install metadata in system_info
# ---------------------------------------------------------------------------
# The version is BUNDLE_VERSION, resolved once near the top of the script (see
# the comment there) and shared with the banner and the image pins, so the
# recorded version can never describe a different bundle than the one the
# pins came from.
# Both writes below warn rather than abort, which is the policy for every step
# between the admin seed and the summary (reference data, demo seeds, SMTP,
# update check above; systemd below). By this point the stack is up and healthy
# and the admin account is seeded behind its own guard, so failing to write
# system_info leaves the install unlabelled, not broken: get_installed_version()
# returns "0.0.0-pre-system-info" and update.sh treats that as a first-class
# state, skipping its version check for it. Aborting here would instead skip the
# systemd install and the summary that prints the admin password — a far worse
# outcome than a row that a re-run of the installer rewrites.
#
# Guarding BOTH is what makes either guard worth anything: the common cause of a
# failure here is an unreachable DB, which fails both calls, so guarding only the
# first would move the abort two lines down instead of preventing it.
update_system_info "${BUNDLE_VERSION:-unknown}" "$RUNTIME" "$INSTALL_MODE" "$INSTALL_DIR" \
    || log_warn "Could not record install metadata in system_info. The stack is running; until this is fixed nothing reports ${BUNDLE_VERSION:-unknown} as the installed version."

# Update opt-in flags now that system_info row exists.
# set_opt_in ends in a `psql -c`, which propagates errors on its own — so before
# this guard a bare call aborted the installer here with no message at all.
MON_FLAG="false"
[ "$WITH_MONITORING" = "1" ] && MON_FLAG="true"
set_opt_in opt_in_monitoring "$MON_FLAG" \
    || log_warn "Could not record the monitoring opt-in in system_info. The stack is running; re-run the installer to set it."

# ---------------------------------------------------------------------------
# Install systemd units (skip gracefully if not available or unit file missing)
# ---------------------------------------------------------------------------
# This runs AFTER the database is seeded but BEFORE the summary that prints the
# generated admin password, so a bare `set -e` abort here would lose it. The
# whole block is therefore non-fatal: install_systemd_units logs precisely what
# failed, and the worst case is a host whose stack is running and healthy but
# whose unit needs attention — which the next update.sh reconciles anyway.
if ! install_systemd_units; then
    log_warn "systemd units are not fully configured. The stack is running. \
The admin password is below and in $INSTALL_DIR/.env (ADMIN_INITIAL_PASSWORD)."
fi

# ---------------------------------------------------------------------------
# Print install summary
# ---------------------------------------------------------------------------
LOG_PATH="${AUDIT_LOG:-/var/log/assetatlas/install.log}"

printf '\n'
log_ok "=== AssetAtlas installation complete ==="
printf '\n'
printf '  Access URL       : https://%s:%s\n' "$DOMAIN" "$HTTPS_PORT"
printf '  Admin user       : admin\n'
printf '  Admin password   : %s  (shown once — store it now)\n' "$INSTALL_ADMIN_PASSWORD"
printf '  Install dir      : %s\n' "$INSTALL_DIR"
printf '  Install log      : %s\n' "$LOG_PATH"
printf '  Support bundle   : assetatlas-cli support-bundle\n'
printf '\n'
printf '  To update later  : assetatlas update   (or %s/installer/update.sh)\n' "$INSTALL_DIR"
if [ "$WITH_MONITORING" = "1" ]; then
    printf '  Monitoring       : Prometheus + Alertmanager running container-internal.\n'
    printf '                     Reach Prometheus via: docker exec -it assetatlas-prometheus wget -qO- localhost:9090/...\n'
    printf '                     (Front via your own ingress/port-forward to expose externally.)\n'
fi
printf '\n'
