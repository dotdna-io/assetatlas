# SPDX-License-Identifier: AGPL-3.0-or-later
"""Email (SMTP) alert delivery channel (#919).

Sends a fired alert as a plaintext email to configured recipients over the
global SMTP transport (Settings → Email), reusing EmailService. The channel
holds no SMTP secret — the encrypted password lives in the email settings.

The #917 drain runs in the scheduler worker with NO Flask app context, so the
channel cannot reach current_app.email_service. Instead the process EmailService
is injected once at startup via set_email_service() (API factory + worker).
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.auth.services.email_service import (
    EMAIL_DISABLED,
    EMAIL_NOT_CONFIGURED,
    EmailService,
)
from src.modules.alert_delivery.channels.base import AlertChannel, DeliveryResult

logger = logging.getLogger(__name__)

# Set once at startup by the API factory and the scheduler worker.
_email_service: Optional[EmailService] = None

_SMTP_UNCONFIGURED_MESSAGE = 'SMTP is not configured (Settings → Email)'


def set_email_service(service: Optional[EmailService]) -> None:
    """Inject the process EmailService. Called at startup by the API factory and
    the scheduler worker — the drain has no app context to reach it otherwise."""
    global _email_service
    _email_service = service


def get_email_service() -> Optional[EmailService]:
    return _email_service


# Mirrors AlertCard.tsx TYPE_LABELS — the card never shows the raw slug.
_TYPE_LABELS = {
    'security': 'Security',
    'compliance': 'Compliance',
    'operational': 'Operational',
    'data_quality': 'Data quality',
}

# Mirrors AlertsPage.tsx:248-249 — a non-asset alert must never say "assets".
_SUBJECT_NOUNS = {
    'asset': ('asset', 'assets'),
    'integration': ('data source', 'data sources'),
    'audit_event': ('event', 'events'),
}

# Mirrors AlertSubjects.tsx PANE_TITLE.
_SUBJECT_PANE_TITLES = {'integration': 'Data Sources', 'audit_event': 'Audit Events'}

_ASSET_LIST_CAP = 12   # AlertsPage.tsx:205
_SAMPLE_CAP = 25       # AlertEvidence.tsx:52


def _type_label(alert_type: Any) -> str:
    if not alert_type:
        return ''
    return _TYPE_LABELS.get(alert_type) or str(alert_type).replace('_', ' ').capitalize()


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _count_phrase(payload: Dict[str, Any]) -> str:
    """'12 assets' / '3 data sources' / '' for summary-only types. The card hides the
    count when it is 0; the old body printed 'Affected assets: 0'."""
    count = _int_or_none(payload.get('asset_count'))
    if not count or count <= 0:
        return ''
    nouns = _SUBJECT_NOUNS.get(payload.get('subject_type') or 'asset')
    if not nouns:
        return ''
    return f"{count} {nouns[0] if count == 1 else nouns[1]}"


def _detected(created_at: Any) -> Optional[str]:
    """'2026-07-16 14:32 UTC' from the payload's ISO string."""
    if not isinstance(created_at, str) or not created_at:
        return None
    try:
        dt = datetime.fromisoformat(created_at)
    except ValueError:
        return created_at  # unparseable: show it raw rather than drop it
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%d %H:%M UTC')


def _asset_name(asset: Any) -> Optional[str]:
    if not isinstance(asset, dict):
        return None
    # asset_label already COALESCEs override/hostname/fqdn/mac/sn/ip, so a missing
    # label means the asset has none of them — the card's final fallback.
    return asset.get('label') or 'Unknown'


def _entity_lines(payload: Dict[str, Any]) -> List[str]:
    """The card's entity pane, as one list. Precedence, not concatenation: asset-subject
    alerts carry `assets`, metric-subject anomaly findings carry evidence['sample'], and
    integration/audit_event carry `subjects`. They are mutually exclusive in practice;
    the precedence makes it impossible to print two lists if that ever stops holding."""
    subject_type = payload.get('subject_type') or 'asset'
    assets = payload.get('assets') or []
    evidence = payload.get('evidence')
    evidence = evidence if isinstance(evidence, dict) else {}
    sample = evidence.get('sample')
    subjects = payload.get('subjects') or []
    lines: List[str] = []

    if subject_type == 'asset' and assets:
        total = _int_or_none(payload.get('asset_count')) or len(assets)
        shown = [n for n in (_asset_name(a) for a in assets[:_ASSET_LIST_CAP]) if n]
        lines.append('')
        lines.append(f"Affected assets ({total})")
        lines.extend(f"  {n}" for n in shown)
        more = total - len(shown)
        if more > 0:
            lines.append(f"  +{more} more")
        return lines

    if isinstance(sample, list) and sample:
        observed = evidence.get('observed')
        shown = [n for n in (_asset_name(s) for s in sample[:_SAMPLE_CAP]) if n]
        header = ('Sample of affected assets'
                  if observed is None else
                  f"Sample of affected assets ({len(sample)} of {observed})")
        lines.append('')
        lines.append(header)
        lines.extend(f"  {n}" for n in shown)
        return lines

    title = _SUBJECT_PANE_TITLES.get(subject_type)
    if title and subjects:
        lines.append('')
        lines.append(title)
        for s in subjects:
            if not isinstance(s, dict):
                continue
            sublabel = s.get('sublabel')
            lines.append(f"  {s.get('label') or 'Unknown'}"
                         + (f" — {sublabel}" if sublabel else ''))
    return lines


