#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# deploy/lib/systemd.sh — install, enable and start the AssetAtlas systemd units.
# Requires: common.sh sourced; INSTALL_DIR set.
#
# Called by BOTH install.sh (fresh install) and update.sh (reconcile on every
# update). update.sh calling it is the point: the unit files ship inside the
# bundle, so any change to them — a corrected WorkingDirectory, a new ExecStart,
# a Documentation URL — reaches an existing host only if the update path
# re-installs them. Before this, update.sh never touched systemd at all, so unit
# changes were stranded on fresh installs forever and a host installed before the
# unit was ever started kept an enabled-but-inactive unit (and therefore a
# `systemctl stop assetatlas` that silently did nothing).
#
# Every operation is idempotent, so running this on an already-correct host is a
# no-op rather than a restart.
#
# Overridable for tests:
#   SYSTEMD_DIR          : where unit files are installed
#                          (default: /etc/systemd/system)
#   ASSETATLAS_BIN_DIR   : where the CLI symlinks go
#                          (default: /usr/local/bin)
: "${SYSTEMD_DIR:=/etc/systemd/system}"
: "${ASSETATLAS_BIN_DIR:=/usr/local/bin}"
# Where to ALSO link when sudo cannot see ASSETATLAS_BIN_DIR — see
# link_assetatlas_cli. /usr/bin because it is in every sudo secure_path there is.
: "${ASSETATLAS_FALLBACK_BIN_DIR:=/usr/bin}"

# _root_do <cmd> [args...]
# Run, retrying under sudo — a non-root operator running a system install gets
# the same result as root without every call site spelling the fallback out.
_root_do() {
    "$@" 2>/dev/null || sudo "$@"
}

# systemd_usable
# True when we should manage units at all: systemd present and not opted out.
# NO_SYSTEMD is the installer's --no-systemd flag; default off.
systemd_usable() {
    [ "${NO_SYSTEMD:-0}" = "0" ] || return 1
    systemctl --version >/dev/null 2>&1
}

# link_assetatlas_cli
# Create /usr/local/bin/{assetatlas,assetatlas-cli} -> $INSTALL_DIR/installer/assetatlas-cli.
#
# The units' ExecStart is an absolute path to that symlink. ONLY the online
# bundle's self-extract header creates it (deploy/build/self-extract-header.sh);
# build-offline.sh emits a plain tarball with no header at all, so on air-gapped
# installs — the primary target — the unit pointed at a path that did not exist
# and every start failed 203/EXEC. Doing it here covers both bundles.
#
# The CLI resolves its own INSTALL_DIR via `readlink -f`, so pointing the symlink
# at installer/assetatlas-cli (not a bundle-root copy) is required.
link_assetatlas_cli() {
    local src="$INSTALL_DIR/installer/assetatlas-cli"
    [ -f "$src" ] || { log_warn "assetatlas-cli not found at $src; unit ExecStart will not resolve."; return 1; }
    _root_do mkdir -p "$ASSETATLAS_BIN_DIR" \
        || { log_warn "Could not create $ASSETATLAS_BIN_DIR."; return 1; }
    local name rc=0
    for name in assetatlas-cli assetatlas; do
        _root_do ln -sf "$src" "$ASSETATLAS_BIN_DIR/$name" || {
            log_warn "Could not link $ASSETATLAS_BIN_DIR/$name."
            rc=1
        }
    done
    link_cli_for_sudo || rc=1
    # Captured then matched — `printf | grep -q` is the banned EPIPE shape.
    # Match on the whole entry so that /usr/local/bin does not vouch for
    # /usr/local/bin-something.
    #
    # Note this reads the INSTALLER's PATH, which is not the operator's login
    # PATH and, under `curl … | sudo bash`, is sudo's secure_path. It is a weak
    # signal on both counts — detect_runtime may have appended /usr/local/bin to
    # reach podman-compose, which silences this warning on exactly the hosts
    # that most need it. link_cli_for_sudo above is the actual guarantee; this
    # stays only for the operator who installed somewhere genuinely exotic.
    local path_entries
    path_entries=$(printf '%s\n' "${PATH//:/$'\n'}")
    if ! grep -Fxq "$ASSETATLAS_BIN_DIR" <<<"$path_entries"; then
        log_warn "$ASSETATLAS_BIN_DIR is not on your PATH, so 'assetatlas' and 'assetatlas-cli' will not resolve. Add it:"
        log_warn "  echo 'export PATH=\"\$PATH:$ASSETATLAS_BIN_DIR\"' >> ~/.bashrc && . ~/.bashrc"
    fi
    return $rc
}

