# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gated, air-gap-safe "update available" check.

Runs out of band from a daily scheduler job (like the pipeline watchdog). It is
OFF by default in two independent ways:

  1. It makes NO outbound network call unless the `system.enable_update_check`
     setting is explicitly turned on (default false, everywhere).
  2. Even when enabled, EVERY network/parse error is swallowed — an air-gapped
     install that somehow has the setting on stays completely quiet (no alert,
     no raised exception, only a debug log).

When enabled and a newer release is found it creates/refreshes a single alert
for the `assetatlas-update-available` rule (one-active-alert-per-rule dedup,
matching the watchdog); when the versions match it resolves that alert.
"""
import logging
import time

from src.modules.alerts.service import AlertsService

logger = logging.getLogger(__name__)

UPDATE_RULE_SLUG = 'assetatlas-update-available'
# Public version manifest for the "newer version" comparison, published on each
# release by .github/workflows/release.yml. It lives in the PUBLIC
# assetatlas-reference-bundles repo precisely so an anonymous read works — the
# source repo is private, so its GitHub releases API 404s unauthenticated. A
# missing/unreachable manifest simply 404s/times out here, which is swallowed →
# the check stays silent (air-gap-safe).
DEFAULT_MANIFEST_URL = (
    'https://raw.githubusercontent.com/dotdna-io/'
    'assetatlas-reference-bundles/main/latest.json'
)
# Must stay strictly BELOW the daily job interval: the job fires every 24h and
# stamps _last_ok_check a few seconds after each fire, so a 24h TTL would make
# the very next daily fire land inside the window and skip — running only every
# other day. 12h guards against an accidental rapid re-invocation without ever
# suppressing the scheduled daily run.
_CACHE_TTL_SECONDS = 12 * 3600
# Short, fixed budget: the check is best-effort background work, so a slow or
# unreachable manifest host must fail fast rather than tie up the request/worker.
_HTTP_TIMEOUT_SECONDS = 5


def _norm(version):
    """Strip a leading v/V from a version/tag string."""
    v = (version or '').strip()
    return v[1:] if v[:1] in ('v', 'V') else v


def _parse_version(version):
    """Parse 'YYYY.WW.N' (optionally v-prefixed) into an int tuple, or None."""
    v = _norm(version)
    if not v:
        return None
    try:
        return tuple(int(part) for part in v.split('.'))
    except (ValueError, TypeError):
        return None


def fetch_latest_version(url=DEFAULT_MANIFEST_URL):
    """Return the latest release version string, or None on ANY error.

    Reads a small public JSON manifest — ``{"version": "YYYY.WW.N"}`` — published
    by the release workflow. Module-level so both the scheduler's
    UpdateCheckService and the API's `/health/update-check` endpoint share one
    air-gap-safe fetcher: connection refused, timeout, DNS failure, non-200, or a
    malformed body all collapse to None so the caller surfaces nothing.
    """
    try:
        import requests
        resp = requests.get(url, timeout=_HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        version = resp.json().get('version')
        return _norm(version) if version else None
    except Exception as e:  # noqa: BLE001 — intentional catch-all (air-gap safety)
        logger.debug("Update check fetch failed (ignored): %s", e)
        return None


def is_newer(latest, current):
    """True iff `latest` is a strictly newer YYYY.WW.N version than `current`."""
    lt, ct = _parse_version(latest), _parse_version(current)
    if lt is None or ct is None:
        return False
    # Zero-pad to equal length so '2026.27' is not treated as newer than
    # '2026.27.0' (a shorter tuple sorts before its zero-extended form).
    n = max(len(lt), len(ct))
    lt = lt + (0,) * (n - len(lt))
    ct = ct + (0,) * (n - len(ct))
    return lt > ct


class UpdateCheckService:
    """Daily, gated check for a newer AssetAtlas release."""

    def __init__(self, alert_repo, settings_manager, current_version,
                 delivery_service=None,  # AlertDeliveryService; None = no channels
                 *, url=DEFAULT_MANIFEST_URL, clock=time.time):
        self.alert_repo = alert_repo
        self.settings = settings_manager
        self.current_version = _norm(current_version)
        self.delivery = delivery_service
        self.url = url
        self._clock = clock
        self._last_ok_check = None  # timestamp of the last successful fetch

    def tick(self):
        """One evaluation cycle. Never raises for network reasons."""
        rule = self.alert_repo.get_rule_by_slug(UPDATE_RULE_SLUG)
        if rule is None:
            return
        # Off (setting disabled OR rule disabled) → clear any standing alert and
        # make NO outbound network call. Resolving on setting-off prevents a
        # stale "update available" alert lingering forever after the toggle.
        if not self._enabled_setting() or not rule.get('enabled'):
            self.alert_repo.resolve_alerts_for_rule(rule['id'])
            return

        now = self._clock()
        if self._last_ok_check is not None and (now - self._last_ok_check) < _CACHE_TTL_SECONDS:
            return  # cached: at most one real check per _CACHE_TTL_SECONDS

        latest = self._fetch_latest_version()
        if latest is None:
            return  # network/parse error (incl. air-gapped) → stay silent
        self._last_ok_check = now
        self._reconcile(rule, latest)

    def _enabled_setting(self):
        try:
            return bool(self.settings.get('system', 'enable_update_check'))
        except Exception:
            return False

    def _fetch_latest_version(self):
        """Instance wrapper around the module-level fetcher (uses self.url)."""
        return fetch_latest_version(self.url)

    def _reconcile(self, rule, latest):
        existing = self.alert_repo.get_active_alert_for_rule(rule['id'])
        threshold = {'current_version': self.current_version, 'latest_version': latest}
        if is_newer(latest, self.current_version):
            if existing is None:
                # Kept version-agnostic: the specific current/latest live in
                # the threshold (refreshed by update_alert_assets on the
                # supersede path), so a second newer release can't leave a
                # stale version string in the description.
                description = "A newer AssetAtlas release is available."
                created = self.alert_repo.create_alert(
                    rule_id=rule['id'],
                    alert_type=rule.get('type', 'operational'),
                    severity=rule.get('severity', 'low'),
                    title=rule.get('name', 'AssetAtlas Update Available'),
                    description=description,
                    threshold=threshold,
                    asset_count=0, assets_by_category={},
                    data_sources=[], asset_ids=[], source='system',
                    # About the install, not a set of assets. The repo defaults to
                    # 'asset', which would store this asset_count=0 row as an
                    # asset-subject alert — card copy "0 assets matched this rule"
                    # over an empty Involved Assets grid (#986).
                    subject_type='system',
                )
                # None = the INSERT returned no row, so there is no alert to
                # deliver (and no id to read).
                if created is not None:
                    self._dispatch_delivery(rule, created, threshold, description)
            else:
                self.alert_repo.update_alert_assets(
                    alert_id=existing['id'], asset_count=0,
                    assets_by_category={}, data_sources=[], asset_ids=[],
                    threshold=threshold,
                    # Re-passed on every supersede: subject_type sits in this
                    # UPDATE's SET clause and defaults to 'asset', so omitting it
                    # would silently revert the row on the next newer release.
                    subject_type='system',
                )
        elif existing is not None:
            # Caught up (or ahead) — resolve the standing "update available" alert.
            self.alert_repo.resolve_alert(existing['id'])

    def _dispatch_delivery(self, rule, created, threshold, description):
        """Enqueue outbound deliveries for a newly created update alert (#986).

        Created branch only: a standing "update available" alert keeps its existing
        row via the supersede path, so this notifies once per firing episode rather
        than every daily tick until the operator upgrades. Never breaks the check —
        the alert row is already written, and an enqueue failure is swallowed.

        No extra gate: the check itself only reaches this point when
        system.enable_update_check is on, so an air-gapped install stays silent.
        """
        if self.delivery is None:
            return
        try:
            # Mirrors the create_alert call above field-for-field: the channel must
            # describe the same alert the card does.
            alert = {
                'id': created['id'], 'type': rule.get('type', 'operational'),
                'severity': rule.get('severity', 'low'),
                'title': rule.get('name', 'AssetAtlas Update Available'),
                'description': description, 'threshold': threshold,
                'asset_count': 0, 'assets_by_category': {},
                'data_sources': [], 'asset_ids': [], 'assets': [],
                'subject_type': 'system', 'created_at': created['created_at'],
            }
            # The same plain-language sentence the card shows, via the same static
            # method GET /api/alerts calls — so the two cannot disagree. The version
            # threshold carries no operator, so subject_type='system' lands on
            # _generic_summary_reason('system').
            alert['reason'] = AlertsService._compute_reason(alert)
            self.delivery.enqueue_for_alert(alert, rule)
        except Exception as e:
            logger.error("update check: delivery enqueue failed (non-fatal): %s",
                         e, exc_info=True)
