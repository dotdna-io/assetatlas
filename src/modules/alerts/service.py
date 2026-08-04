# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Alerts Service

Business logic for alert evaluation and API responses.
Evaluates SQL-based rules, manages alert lifecycle (create/update/resolve),
and builds API response payloads.
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.modules.alerts.repository import SystemRuleImmutableError  # noqa: F401

logger = logging.getLogger(__name__)


class AlertsService:
    """Service for alert evaluation and API operations."""

    def __init__(self, repository, delivery_service=None):
        self.repo = repository
        self.delivery = delivery_service

    # =========================================================================
    # Evaluation (called by pipeline Stage 12)
    # =========================================================================

    def evaluate_all_rules(self, pipeline_run_id=None) -> Dict[str, Any]:
        """Evaluate all enabled pipeline-cadence rules (pipeline Stage 12).

        Cadence defaults to 'pipeline', so interval-cadence rules — which the
        scheduler worker evaluates every 5 minutes — are never picked up here.
        s20's behavior is therefore unchanged.

        ``pipeline_run_id`` (pipeline_runs.id) is threaded so each evaluation can
        append a firing-history snapshot (#906); it is None on the interval path.
        """
        return self._evaluate_rules(
            self.repo.get_enabled_rules(cadence='pipeline'), pipeline_run_id)

    def evaluate_interval_rules(self) -> Dict[str, Any]:
        """Evaluate all enabled interval-cadence rules.

        Called by the scheduler worker on a 5-minute interval, out of band from
        the pipeline. Shares the exact create/update/resolve loop with the
        pipeline path via _evaluate_rules.
        """
        return self._evaluate_rules(self.repo.get_enabled_rules(cadence='interval'))

    def _evaluate_rules(self, rules: List[Dict[str, Any]], pipeline_run_id=None) -> Dict[str, Any]:
        """Run the create/update/resolve loop over a rule set. Returns stats.

        A rule that raises is contained by evaluate_rule (which returns 'error'),
        so one bad rule never stops the others.
        """
        stats = {
            'rules_evaluated': 0,
            'alerts_created': 0,
            'alerts_updated': 0,
            'alerts_resolved': 0,
            'errors': 0,
        }

        for rule in rules:
            result = self.evaluate_rule(rule, pipeline_run_id)
            stats['rules_evaluated'] += 1
            if result == 'created':
                stats['alerts_created'] += 1
            elif result == 'updated':
                stats['alerts_updated'] += 1
            elif result == 'resolved':
                stats['alerts_resolved'] += 1
            elif result == 'error':
                stats['errors'] += 1

        logger.info(
            f"Alert evaluation complete: {stats['rules_evaluated']} rules, "
            f"{stats['alerts_created']} created, {stats['alerts_updated']} updated, "
            f"{stats['alerts_resolved']} resolved, {stats['errors']} errors"
        )
        return stats

    def evaluate_rule(self, rule: Dict[str, Any], pipeline_run_id=None) -> str:
        """
        Evaluate a single rule. Returns action taken:
        'created', 'updated', 'resolved', 'unchanged', 'error'
        """
        slug = rule['slug']
        try:
            if rule.get('query'):
                matches = self.repo.execute_rule_query(rule['query'])
            else:
                matches = self.repo.execute_filter_rule(
                    rule.get('target_filters') or [],
                    rule.get('compliance_criteria'),
                )
            return self._evaluate_match_result(rule, matches, pipeline_run_id)
        except Exception as e:
            logger.error(f"  Error evaluating rule {slug}: {e}", exc_info=True)
            return 'error'

    def _evaluate_match_result(
        self,
        rule: Dict[str, Any],
        matches: List[Dict[str, Any]],
        pipeline_run_id=None,
    ) -> str:
        """Given a rule and its match set, create/update/resolve the alert.

        Handles the threshold comparison, subject-identity-unchanged
        short-circuit, and silence-duration check.
        """
        rule_id = rule['id']
        slug = rule['slug']
        match_count = len(matches)

        should_fire = self._should_fire(rule, matches)
        existing = self.repo.get_active_alert_for_rule(rule_id)

        if not should_fire:
            if existing is not None:
                self.repo.resolve_alert(existing['id'])
                # Resolved = no longer firing -> 0 coverage, matching the anomaly
                # path's condition-resolve event (#906 cross-path consistency).
                self._record_event(existing['id'], rule, pipeline_run_id, 'resolved', 0)
                logger.info(f"  Alert resolved: {slug} (condition cleared)")
                return 'resolved'
            return 'unchanged'

        if self._is_silenced(rule):
            logger.debug(f"  Alert silenced: {slug}")
            return 'unchanged'

        # Branch on subject_type: 'asset' (default) keeps today's asset-centric
        # fields; 'integration'/'audit_event' build a typed subjects list instead
        # (one per match, keyed by the query's subject_id/subject_label projection);
        # summary-only types (pipeline_run/metric/system) carry no entities at all.
        subject_type = rule.get('subject_type') or 'asset'
        subjects = None
        assets_by_category = {}
        data_sources = []
        asset_ids = []
        assets = []
        if subject_type == 'asset':
            # Sorted by id, with asset_ids derived from the same list — the delivery
            # payload pairs them by index, so they must not drift.
            assets = sorted(
                ({'id': str(m['id']), 'label': m.get('asset_label')} for m in matches),
                key=lambda a: a['id'])
            asset_ids = [a['id'] for a in assets]
            assets_by_category = self._compute_category_breakdown(matches)
            data_sources = self._compute_data_sources(matches)
            count = len(matches)
        elif subject_type in ('integration', 'audit_event'):
            subjects = [{'id': str(m['subject_id']), 'label': m['subject_label'],
                         'sublabel': m.get('subject_sublabel')} for m in matches]
            count = len(matches)
        else:  # pipeline_run / metric / system — summary-only
            count = 0
        threshold = dict(rule.get('threshold') or {})
        threshold['current_value'] = self._current_value(rule, len(matches))

        if existing is None:
            created = self.repo.create_alert(
                rule_id=rule_id,
                alert_type=rule['type'],
                severity=rule['severity'],
                title=rule['name'],
                description=rule['description'],
                threshold=threshold,
                asset_count=count,
                assets_by_category=assets_by_category,
                data_sources=data_sources,
                asset_ids=asset_ids,
                subject_type=subject_type,
                subjects=subjects,
                source=rule.get('source') or 'system',
            )
            alert_id = created['id']
            self._record_event(alert_id, rule, pipeline_run_id, 'active', count)
            # Resolve labels only once a delivery is actually about to go out (this
            # created/re-fired branch is the only one that reaches _dispatch_delivery),
            # and only for asset-subject alerts, whose rows may omit the label.
            # execute_filter_rule projects asset_label; a rule's own stored SQL
            # (evaluation_type 'sql' — the default) can't be expected to, so its rows
            # arrive without the key at all. Resolve those rather than ship a label-less
            # asset the channels can only render as "Unknown". Keyed on the KEY's
            # absence, not a null value: a filter row's label is legitimately null for
            # an asset with no override/hostname/fqdn/mac/serial/ip.
            # Never fatal: the alert row is already committed, and _dispatch_delivery
            # is the only thing that will ever notify for this firing episode (the next
            # run finds the alert active and returns unchanged/updated, which don't
            # dispatch). A label lookup that fails must cost labels — the channels fall
            # back to "Unknown" — not the notification itself.
            if subject_type == 'asset' and matches and 'asset_label' not in matches[0]:
                try:
                    self._resolve_asset_labels(assets)
                except Exception as e:
                    logger.warning(
                        "  Asset label resolution failed, sending unlabelled: %s", e)
            self._dispatch_delivery(alert_id, rule, severity=rule['severity'],
                                    alert_type=rule['type'], title=rule['name'],
                                    description=rule['description'], threshold=threshold,
                                    asset_count=count, assets_by_category=assets_by_category,
                                    data_sources=data_sources, asset_ids=asset_ids,
                                    assets=assets, created_at=created['created_at'],
                                    subject_type=subject_type, subjects=subjects,
                                    pipeline_run_id=pipeline_run_id)
            logger.info(f"  Alert created: {slug} ({match_count} matches)")
            return 'created'

        new_identity = self._subject_identity(subject_type, asset_ids, subjects)
        existing_identity = self._subject_identity(
            subject_type, sorted([str(i) for i in (existing.get('asset_ids') or [])]),
            existing.get('subjects'))
        if new_identity == existing_identity:
            self._record_event(existing['id'], rule, pipeline_run_id, 'active', count)
            return 'unchanged'

        self.repo.update_alert_assets(
            alert_id=existing['id'],
            asset_count=count,
            assets_by_category=assets_by_category,
            data_sources=data_sources,
            asset_ids=asset_ids,
            threshold=threshold,
            subject_type=subject_type,
            subjects=subjects,
        )
        self._record_event(existing['id'], rule, pipeline_run_id, 'active', count)
        logger.info(f"  Alert updated: {slug} ({match_count} matches)")
        return 'updated'

    @staticmethod
    def _subject_identity(subject_type, asset_ids, subjects):
        """Stable identity for the unchanged-short-circuit, per subject type."""
        if subject_type == 'asset':
            return ('asset', tuple(asset_ids))
        if subject_type in ('integration', 'audit_event'):
            return (subject_type, tuple(sorted(s['id'] for s in (subjects or []))))
        return (subject_type, 'active')  # summary-only: stable while active

    def _record_event(self, alert_id, rule, pipeline_run_id, status, asset_count) -> None:
        """Append a per-run firing snapshot (#906). No-op outside a pipeline run —
        interval/watchdog fires carry no pipeline_run_id, so they get no history."""
        if pipeline_run_id is None:
            return
        self.repo.append_alert_event(
            alert_id=alert_id, rule_id=rule['id'], pipeline_run_id=pipeline_run_id,
            status=status, severity=rule['severity'], asset_count=asset_count,
        )

    def _dispatch_delivery(self, alert_id, rule, *, severity, alert_type, title,
                           description, threshold, asset_count, assets_by_category,
                           data_sources, asset_ids, assets, created_at, subject_type,
                           subjects, pipeline_run_id) -> None:
        """Enqueue outbound deliveries for a newly created/re-fired alert (#917).

        A re-fire is a fresh alerts row (resolve clears the active alert), so the
        'created' branch is exactly the created+re-fired boundary — one delivery per
        firing episode, no per-run spam. Not gated on pipeline_run_id: interval-cadence
        rules notify too. Never breaks alert creation — enqueue failure is swallowed.
        """
        if self.delivery is None:
            return
        try:
            alert = {
                'id': alert_id, 'type': alert_type, 'severity': severity,
                'title': title, 'description': description, 'threshold': threshold,
                'asset_count': asset_count, 'assets_by_category': assets_by_category,
                'data_sources': data_sources, 'asset_ids': asset_ids,
                'assets': assets, 'created_at': created_at,
                'subject_type': subject_type, 'subjects': subjects,
                'pipeline_run_id': pipeline_run_id,
            }
            # The same plain-language sentence the card shows. _compute_reason is a
            # pure read over threshold/subject_type/asset_count/evidence — all already
            # in hand — so the channel and GET /api/alerts cannot disagree.
            alert['reason'] = self._compute_reason(alert)
            self.delivery.enqueue_for_alert(alert, rule)
        except Exception as e:
            logger.error("  Alert delivery enqueue failed (non-fatal): %s", e, exc_info=True)

    def _resolve_asset_labels(self, assets: List[Dict[str, Any]]) -> None:
        """Fill in labels for assets a rule matched via its own SQL, in place.

        Reuses get_asset_details — the query GET /api/alerts already builds the card
        from — so the channel and the card cannot name the same asset differently.
        One batched query per firing SQL rule, never one per asset. Rewrites only the
        label values, so assets stays index-aligned with the asset_ids derived from it.
        """
        rows = self.repo.get_asset_details([a['id'] for a in assets])
        # get_asset_details returns UUID ids; assets keys them as str.
        labels = {str(r['id']): r.get('label') for r in rows}
        for a in assets:
            a['label'] = labels.get(a['id'])

    def _should_fire(
        self,
        rule: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> bool:
        """Return True iff the rule's threshold condition is met."""
        threshold = rule.get('threshold') or {}
        condition = threshold.get('condition')
        match_count = len(matches)

        if not condition or 'value' not in threshold:
            return match_count > 0

        value = threshold['value']
        unit = threshold.get('unit', 'number')

        # Zero matches never exceed a positive threshold — skip the universe query.
        if match_count == 0 and condition == 'exceeds':
            return False

        if unit == 'percent':
            universe = self.repo.count_target_universe(rule.get('target_filters') or [])
            if universe == 0:
                return False
            current = (match_count / universe) * 100
        else:
            current = match_count

        if condition == 'falls_below':
            return current < value
        if condition == 'exceeds':
            return current > value
        return False

    def _current_value(self, rule: Dict[str, Any], match_count: int) -> float:
        """The rule's current measured value in its threshold's unit, stored on
        the alert for the 'current vs threshold' readout.

        Number-unit (or no-unit) thresholds report the raw match count; percent
        -unit thresholds report the matches as a percentage of the target
        universe (rounded to 1 dp) so the readout reads e.g. "9% below 85%"
        rather than the raw count with a stray % sign.
        """
        threshold = rule.get('threshold') or {}
        if threshold.get('unit') == 'percent':
            universe = self.repo.count_target_universe(rule.get('target_filters') or [])
            if universe == 0:
                return 0
            return round((match_count / universe) * 100, 1)
        return match_count

    def _is_silenced(self, rule: Dict[str, Any]) -> bool:
        """True if the most recent resolved alert is within the silence window."""
        duration = rule.get('silence_duration')
        if not duration:
            return False
        recent = self.repo.get_last_resolved_alert_for_rule(rule['id'])
        if not recent or not recent.get('resolved_at'):
            return False
        silenced_until = recent['resolved_at'] + timedelta(minutes=duration)
        return silenced_until > datetime.now(tz=recent['resolved_at'].tzinfo)

    # =========================================================================
    # API responses
    # =========================================================================

    def get_alerts_response(
        self, time_range: str = '7d', severity: str = None,
        alert_type: str = None, status: str = None,
        search: str = None, asset_id: str = None,
        page: int = 1, per_page: int = 20
    ) -> Dict[str, Any]:
        """Build the full GET /api/alerts response."""
        filter_kwargs = dict(
            time_range=time_range, severity=severity,
            alert_type=alert_type, status=status, search=search,
            asset_id=asset_id
        )

        summary = self.repo.get_alerts_summary(**filter_kwargs)
        timeline = self.repo.get_alerts_timeline(**filter_kwargs)
        alerts_raw = self.repo.get_alerts(**filter_kwargs, page=page, per_page=per_page)
        total_count = self.repo.get_alerts_count(**filter_kwargs)

        # Embed asset details into each alert
        alerts = []
        for alert in alerts_raw:
            subject_type = alert.get('subject_type') or 'asset'
            asset_ids = alert.get('asset_ids') or []
            asset_details = (self.repo.get_asset_details(asset_ids)
                             if subject_type == 'asset' and asset_ids else [])
            alert_dict = dict(alert)
            alert_dict['assets'] = [self._serialize_dates(a) for a in asset_details]
            alert_dict['reason'] = self._compute_reason(alert_dict)
            # DB stores source as 'system' or 'user' (matching alert_rules.source).
            # Frontend expects 'system' or 'custom'.
            if alert_dict.get('source') == 'user':
                alert_dict['source'] = 'custom'
            if alert_dict.get('last_pipeline_run_id') is not None:
                alert_dict['last_pipeline_run_id'] = str(alert_dict['last_pipeline_run_id'])
            # Remove raw asset_ids from response (replaced by assets array)
            alert_dict.pop('asset_ids', None)
            alert_dict.pop('rule_id', None)
            self._serialize_dates(alert_dict)
            alerts.append(alert_dict)

        return {
            'summary': summary,
            'timeline': timeline,
            'alerts': alerts,
            'latest_pipeline_run_id': self.repo.get_latest_pipeline_run_id(),
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_pages': math.ceil(total_count / per_page) if per_page > 0 else 0,
                'total_items': total_count,
            }
        }

    def get_alert_history(self, alert_id: str, days: int = 90) -> Dict[str, Any]:
        """Firing-history series for the alert drilldown (#905)."""
        # Bound the window: a negative days yields an empty (future) window and a
        # huge one can overflow the Postgres interval -> query error.
        days = max(1, min(days, 730))
        rows = self.repo.get_alert_history(alert_id, days=days)
        history = []
        for row in rows:
            point = dict(row)
            if point.get('pipeline_run_id') is not None:
                point['pipeline_run_id'] = str(point['pipeline_run_id'])
            self._serialize_dates(point)
            history.append(point)
        return {'alert_id': alert_id, 'history': history}

    def update_alert_status(self, alert_id: str, status: str) -> Optional[Dict[str, Any]]:
        """Update alert status (acknowledge/resolve)."""
        result = self.repo.update_alert_status(alert_id, status)
        if result:
            self._serialize_dates(result)
        return result

    def get_all_rules(self) -> Dict[str, Any]:
        """Get all rules for settings page."""
        rules = self.repo.get_all_rules()
        return {'rules': rules}

    def get_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Get a single rule by ID."""
        return self.repo.get_rule(rule_id)

    def toggle_rule(self, rule_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
        """Toggle a rule. Auto-resolves alerts when disabling."""
        if not enabled:
            self.repo.resolve_alerts_for_rule(rule_id)
        return self.repo.toggle_rule(rule_id, enabled)

    def create_rule(self, payload: Dict[str, Any], created_by: str) -> Dict[str, Any]:
        """Create a new user alert rule. Payload is pre-validated (Pydantic)."""
        return self.repo.create_rule(payload, created_by=created_by)

    def update_rule(
        self, rule_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a user rule. Raises SystemRuleImmutableError for system rules."""
        return self.repo.update_rule(rule_id, updates)

    def update_anomaly_threshold(
        self, rule_id: str, patch: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Merge a partial anomaly threshold into a system anomaly rule (sensitivity
        tuning). Returns None if the rule is missing or not anomaly."""
        return self.repo.merge_anomaly_threshold(rule_id, patch)

    def delete_rule(self, rule_id: str) -> bool:
        """Delete a user rule. Raises SystemRuleImmutableError for system rules."""
        return self.repo.delete_rule(rule_id)

    def preview_rule(
        self,
        target_filters: List[Dict[str, Any]],
        compliance_criteria: Optional[Dict[str, Any]],
    ) -> Dict[str, int]:
        """Dry-run: count matching assets without creating an alert."""
        matches = self.repo.execute_filter_rule(target_filters, compliance_criteria)
        return {'total_matching': len(matches)}

    # =========================================================================
    # Helpers
    # =========================================================================

    @staticmethod
    def _serialize_dates(d: dict) -> dict:
        """Convert datetime values to ISO format strings for JSON serialization."""
        for key, val in d.items():
            if isinstance(val, datetime):
                d[key] = val.isoformat()
        return d

    @staticmethod
    def _compute_reason(alert: dict) -> str:
        """One-line, plain 'why this fired' — always distinct from the description.
        Anomaly alerts surface their humanized narrative; summary-only alerts build
        from threshold metadata (or a generic per-type line) — never the description.
        """
        # 1. Anomaly: the humanized instance story lives in evidence.narrative.
        narrative = (alert.get('evidence') or {}).get('narrative')
        if narrative:
            return narrative

        threshold = alert.get('threshold') or {}
        subject_type = alert.get('subject_type') or 'asset'
        asset_count = alert.get('asset_count', 0)

        # 2. Condition-shape thresholds (existing rules — keep wording byte-identical).
        condition = threshold.get('condition')
        value = threshold.get('value')
        if condition in ('falls_below', 'exceeds') and value is not None:
            current = threshold.get('current_value', asset_count)
            comparator = 'below' if condition == 'falls_below' else 'above'
            if threshold.get('unit') == 'percent':
                return f"Current value {current}% is {comparator} the {value}% threshold"
            # 'assets' stays literal (unpluralized) here to match the historical
            # asset-subject wording byte-for-byte; non-asset subject types must
            # never say "assets".
            if subject_type == 'asset':
                return f"{current} assets — {comparator} the threshold of {value}"
            return f"{current} — {comparator} the threshold of {value}"

        # 3. Operator-shape summary thresholds: {"metric","operator",">","value","unit"}.
        if (subject_type in ('pipeline_run', 'metric', 'system')
                and threshold.get('operator') in ('>', '>=', '<', '<=') and value is not None):
            return AlertsService._summary_reason(threshold)

        # 4. Entity nouns.
        if subject_type == 'asset':
            noun = 'asset' if asset_count == 1 else 'assets'
            return f"{asset_count} {noun} matched this rule"  # byte-identical to history
        if subject_type in ('integration', 'audit_event'):
            base = 'data source' if subject_type == 'integration' else 'event'
            label = base if asset_count == 1 else base + 's'
            return f"{asset_count} {label} — see below"

        # 5. Summary-only generic fallback — never the description.
        return AlertsService._generic_summary_reason(subject_type)

    @staticmethod
    def _summary_reason(threshold: dict) -> str:
        """Plain sentence for operator-shape summary thresholds (no live counts
        persisted, so this describes the limit crossed, not "N down from M")."""
        metric = threshold.get('metric') or ''
        amount = (f"{threshold.get('value')}%" if threshold.get('unit') == 'percent'
                  else f"{threshold.get('value')}")
        verb = {'>': 'went past', '>=': 'reached', '<': 'fell below', '<=': 'dropped to'}.get(
            threshold.get('operator'), 'crossed')
        phrases = {
            'assets_disappeared_pct': 'the share of assets that disappeared this run',
            'secured_to_monitored_pct': 'the drop in Secured-tier coverage this check',
        }
        subject = phrases.get(metric) or (metric.replace('_', ' ') if metric else 'this run')
        subject = subject[:1].upper() + subject[1:]
        return f"{subject} {verb} the {amount} limit."

    @staticmethod
    def _generic_summary_reason(subject_type: str) -> str:
        """Last-resort per-type sentence when threshold metadata is too thin to
        build a specific reason (e.g. chronicle-silence's {"min": 1})."""
        return {
            'pipeline_run': 'The latest pipeline run matched this rule.',
            'metric': 'A tracked metric crossed its threshold this check.',
            'system': 'A system health check raised this alert.',
        }.get(subject_type, 'This rule matched.')

    @staticmethod
    def _compute_category_breakdown(matches: List[Dict]) -> Dict[str, int]:
        """Count assets by category from match results."""
        breakdown = {}
        for m in matches:
            cat = m.get('category') or 'Unknown'
            breakdown[cat] = breakdown.get(cat, 0) + 1
        return breakdown

    @staticmethod
    def _compute_data_sources(matches: List[Dict]) -> List[str]:
        """Extract unique data sources from match results (if available)."""
        # Asset-level rules don't return data_source directly;
        # this is a best-effort extraction from the assets table
        sources = set()
        for m in matches:
            ds = m.get('data_source')
            if ds:
                if isinstance(ds, list):
                    sources.update(ds)
                else:
                    sources.add(ds)
        return sorted(sources)