# _sudo_resolves <name>
# 0 when a LATER `sudo <name>` would find it on this host.
#
# Asks sudo rather than parsing secure_path out of /etc/sudoers: sudoers has
# include files and last-match-wins precedence, and `sudo -V` does not print the
# value on every build (RHEL 10.2's does not, its sudoers does). Asking sudo
# itself is the only answer that cannot drift from what sudo will really do.
#
# No sudo on the host means no secure_path to be defeated by, so that reports
# resolvable. A sudo that demands a password under -n reports NOT resolvable,
# which costs one redundant symlink and never a missing one.
_sudo_resolves() {
    command -v sudo >/dev/null 2>&1 || return 0
    sudo -n sh -c "command -v $1" >/dev/null 2>&1
}

# link_cli_for_sudo
# `sudo assetatlas update` is the documented update command, and on RHEL it
# failed with "command not found" while both symlinks sat correctly in
# /usr/local/bin: sudo replaces PATH with secure_path, whose RHEL default
# (/sbin:/bin:/usr/sbin:/usr/bin) omits /usr/local/bin (#1325). Same root cause
# as detect_runtime's podman-compose recovery, one layer up.
#
# A second symlink, not an edit to the operator's sudoers: a malformed sudoers
# drop-in can lock every user out of sudo entirely, which is a catastrophic
# failure mode to risk for a convenience path.
link_cli_for_sudo() {
    [ "$ASSETATLAS_FALLBACK_BIN_DIR" = "$ASSETATLAS_BIN_DIR" ] && return 0
    _sudo_resolves assetatlas && return 0
    local src="$INSTALL_DIR/installer/assetatlas-cli"
    local name rc=0
    for name in assetatlas-cli assetatlas; do
        _root_do ln -sf "$src" "$ASSETATLAS_FALLBACK_BIN_DIR/$name" || {
            log_warn "Could not link $ASSETATLAS_FALLBACK_BIN_DIR/$name; 'sudo assetatlas' will not resolve."
            rc=1
        }
    done
    [ "$rc" -eq 0 ] && log_info "CLI: also linked into $ASSETATLAS_FALLBACK_BIN_DIR — sudo's secure_path does not include $ASSETATLAS_BIN_DIR, so 'sudo assetatlas update' would not have resolved."
    return $rc
}

# install_unit_file <src> <dest>
# Copy a unit file into $SYSTEMD_DIR, retargeting everything the shipped unit
# hardcodes. Both are wrong whenever the install diverges from the defaults:
#
#   WorkingDirectory  — hardcodes /opt/assetatlas; --install-dir puts it
#                       elsewhere -> 200/CHDIR on start, and silently at 02:00
#                       for the backup timer.
#   ExecStart/ExecStop— hardcode /usr/local/bin/assetatlas-cli; an overridden
#                       ASSETATLAS_BIN_DIR links it elsewhere -> 203/EXEC.
#
# sed argument ARRAY, not `sed -i`: BSD and GNU disagree on the suffix argument
# and this repo's bats suite runs on macOS dev machines.
install_unit_file() {
    local src=$1 dest=$2
    [ -f "$src" ] || { log_error "unit file not found: $src"; return 1; }
    _root_do mkdir -p "$SYSTEMD_DIR" || { log_error "Could not create $SYSTEMD_DIR."; return 1; }
    local tmp
    tmp=$(mktemp) || return 1
    local sed_args=(
        -e "s|^WorkingDirectory=.*|WorkingDirectory=${INSTALL_DIR}|"
        -e "s|^ExecStart=/usr/local/bin/|ExecStart=${ASSETATLAS_BIN_DIR}/|"
        -e "s|^ExecStop=/usr/local/bin/|ExecStop=${ASSETATLAS_BIN_DIR}/|"
    )
    sed "${sed_args[@]}" "$src" > "$tmp" || { rm -f "$tmp"; return 1; }
    chmod 644 "$tmp"
    _root_do cp "$tmp" "$dest" || { rm -f "$tmp"; return 1; }
    rm -f "$tmp"
}

