# SPDX-License-Identifier: AGPL-3.0-or-later
"""AnomalyDetectionService: dispatch enabled anomaly rules to their detectors,
turn findings into alerts (create/update/resolve), with per-detector isolation,
fingerprint dedup, and per-entity flood aggregation."""
import logging
from datetime import datetime, timedelta, time as _time

from src.modules.alerts.service import AlertsService
from src.modules.anomaly_detection.detectors import DETECTOR_CLASSES
from src.modules.anomaly_detection.domain import ASSET_LABEL_TOKEN, AnomalyFinding, RunEvalContext
from src.modules.anomaly_detection.profiles import ProfileStore
from src.modules.anomaly_detection.repository import AnomalyRepository
from src.modules.anomaly_detection.settings import load_settings

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {'low': 0, 'medium': 1, 'high': 2, 'critical': 3}

# Plain-language names for the aggregate title, so a flood alert reads as a product
# message ("12 identity churn events this run") instead of leaking a raw detector_key.
_FRIENDLY_DETECTOR = {
    'ml_newcomer': 'unusual new assets',
    'ml_behavior': 'unusual change findings',
    'mass_new_assets': 'mass new assets',
    'mass_missing': 'mass disappearances',
    'source_volume': 'source volume anomalies',
    'attribute_churn': 'attribute changes',
    'identity_churn': 'identity churn events',
    'segment_outlier': 'out-of-profile devices',
    'first_seen_value': 'first-seen values',
    'security_source_loss': 'security coverage losses',
    'unprotected_newcomer': 'unprotected newcomers',
    'confidence_shift': 'confidence drops',
    'identity_swap': 'identity changes',
}


def _max_severity(severities):
    return max(severities, key=lambda s: _SEVERITY_ORDER.get(s, 0))


def _event_since(run_started_at, silence_minutes):
    """Dedup window for event alerts: at least the current day (event
    fingerprints carry the run date) and further back for longer silences."""
    day_start = datetime.combine(run_started_at.date(), _time.min)
    if run_started_at.tzinfo is not None:
        day_start = day_start.replace(tzinfo=run_started_at.tzinfo)
    return min(run_started_at - timedelta(minutes=silence_minutes or 0), day_start)


def _short_label(asset_id):
    """Fallback when an asset has no resolvable label — never expose a full UUID."""
    return f"asset {str(asset_id)[:8]}" if asset_id else "an asset"


