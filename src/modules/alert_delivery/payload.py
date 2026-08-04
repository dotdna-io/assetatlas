# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical, stable alert payload built at alert-creation time (#917).

Channels consume this dict; #918 formalizes the webhook wire format on top of it.
Built from data already in hand at create time — no extra DB read.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

# Evidence keys stripped before the payload leaves the building. Both are raw-UUID
# lists — which #979's copy contract keeps out of anything an operator reads — and both
# are redundant with `assets`, which names the same entities with labels. Stripped here,
# at the boundary, so no channel has to remember to.
# Not a size guard: every detector caps its asset list at 25 before either key is built,
# so both are already small. Strip them because they are raw UUIDs, not because of bulk.
_EVIDENCE_STRIP = ('labels', 'sample_asset_ids')


def _iso(value: Any) -> Any:
    """Serialize a datetime to ISO. A raw datetime breaks the JSONB enqueue and
    webhook's json.dumps, and syslog._timestamp needs a whitespace-free str."""
    return value.isoformat() if isinstance(value, datetime) else value


def _public_evidence(evidence: Any) -> Optional[Dict[str, Any]]:
    """The card's evidence minus the raw-id keys. Returns None for a rule alert
    (no evidence at all) so the webhook envelope keeps a stable shape."""
    if not isinstance(evidence, dict):
        return None
    return {k: v for k, v in evidence.items() if k not in _EVIDENCE_STRIP} or None


def _assets(alert: Dict[str, Any], cap: int) -> List[Dict[str, Any]]:
    """[{id, label}] for the alert's assets. Same order and same cap as asset_ids —
    built the same unfiltered way, so a receiver correlating the two by index cannot
    fall off the end. The caller always supplies real dicts (DB rows on the rule path,
    resolved evidence labels on the anomaly path); a malformed entry is a caller bug
    and should surface, not be silently dropped into a misaligned list."""
    return [
        {'id': str(a['id']), 'label': a.get('label')}
        for a in (alert.get('assets') or [])[:cap]
    ]


def build_alert_payload(alert: Dict[str, Any], rule: Dict[str, Any],
                        *, max_asset_ids: int = 100) -> Dict[str, Any]:
    asset_ids = [str(a) for a in (alert.get('asset_ids') or [])]
    return {
        'alert_id': str(alert.get('id')) if alert.get('id') is not None else None,
        'rule_slug': rule.get('slug'),
        'rule_name': rule.get('name'),
        'type': alert.get('type'),
        'severity': alert.get('severity'),
        'title': alert.get('title'),
        'description': alert.get('description'),
        'asset_count': alert.get('asset_count', len(asset_ids)),
        'assets_by_category': alert.get('assets_by_category') or {},
        'data_sources': alert.get('data_sources') or [],
        'asset_ids': asset_ids[:max_asset_ids],
        # Assets by label, never UUID (#979 copy contract). Same order and same cap
        # as asset_ids. Both dispatch sites have labels already in hand — the rule
        # path projects asset_label on its match rows, the anomaly path reads
        # evidence['labels'] — so this costs no extra DB read.
        'assets': _assets(alert, max_asset_ids),
        'threshold': alert.get('threshold') or {},
        'reason': alert.get('reason'),
        # The card's evidence: narrative, observed/typical, breakdown, labelled
        # sample, and the technical stats it keeps behind a collapsed panel.
        'evidence': _public_evidence(alert.get('evidence')),
        'pipeline_run_id': str(alert['pipeline_run_id'])
            if alert.get('pipeline_run_id') is not None else None,
        'created_at': _iso(alert.get('created_at')),
        # alert-subject-types design §7: carry subject_type/subjects so channels
        # can render non-asset alerts. Default 'asset' covers callers that predate
        # the column (e.g. anomaly_detection's dispatch dict never sets this key).
        'subject_type': alert.get('subject_type') or 'asset',
        'subjects': alert.get('subjects'),
        # The card's source badge. Derived from the rule, mirroring both the UI's
        # 'user' -> 'custom' mapping and AlertDeliveryService._category_for, so no
        # dispatch site has to pass it. Prometheus alerts never reach this path.
        'source': 'custom' if rule.get('source') == 'user' else 'system',
    }