# install_systemd_units
# Install + enable + start assetatlas.service, and install + enable the backup
# timer. Returns non-zero if anything essential failed; the caller decides how
# loud to be (install.sh must not abort before printing the admin password).
install_systemd_units() {
    systemd_usable || { log_info "systemd not in use; skipping unit installation."; return 0; }

    local unit_src="$INSTALL_DIR/systemd/assetatlas.service"
    [ -f "$unit_src" ] || { log_warn "systemd unit file not found at $unit_src; skipping."; return 0; }

    log_info "Installing systemd units..."
    link_assetatlas_cli || true   # warned already; a broken ExecStart surfaces below

    install_unit_file "$unit_src" "$SYSTEMD_DIR/assetatlas.service" \
        || { log_error "Could not install $SYSTEMD_DIR/assetatlas.service."; return 1; }
    _root_do systemctl daemon-reload \
        || { log_error "Could not reload systemd."; return 1; }
    _root_do systemctl enable assetatlas.service \
        || { log_error "Could not enable assetatlas.service."; return 1; }

    # Enabling alone leaves this Type=oneshot unit 'inactive (dead)' while the
    # stack serves traffic, because its ExecStart has never run — which makes
    # `systemctl stop assetatlas` a silent no-op, since systemd runs no ExecStop
    # for an inactive unit. ExecStart is `assetatlas-cli start`, an idempotent
    # `compose up -d`, so starting it against a running stack just latches the
    # unit active and starts nothing new.
    #
    # Not fatal, and a single attempt on purpose: the stack is already up and
    # health-checked by the time either caller reaches this, and TimeoutStartSec
    # is 300, so a sudo retry could stall for ten minutes.
    if systemctl start assetatlas.service 2>/dev/null \
       || sudo systemctl start assetatlas.service; then
        log_ok "systemd unit installed, enabled and started."
    else
        log_warn "systemd unit installed and enabled, but could not be started. \
The stack is running, but 'systemctl stop assetatlas' will not stop it until the unit is active."
    fi

    install_backup_timer
}

# reconcile_systemd_units
# update.sh's entry point. Refreshes the units this host ALREADY has, and does
# nothing otherwise.
#
# The distinction matters: install.sh CREATES units because the operator is
# installing. An update must not quietly reverse a deliberate --no-systemd
# choice — and that choice is not recorded in .install-state, so there is nothing
# to read back. The presence of an installed unit is the signal, and it is a
# better one anyway: it is the actual state of the host rather than a claim about
# what someone chose months ago.
#
# A host that declined systemd and later wants it re-runs the installer.
reconcile_systemd_units() {
    systemd_usable || return 0
    if [ ! -f "$SYSTEMD_DIR/assetatlas.service" ]; then
        log_info "No AssetAtlas systemd unit on this host; leaving systemd alone."
        return 0
    fi
    install_systemd_units
}

# install_backup_timer
# The nightly DB backup. Separate from the main unit because it is optional —
# absent from older bundles — but it carries the same hardcoded WorkingDirectory,
# so it goes through install_unit_file too.
install_backup_timer() {
    local timer_src="$INSTALL_DIR/systemd/assetatlas-backup.timer"
    local svc_src="$INSTALL_DIR/systemd/assetatlas-backup.service"
    [ -f "$timer_src" ] || return 0

    log_info "Installing assetatlas-backup timer..."
    install_unit_file "$svc_src" "$SYSTEMD_DIR/assetatlas-backup.service" \
        || { log_error "Could not install assetatlas-backup.service."; return 1; }
    install_unit_file "$timer_src" "$SYSTEMD_DIR/assetatlas-backup.timer" \
        || { log_error "Could not install assetatlas-backup.timer."; return 1; }
    _root_do systemctl daemon-reload \
        || { log_error "Could not reload systemd for the backup timer."; return 1; }
    _root_do systemctl enable --now assetatlas-backup.timer \
        || { log_error "Could not enable assetatlas-backup.timer."; return 1; }
    log_ok "Backup timer installed and enabled."
}