class AnomalyDetectionService:
    def __init__(self, db_adapter, alerts_repository, detector_classes=None, delivery_service=None):
        self.db = db_adapter
        self.alerts_repo = alerts_repository
        self.repo = AnomalyRepository(db_adapter)
        self.profiles = ProfileStore(db_adapter)
        self.detector_classes = detector_classes or DETECTOR_CLASSES
        self.delivery = delivery_service

    def evaluate_run(self, generation_run_id, run_started_at, current_stats,
                     current_batches=None, pipeline_run_id=None):
        settings = load_settings(self.db)
        summary = {'rules_evaluated': 0, 'alerts_created': 0, 'alerts_updated': 0,
                   'alerts_resolved': 0, 'learning': [], 'errors': {}}
        for rule in self.repo.get_enabled_anomaly_rules():
            slug = rule.get('slug', '?')
            cls = self.detector_classes.get(rule.get('detector_key'))
            if cls is None:
                summary['errors'][slug] = 'unknown detector_key'
                continue
            try:
                params = {**getattr(cls, 'DEFAULT_PARAMS', {}), **(rule.get('threshold') or {})}
                ctx = RunEvalContext(
                    generation_run_id=str(generation_run_id),
                    run_started_at=run_started_at,
                    current_stats=current_stats or {},
                    rule=rule, params=params, settings=settings,
                    repo=self.repo, current_batches=current_batches or [],
                    profiles=self.profiles)
                findings = cls().evaluate(ctx)
                findings = self._label_findings(findings)
                findings = self._aggregate(findings, settings.aggregation_threshold)
                self._apply(rule, findings, run_started_at, summary, pipeline_run_id)
                summary['rules_evaluated'] += 1
            except Exception as e:      # one broken detector never blocks the rest
                logger.error("anomaly detector %s failed: %s", slug, e, exc_info=True)
                summary['errors'][slug] = str(e)
        return summary

    def fleet_confidence_histogram(self):
        """Low/medium/high confidence counts, recorded into run stats so A6
        (Phase 2) has a warm baseline. Boundaries resolved by the repository (the
        A6 detector reuses the same path via ctx.repo)."""
        med, high = self.repo.get_confidence_boundaries()
        return self.repo.get_confidence_histogram(med, high)

    def _aggregate(self, findings, threshold):
        events = [f for f in findings if f.kind == 'event']
        if len(events) <= threshold:
            return findings
        others = [f for f in findings if f.kind != 'event']
        asset_ids = []
        for f in events:
            for aid in f.asset_ids:
                if aid not in asset_ids:
                    asset_ids.append(aid)
        n = len(events)
        dk = events[0].detector_key
        friendly = _FRIENDLY_DETECTOR.get(dk, dk.replace('_', ' '))
        agg_ids = asset_ids[:25]
        # _label_findings ran first and already resolved a label for every asset id on
        # every collapsed finding, so the aggregate inherits them for free — no query,
        # and no relabelling pass after aggregating (which is what would leak a raw
        # {{asset_label}} token into the frozen evidence items).
        labels = {}
        for f in events:
            labels.update(f.evidence.get('labels') or {})
        agg = AnomalyFinding(
            detector_key=dk, dimension_key='aggregate', kind='event',
            severity=_max_severity([f.severity for f in events]),
            alert_type=events[0].alert_type,
            subject_type=events[0].subject_type,
            title=f"{n} {friendly} this run",
            description=f"{n} findings collapsed into one aggregate alert.",
            evidence={'aggregated': True, 'count': n, 'detector': dk,
                      'items': [f.evidence for f in events[:10]],
                      'labels': {aid: labels.get(aid) for aid in agg_ids}},
            asset_ids=agg_ids)
        return others + [agg]

    def _label_findings(self, findings):
        """Batch-resolve asset labels for this rule's findings and (a) inject a per-asset
        {id: label} map + a primary 'label' into evidence, (b) substitute ASSET_LABEL_TOKEN
        in title/description/narrative, (c) label sample_asset_ids into evidence['sample']
        (metric findings with a natural asset list — mass-new, re-added). Runs once per
        rule (findings are per-rule)."""
        ids = []
        for f in findings:
            for aid in f.asset_ids:
                if aid not in ids:
                    ids.append(aid)
            for aid in f.sample_asset_ids:
                if aid not in ids:
                    ids.append(aid)
        labels = self.repo.get_asset_labels(ids) if ids else {}
        for f in findings:
            if f.asset_ids:
                f.evidence['labels'] = {aid: labels.get(aid) or _short_label(aid) for aid in f.asset_ids}
            if f.sample_asset_ids:
                f.evidence['sample'] = [{'id': aid, 'label': labels.get(aid) or _short_label(aid)}
                                        for aid in f.sample_asset_ids]
            primary = f.asset_ids[0] if f.asset_ids else None
            label = (labels.get(primary) or _short_label(primary)) if primary else 'an asset'
            if primary:
                f.evidence.setdefault('label', label)
            if ASSET_LABEL_TOKEN in f.title:
                f.title = f.title.replace(ASSET_LABEL_TOKEN, label)
            if f.description and ASSET_LABEL_TOKEN in f.description:
                f.description = f.description.replace(ASSET_LABEL_TOKEN, label)
            narrative = f.evidence.get('narrative')
            if narrative and ASSET_LABEL_TOKEN in narrative:
                f.evidence['narrative'] = narrative.replace(ASSET_LABEL_TOKEN, label)
        return findings

    def _apply(self, rule, findings, run_started_at, summary, pipeline_run_id=None):
        events = [f for f in findings if f.kind == 'event']
        conditions = [f for f in findings if f.kind == 'condition']
        self._apply_events(rule, events, run_started_at, summary, pipeline_run_id)
        self._apply_conditions(rule, conditions, summary, pipeline_run_id)

    def _apply_events(self, rule, events, run_started_at, summary, pipeline_run_id=None):
        if not events:
            return
        run_date = run_started_at.date().isoformat()
        since = _event_since(run_started_at, rule.get('silence_duration'))
        recent = set(self.repo.get_recent_alert_fingerprints(rule['id'], since))
        for f in events:
            fp = f"anomaly:{f.detector_key}:{f.dimension_key}:{run_date}"
            if fp in recent:
                continue
            self._create(rule, f, fp, summary, pipeline_run_id)
            recent.add(fp)          # also dedup exact duplicates within this run

    def _apply_conditions(self, rule, conditions, summary, pipeline_run_id=None):
        # Always runs (even with no conditions) so stale conditions auto-resolve.
        active = self.repo.get_active_anomaly_alerts(rule['id']) or []
        active_by_fp = {a['fingerprint']: a for a in active if a.get('fingerprint')}
        seen = set()
        for f in conditions:
            fp = f"anomaly-cond:{f.detector_key}:{f.dimension_key}"
            seen.add(fp)
            existing = active_by_fp.get(fp)
            if existing:
                self.alerts_repo.update_alert_assets(
                    existing['id'], len(f.asset_ids), f.assets_by_category,
                    f.data_sources, f.asset_ids, rule.get('threshold') or {})
                self.alerts_repo.update_alert_evidence(existing['id'], f.severity, f.evidence)
                self._emit_event(existing['id'], rule, pipeline_run_id, f.severity,
                                 'active', len(f.asset_ids))
                summary['alerts_updated'] += 1
            else:
                self._create(rule, f, fp, summary, pipeline_run_id)
        for fp, alert in active_by_fp.items():
            if fp.startswith('anomaly-cond:') and fp not in seen:
                self.alerts_repo.resolve_alert(alert['id'])
                self._emit_event(alert['id'], rule, pipeline_run_id, alert.get('severity', 'low'),
                                 'resolved', 0)
                summary['alerts_resolved'] += 1

    def _create(self, rule, f, fp, summary, pipeline_run_id=None):
        created = self.alerts_repo.create_alert(
            rule_id=rule['id'], alert_type=f.alert_type, severity=f.severity,
            title=f.title, description=rule.get('description') or f.description,
            threshold=rule.get('threshold') or {},
            asset_count=len(f.asset_ids), assets_by_category=f.assets_by_category,
            data_sources=f.data_sources, asset_ids=f.asset_ids, source='system',
            subject_type=f.subject_type,
            evidence=f.evidence, fingerprint=fp)
        alert_id = created['id']
        self._emit_event(alert_id, rule, pipeline_run_id, f.severity, 'active', len(f.asset_ids))
        self._dispatch_delivery(alert_id, rule, f, pipeline_run_id,
                                created_at=created['created_at'])
        summary['alerts_created'] += 1

    def _emit_event(self, alert_id, rule, pipeline_run_id, severity, status, asset_count) -> None:
        """Append a per-run firing snapshot for an anomaly alert (#906). No-op
        outside a pipeline run."""
        if pipeline_run_id is None:
            return
        self.alerts_repo.append_alert_event(
            alert_id=alert_id, rule_id=rule['id'], pipeline_run_id=pipeline_run_id,
            severity=severity, status=status, asset_count=asset_count)

    def _dispatch_delivery(self, alert_id, rule, f, pipeline_run_id,
                           created_at=None) -> None:
        """Enqueue outbound deliveries for a newly created anomaly alert (#917).
        Swallows errors so a delivery miss never breaks anomaly evaluation."""
        if self.delivery is None:
            return
        try:
            evidence = f.evidence if isinstance(f.evidence, dict) else {}
            labels = evidence.get('labels') or {}
            alert = {
                'id': alert_id, 'type': f.alert_type, 'severity': f.severity,
                'title': f.title,
                'description': rule.get('description') or f.description,
                'threshold': rule.get('threshold') or {}, 'asset_count': len(f.asset_ids),
                'assets_by_category': f.assets_by_category, 'data_sources': f.data_sources,
                'asset_ids': f.asset_ids,
                # _label_findings already resolved these; no extra query. Order matches
                # asset_ids so the payload pairs them by index.
                'assets': [{'id': aid, 'label': labels.get(aid)} for aid in f.asset_ids],
                'evidence': f.evidence,
                'subject_type': f.subject_type,
                'pipeline_run_id': pipeline_run_id,
                'created_at': created_at,
            }
            # The same plain-language sentence the card shows, via the same static
            # method GET /api/alerts calls — so the two cannot disagree. Its first
            # branch returns evidence['narrative'], leaving every per-finding alert
            # byte-identical; only the aggregate, whose evidence carries no narrative,
            # changes — from nothing at all to the sentence the card already showed.
            alert['reason'] = AlertsService._compute_reason(alert)
            self.delivery.enqueue_for_alert(alert, rule)
        except Exception as e:
            logger.error("anomaly delivery enqueue failed (non-fatal): %s", e, exc_info=True)