def _format_body(payload: Dict[str, Any], base_url: str = '') -> str:
    """The alert card, as plaintext, in the card's reading order: meta -> description
    -> reason -> evidence -> entities -> footer. evidence['technical'] is deliberately
    not rendered — the card keeps it behind a collapsed panel; it still travels in the
    payload for webhook receivers."""
    payload = payload or {}
    lines: List[str] = []

    meta = [p for p in (str(payload.get('severity') or '').upper(),
                        _type_label(payload.get('type')),
                        _count_phrase(payload)) if p]
    if meta:
        lines.append(' · '.join(meta))

    description = payload.get('description')
    if description:
        lines.append('')
        lines.append(str(description))

    # Suppressed when it would repeat the description — the card's rule
    # (AlertsPage.tsx:267).
    reason = payload.get('reason')
    if reason and reason != description:
        lines.append('')
        lines.append(f"Why: {reason}")

    evidence = payload.get('evidence')
    evidence = evidence if isinstance(evidence, dict) else {}

    # Suppressed when it duplicates the reason — AlertEvidence.tsx:23.
    narrative = evidence.get('narrative')
    if narrative and narrative != reason:
        lines.append('')
        lines.append(str(narrative))

    observed = evidence.get('observed')
    if observed is not None:
        technical = evidence.get('technical')
        usual = evidence.get('typical')
        if usual is None and isinstance(technical, dict):
            usual = technical.get('median')
        lines.append('')
        lines.append(f"Observed {observed}"
                     + (f" vs the usual {usual}" if usual is not None else ''))

    breakdown = evidence.get('breakdown')
    if isinstance(breakdown, list) and breakdown:
        lines.append('')
        lines.append('Breakdown')
        for row in breakdown[:8]:  # AlertEvidence.tsx:35
            if isinstance(row, dict):
                lines.append(f"  {row.get('key')}   {row.get('count')}")

    categories = payload.get('assets_by_category')
    if isinstance(categories, dict) and categories:
        lines.append('')
        lines.append('By category')
        for name, count in categories.items():
            lines.append(f"  {name}   {count}")

    lines.extend(_entity_lines(payload))

    data_sources = payload.get('data_sources') or []
    if isinstance(data_sources, str):
        # A scalar source would otherwise be join()'d character-by-character.
        data_sources = [data_sources]
    footer: List[str] = []
    if data_sources:
        footer.append(f"Data sources: {', '.join(str(s) for s in data_sources)}")
    detected = _detected(payload.get('created_at'))
    if detected:
        footer.append(f"Detected: {detected}")
    if footer:
        lines.append('')
        lines.extend(footer)

    # Omitted whole when unconfigured — an air-gapped install may never set
    # security.public_base_url, and a dangling header is worse than no link.
    if base_url:
        lines.append('')
        lines.append('View in AssetAtlas')
        lines.append(f"{base_url.rstrip('/')}/alerts")

    lines.append('')
    lines.append('— AssetAtlas')
    return "\n".join(lines)


class EmailChannel(AlertChannel):
    type = 'email'
    sensitive_fields = ()          # SMTP password lives in global email settings
    user_creatable = True

    def validate_config(self, config: dict) -> None:
        config = config or {}
        recipients = config.get('recipients')
        if not isinstance(recipients, list) or not recipients:
            raise ValueError('recipients is required and must be a non-empty list')
        for r in recipients:
            if not isinstance(r, str) or '@' not in r:
                raise ValueError('each recipient must be a valid email address')

    def send(self, config: dict, payload: dict) -> DeliveryResult:
        config = config or {}
        payload = payload or {}
        service = _email_service
        if service is None:
            return DeliveryResult(ok=False, retriable=False,
                                  error='email service not available')
        recipients = [r for r in (config.get('recipients') or [])
                      if isinstance(r, str) and r.strip()]
        if not recipients:
            return DeliveryResult(ok=False, retriable=False,
                                  error='no recipients configured')
        severity = str(payload.get('severity') or 'info').upper()
        title = payload.get('title') or 'Alert'
        subject = f"[AssetAtlas][{severity}] {title}"
        try:
            base_url = service.get_public_base_url() or ''
        except Exception:
            # ABC contract: never raise. A settings read failure costs the link, not
            # the alert.
            base_url = ''
        try:
            body = _format_body(payload, base_url)
        except Exception as e:
            # A malformed payload (e.g. assets/subjects as the wrong container type,
            # or type/subject_type as an unhashable value) is a permanent data error —
            # surface it without sending and without raising (ABC contract; send_test()
            # calls send() unwrapped, so a raise = HTTP 500).
            return DeliveryResult(ok=False, retriable=False,
                                  error=f'invalid alert payload: {e}')
        ok, error, retriable = service.send_alert_email(recipients, subject, body)
        if not ok and error in (EMAIL_DISABLED, EMAIL_NOT_CONFIGURED):
            error = _SMTP_UNCONFIGURED_MESSAGE
        return DeliveryResult(ok=ok, error=(None if ok else error), retriable=retriable)
