# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Alerts Repository

Data access layer for alert_rules and alerts tables.
Uses PostgreSQLAdapter for direct SQL queries (no ORM).
"""

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from src.database.postgres_adapter import PostgreSQLAdapter, _clean_nan
from src.modules.fields.catalog import VIRTUAL_FIELDS
from src.modules.shared.asset_query_compiler import AssetQueryCompiler

logger = logging.getLogger(__name__)

# The one canonical asset label — COALESCE(override, hostname, fqdn, mac, sn, ip).
# Sourced from the field catalog rather than re-copied: the expression already lives
# there and in anomaly's ASSET_LABELS_SQL, and a third divergent copy is exactly how
# MAC normalization ended up with four.
_ASSET_LABEL_EXPR = VIRTUAL_FIELDS['asset_label']['sql']

# One expression, two consumers, two names — each alias is the key its own consumer
# reads, so neither gets to be wrong. get_asset_details feeds GET /api/alerts, whose
# rows the card reads as `asset.label`; execute_filter_rule feeds the rule dispatch,
# which reads `asset_label` off the match row to build the delivery payload.
_ASSET_LABEL_AS_LABEL = f"{_ASSET_LABEL_EXPR} AS label"
_ASSET_LABEL_SQL = f"{_ASSET_LABEL_EXPR} AS asset_label"


class SystemRuleImmutableError(Exception):
    """Raised when attempting to edit/delete a system-owned alert rule."""


def _slugify(name: str) -> str:
    """Convert a rule name to a URL-safe slug fragment."""
    s = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return s or 'rule'


def _to_compiler_filter(cond: Dict[str, Any]) -> Dict[str, Any]:
    """Translate alert-rule condition ({field, op, value}) to the compiler's
    shape ({field_id, operator, value}). The operator spelling is normalized to
    its canonical registry code so a label/code mix can't reach the compiler as
    a phantom; an unresolvable spelling is passed through unchanged for the
    compiler's loud-fail to reject (never silently dropped here).
    """
    from src.modules.fields.operators import resolve as _resolve_op, OperatorResolutionError
    op = cond['op']
    try:
        op = _resolve_op(op).code
    except OperatorResolutionError:
        pass
    return {
        'field_id': cond['field'],
        'operator': op,
        'value': cond['value'],
    }


class AlertsRepository:
    """Repository for alerts and alert_rules tables."""

    # Map time_range shorthand to PostgreSQL interval. The literal includes the
    # `interval` keyword so it slots straight into `now() - {interval}`. The read
    # methods also accept the sentinel 'all' (no window) and explicit start/end
    # bounds in addition to these presets — see _alert_window.
    TIME_RANGES = {
        '24h': "interval '24 hours'",
        '7d': "interval '7 days'",
        '30d': "interval '30 days'",
        '90d': "interval '90 days'",
    }

    def __init__(self, db_adapter: PostgreSQLAdapter, query_compiler: Optional[AssetQueryCompiler] = None):
        self.db = db_adapter
        self._compiler = query_compiler  # required only when filter-based rules are evaluated

    # =========================================================================
    # Alert Rules
    # =========================================================================

    def get_enabled_rules(self, cadence: str = 'pipeline') -> List[Dict[str, Any]]:
        """Get enabled alert rules for the given evaluation cadence.

        `cadence` selects between the in-pipeline evaluator ('pipeline', the
        default — Stage 12 keeps its exact behavior) and the scheduler worker's
        5-minute interval job ('interval'). The pipeline path therefore never
        picks up an interval rule, and vice versa.

        Explicitly excludes rules with evaluation_type='external', which are
        evaluated out-of-band (e.g., by the pipeline watchdog) and must not
        be evaluated here. Also excludes source='prometheus' rows: their
        `query` holds PromQL (prometheus_sync never set evaluation_type, so
        they default to 'sql') and their alert lifecycle is driven by the
        inbound Alertmanager webhook — DB-evaluating them just fails the
        SELECT-only guard on every run (11 such errors per run observed on
        the dev DB, 2026-07-03).
        """
        query = """
            SELECT id, name, slug, type, severity, description,
                   query, threshold, enabled, source,
                   target_filters, compliance_criteria, silence_duration, created_by,
                   evaluation_type, subject_type
            FROM alert_rules
            WHERE enabled = true
              AND evaluation_type IN ('sql', 'filter')
              AND source != 'prometheus'
              AND evaluation_cadence = %(cadence)s
            ORDER BY type, severity
        """
        results = self.db.execute_query(query, {'cadence': cadence})
        return results if results else []

    def get_all_rules(self) -> List[Dict[str, Any]]:
        """Get all alert rules (for settings page). Includes external rules."""
        query = """
            SELECT id, name, slug, type, severity, description,
                   query, threshold, enabled, source,
                   target_filters, compliance_criteria, silence_duration, created_by,
                   evaluation_type, detector_key, created_at, updated_at
            FROM alert_rules
            ORDER BY type, severity, name
        """
        results = self.db.execute_query(query)
        return results if results else []

    def get_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Get a single alert rule by ID."""
        query = """
            SELECT id, name, slug, type, severity, description,
                   query, threshold, enabled, source,
                   target_filters, compliance_criteria, silence_duration, created_by,
                   evaluation_type, detector_key, created_at, updated_at
            FROM alert_rules
            WHERE id = %(rule_id)s
        """
        results = self.db.execute_query(query, {'rule_id': rule_id})
        return results[0] if results else None

    def get_rule_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get a single alert rule by slug."""
        query = """
            SELECT id, name, slug, type, severity, description,
                   query, threshold, enabled, source,
                   target_filters, compliance_criteria, silence_duration, created_by,
                   evaluation_type, detector_key, created_at, updated_at
            FROM alert_rules
            WHERE slug = %(slug)s
        """
        results = self.db.execute_query(query, {'slug': slug})
        return results[0] if results else None

    def create_rule(self, payload: Dict[str, Any], created_by: str) -> Dict[str, Any]:
        """Create a user alert rule. Caller must pass validated payload."""
        # Generate slug from name (lowercase, hyphenated, unique suffix if needed)
        slug = _slugify(payload['name']) + '-' + str(uuid.uuid4())[:8]
        query = """
            INSERT INTO alert_rules (
                name, slug, description, type, severity,
                target_filters, compliance_criteria, threshold,
                silence_duration, enabled, source, created_by,
                evaluation_type
            ) VALUES (
                %(name)s, %(slug)s, %(description)s, %(type)s, %(severity)s,
                %(target_filters)s::jsonb, %(compliance_criteria)s::jsonb, %(threshold)s::jsonb,
                %(silence_duration)s, %(enabled)s, 'user', %(created_by)s::uuid,
                'filter'
            )
            RETURNING id, name, slug, type, severity, description,
                      target_filters, compliance_criteria, threshold,
                      silence_duration, enabled, source, created_by,
                      evaluation_type, created_at, updated_at
        """
        params = {
            'name': payload['name'],
            'slug': slug,
            'description': payload.get('description', ''),
            'type': payload['type'],
            'severity': payload['severity'],
            'target_filters': json.dumps(payload['target_filters']),
            'compliance_criteria': json.dumps(payload['compliance_criteria']) if payload.get('compliance_criteria') else None,
            'threshold': json.dumps(payload['threshold']) if payload.get('threshold') else None,
            'silence_duration': payload.get('silence_duration'),
            'enabled': payload.get('enabled', True),
            'created_by': created_by,
        }
        results = self.db.execute_query(query, params)
        return results[0] if results else None

    def _get_rule_source(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Return {source, evaluation_type} for a rule, or None if it's missing."""
        rows = self.db.execute_query(
            "SELECT source, evaluation_type FROM alert_rules WHERE id = %(rule_id)s",
            {'rule_id': rule_id},
        )
        return rows[0] if rows else None

    def update_rule(self, rule_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a user rule. System rules are immutable, EXCEPT anomaly system
        rules, which accept an enabled toggle here; threshold tuning goes through
        merge_anomaly_threshold."""
        rule = self._get_rule_source(rule_id)
        if rule is None:
            return None
        if rule['source'] == 'system':
            anomaly_tunable = (rule.get('evaluation_type') == 'anomaly'
                               and set(updates) <= {'enabled'})
            if not anomaly_tunable:
                raise SystemRuleImmutableError(f"Rule {rule_id} is system-owned")

        # Build SET clause from only the provided fields
        allowed = {
            'name', 'description', 'type', 'severity',
            'target_filters', 'compliance_criteria', 'threshold',
            'silence_duration', 'enabled',
        }
        set_parts = []
        params: Dict[str, Any] = {'rule_id': rule_id}
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key in ('target_filters', 'compliance_criteria', 'threshold'):
                params[key] = json.dumps(value) if value is not None else None
                set_parts.append(f"{key} = %({key})s::jsonb")
            else:
                params[key] = value
                set_parts.append(f"{key} = %({key})s")
        if not set_parts:
            return self.get_rule(rule_id)

        set_parts.append("updated_at = now()")
        sql = f"""
            UPDATE alert_rules
            SET {', '.join(set_parts)}
            WHERE id = %(rule_id)s
            RETURNING id, name, slug, type, severity, description,
                      target_filters, compliance_criteria, threshold,
                      silence_duration, enabled, source, created_by,
                      evaluation_type, detector_key, created_at, updated_at
        """
        rows = self.db.execute_query(sql, params)
        return rows[0] if rows else None

    def merge_anomaly_threshold(self, rule_id: str,
                                patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Shallow-merge `patch` into a system anomaly rule's threshold jsonb.
        Postgres `||` overwrites the provided keys and preserves the rest, so a
        partial edit (e.g. {'pct_medium': 0.5}) never drops other params. Returns
        None when the rule is missing or not a system anomaly rule; a no-op patch
        returns the current row unchanged."""
        rule = self._get_rule_source(rule_id)
        if (rule is None or rule.get('source') != 'system'
                or rule.get('evaluation_type') != 'anomaly'):
            return None
        if not patch:
            return self.get_rule(rule_id)
        sql = """
            UPDATE alert_rules
               SET threshold = COALESCE(threshold, '{}'::jsonb) || %(patch)s::jsonb,
                   updated_at = now()
             WHERE id = %(rule_id)s AND source = 'system'
               AND evaluation_type = 'anomaly'
            RETURNING id, name, slug, type, severity, description,
                      target_filters, compliance_criteria, threshold,
                      silence_duration, enabled, source, created_by,
                      evaluation_type, detector_key, created_at, updated_at
        """
        rows = self.db.execute_query(
            sql, {'rule_id': rule_id, 'patch': json.dumps(_clean_nan(patch))})
        return rows[0] if rows else None

    def delete_rule(self, rule_id: str) -> bool:
        """Delete a user rule and resolve its active alerts. Raises for system rules."""
        rule = self._get_rule_source(rule_id)
        if rule is None:
            return False
        if rule['source'] == 'system':
            raise SystemRuleImmutableError(f"Rule {rule_id} is system-owned")

        # Resolve-then-delete in a single statement so partial failure can't
        # leave the rule's alerts resolved while the rule itself survives.
        query = """
            WITH resolved AS (
                UPDATE alerts
                SET status = 'resolved', resolved_at = now(), updated_at = now()
                WHERE rule_id = %(rule_id)s AND status IN ('active', 'acknowledged')
                RETURNING id
            )
            DELETE FROM alert_rules WHERE id = %(rule_id)s RETURNING id
        """
        rows = self.db.execute_query(query, {'rule_id': rule_id})
        return bool(rows)

    def toggle_rule(self, rule_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
        """Enable or disable a rule. Returns updated rule."""
        query = """
            UPDATE alert_rules
            SET enabled = %(enabled)s, updated_at = now()
            WHERE id = %(rule_id)s
            RETURNING id, enabled, updated_at
        """
        results = self.db.execute_query(query, {'rule_id': rule_id, 'enabled': enabled})
        return results[0] if results else None

    # =========================================================================
    # Alerts — Evaluation (write path)
    # =========================================================================

    def get_last_resolved_alert_for_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Get the most recent resolved alert for a rule (for silence-duration check)."""
        query = """
            SELECT id, resolved_at
            FROM alerts
            WHERE rule_id = %(rule_id)s AND status = 'resolved'
            ORDER BY resolved_at DESC NULLS LAST
            LIMIT 1
        """
        rows = self.db.execute_query(query, {'rule_id': rule_id})
        return rows[0] if rows else None

    def get_active_alert_for_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Get the active or acknowledged alert for a rule (at most one).

        Selects ``subject_type``/``subjects`` (psycopg2 returns the jsonb
        ``subjects`` pre-parsed as a Python list) so the service's per-subject
        unchanged-short-circuit can compare a typed-list alert's existing
        subjects against the fresh matches — without them a non-asset alert
        would be rewritten every evaluation.
        """
        query = """
            SELECT id, status, asset_count, asset_ids, updated_at,
                   subject_type, subjects
            FROM alerts
            WHERE rule_id = %(rule_id)s
              AND status IN ('active', 'acknowledged')
            LIMIT 1
        """
        results = self.db.execute_query(query, {'rule_id': rule_id})
        return results[0] if results else None

    def create_alert(
        self, rule_id: str, alert_type: str, severity: str,
        title: str, description: str, threshold: dict,
        asset_count: int, assets_by_category: dict,
        data_sources: list, asset_ids: list,
        subject_type: str = 'asset', subjects: Optional[list] = None,
        source: str = 'system',
        evidence: Optional[dict] = None, fingerprint: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a new alert. Returns ``{'id': ..., 'created_at': ...}``, or None
        if the insert returned no row.

        ``created_at`` is returned because outbound delivery (#917) needs the alert's
        real detect-time: the column defaults to Postgres now() = transaction-start,
        and the pipeline evaluates alerts inside one long transaction, so a
        dispatch-time stamp would disagree with the UI by minutes.

        ``evidence`` (anomaly explainability payload) and ``fingerprint`` (dedup
        key) are optional so existing rule/prometheus callers are unaffected.
        """
        query = """
            INSERT INTO alerts (
                rule_id, type, severity, title, description, threshold,
                asset_count, assets_by_category, data_sources, asset_ids, source,
                subject_type, subjects, evidence, fingerprint
            ) VALUES (
                %(rule_id)s, %(type)s, %(severity)s, %(title)s, %(description)s,
                %(threshold)s::jsonb, %(asset_count)s, %(assets_by_category)s::jsonb,
                %(data_sources)s, %(asset_ids)s::uuid[], %(source)s,
                %(subject_type)s, %(subjects)s::jsonb, %(evidence)s::jsonb, %(fingerprint)s
            )
            RETURNING id, created_at
        """
        params = {
            'rule_id': rule_id, 'type': alert_type, 'severity': severity,
            'title': title, 'description': description,
            'threshold': json.dumps(threshold),
            'asset_count': asset_count,
            'assets_by_category': json.dumps(assets_by_category),
            'data_sources': data_sources, 'asset_ids': asset_ids,
            'source': source,
            'subject_type': subject_type,
            'subjects': json.dumps(subjects) if subjects is not None else None,
            # evidence carries computed floats — _clean_nan strips NaN so
            # psycopg2 emits valid JSON (see CLAUDE.md jsonb gotcha).
            'evidence': json.dumps(_clean_nan(evidence)) if evidence else None,
            'fingerprint': fingerprint,
        }
        results = self.db.execute_query(query, params)
        return dict(results[0]) if results else None

    def update_alert_assets(
        self, alert_id: str, asset_count: int,
        assets_by_category: dict, data_sources: list,
        asset_ids: list, threshold: dict,
        subject_type: str = 'asset', subjects: Optional[list] = None
    ) -> None:
        """Update an existing alert with fresh asset data."""
        query = """
            UPDATE alerts
            SET asset_count = %(asset_count)s,
                assets_by_category = %(assets_by_category)s::jsonb,
                data_sources = %(data_sources)s,
                asset_ids = %(asset_ids)s::uuid[],
                threshold = %(threshold)s::jsonb,
                subject_type = %(subject_type)s,
                subjects = %(subjects)s::jsonb,
                updated_at = now()
            WHERE id = %(alert_id)s
        """
        params = {
            'alert_id': alert_id, 'asset_count': asset_count,
            'assets_by_category': json.dumps(assets_by_category),
            'data_sources': data_sources, 'asset_ids': asset_ids,
            'threshold': json.dumps(threshold),
            'subject_type': subject_type,
            'subjects': json.dumps(subjects) if subjects is not None else None,
        }
        self.db.execute_query(query, params, fetch=False)

    def update_alert_evidence(self, alert_id: str, severity: str, evidence: Optional[dict]) -> None:
        """Refresh a condition alert's severity + evidence while it persists."""
        query = """
            UPDATE alerts
            SET severity = %(severity)s, evidence = %(evidence)s::jsonb, updated_at = now()
            WHERE id = %(alert_id)s
        """
        self.db.execute_query(query, {
            'alert_id': alert_id, 'severity': severity,
            'evidence': json.dumps(_clean_nan(evidence)) if evidence else None,
        }, fetch=False)

    def resolve_alert(self, alert_id: str) -> None:
        """Resolve an alert (condition cleared)."""
        query = """
            UPDATE alerts
            SET status = 'resolved', resolved_at = now(), updated_at = now()
            WHERE id = %(alert_id)s
        """
        self.db.execute_query(query, {'alert_id': alert_id}, fetch=False)

    def resolve_alerts_for_rule(self, rule_id: str) -> None:
        """Resolve all active/acknowledged alerts for a rule (when rule is disabled)."""
        query = """
            UPDATE alerts
            SET status = 'resolved', resolved_at = now(), updated_at = now()
            WHERE rule_id = %(rule_id)s
              AND status IN ('active', 'acknowledged')
        """
        self.db.execute_query(query, {'rule_id': rule_id}, fetch=False)

    def append_alert_event(
        self, alert_id: str, rule_id: str, pipeline_run_id: str,
        status: str, severity: str, asset_count: int,
    ) -> None:
        """Append a per-run firing snapshot and stamp the alert's latest run (#906).

        Called once per pipeline evaluation of an alert. ``pipeline_run_id`` is
        ``pipeline_runs.id`` — the run identity the UI knows and can order by time.

        The event INSERT and the ``last_pipeline_run_id`` stamp run as a single
        data-modifying CTE: one round trip, and atomic — the stamp can never lag
        the event row (matters in s20, which runs post-commit / unscoped).
        """
        self.db.execute_query(
            """
            WITH ev AS (
                INSERT INTO alert_events
                    (alert_id, rule_id, pipeline_run_id, status, severity, asset_count)
                VALUES
                    (%(alert_id)s::uuid, %(rule_id)s::uuid, %(pipeline_run_id)s::uuid,
                     %(status)s, %(severity)s, %(asset_count)s)
            )
            UPDATE alerts SET last_pipeline_run_id = %(pipeline_run_id)s::uuid
            WHERE id = %(alert_id)s::uuid
            """,
            {'alert_id': str(alert_id), 'rule_id': str(rule_id),
             'pipeline_run_id': str(pipeline_run_id), 'status': status,
             'severity': severity, 'asset_count': asset_count},
            fetch=False,
        )

    def get_alert_history(
        self, alert_id: str, days: int = 90, limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Per-run firing series for one alert, joined to pipeline_runs for the
        time axis (#905). Returns the most recent ``limit`` events (a dense-series
        alert can exceed the cap within the window), presented oldest→newest so the
        timechart's right edge is always the latest run."""
        query = """
            SELECT pipeline_run_id, status, asset_count, severity,
                   created_at, started_at, completed_at
            FROM (
                SELECT ae.pipeline_run_id, ae.status, ae.asset_count, ae.severity,
                       ae.created_at, pr.started_at, pr.completed_at
                FROM alert_events ae
                JOIN pipeline_runs pr ON pr.id = ae.pipeline_run_id
                WHERE ae.alert_id = %(alert_id)s::uuid
                  AND ae.created_at >= now() - INTERVAL '1 day' * %(days)s
                ORDER BY pr.started_at DESC NULLS LAST
                LIMIT %(limit)s
            ) sub
            ORDER BY started_at ASC NULLS LAST
        """
        rows = self.db.execute_query(
            query, {'alert_id': alert_id, 'days': days, 'limit': limit})
        return rows if rows else []

    def get_latest_pipeline_run_id(self) -> Optional[str]:
        """The most recent completed pipeline run — the anchor #904 highlights against."""
        rows = self.db.execute_query(
            """
            SELECT id FROM pipeline_runs
            WHERE status = 'completed'
            ORDER BY completed_at DESC NULLS LAST
            LIMIT 1
            """
        )
        return str(rows[0]['id']) if rows else None

    # =========================================================================
    # Alerts — Read path (API)
    # =========================================================================

    def _alert_window(
        self, time_range: str, start: Any = None, end: Any = None,
    ) -> tuple:
        """Build the created_at window clause(s) + params for the read methods.

        Precedence: an explicit ``start``/``end`` bound (either or both) defines
        an arbitrary window and overrides the preset; the preset ``'all'``
        applies NO window; otherwise a ``TIME_RANGES`` interval is used (falling
        back to 7 days for an unknown preset — callers that want a loud error
        validate before calling). Bounds are parameterized; the interval is a
        controlled constant interpolated as today. Returns ``(conditions, params)``
        where ``conditions`` may be empty (the 'all' case)."""
        conditions: List[str] = []
        params: Dict[str, Any] = {}
        if start is not None or end is not None:
            if start is not None:
                conditions.append("created_at >= %(w_start)s")
                params['w_start'] = start
            if end is not None:
                conditions.append("created_at <= %(w_end)s")
                params['w_end'] = end
        elif time_range != 'all':
            interval = self.TIME_RANGES.get(time_range, "interval '7 days'")
            conditions.append(f"created_at >= now() - {interval}")
        return conditions, params

    def get_alerts(
        self, time_range: str = '7d', severity: str = None,
        alert_type: str = None, status: str = None,
        search: str = None, asset_id: str = None,
        page: int = 1, per_page: int = 20,
        *, start: Any = None, end: Any = None,
    ) -> List[Dict[str, Any]]:
        """Get paginated, filtered alerts.

        ``start``/``end`` (datetimes) define an arbitrary window that overrides
        ``time_range``; ``time_range='all'`` skips the window entirely. Both are
        keyword-only so existing positional callers are unaffected.
        """
        conditions, params = self._alert_window(time_range, start, end)

        if severity:
            conditions.append("severity = %(severity)s")
            params['severity'] = severity
        if alert_type:
            conditions.append("type = %(type)s")
            params['type'] = alert_type
        if status:
            conditions.append("status = %(status)s")
            params['status'] = status
        if search:
            conditions.append("(title ILIKE %(search)s OR description ILIKE %(search)s)")
            params['search'] = f'%{search}%'
        if asset_id:
            conditions.append("%(asset_id)s::uuid = ANY(asset_ids)")
            params['asset_id'] = asset_id

        where = " AND ".join(conditions) if conditions else "TRUE"
        offset = (page - 1) * per_page
        params['limit'] = per_page
        params['offset'] = offset

        query = f"""
            SELECT id, rule_id, type, severity, title, description, threshold,
                   status, asset_count, assets_by_category, data_sources, asset_ids,
                   source, created_at, updated_at, resolved_at, evidence,
                   last_pipeline_run_id, subject_type, subjects
            FROM alerts
            WHERE {where}
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3 WHEN 'low' THEN 4
                END,
                created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        results = self.db.execute_query(query, params)
        return results if results else []

    def get_alerts_count(
        self, time_range: str = '7d', severity: str = None,
        alert_type: str = None, status: str = None, search: str = None,
        asset_id: str = None,
        *, start: Any = None, end: Any = None,
    ) -> int:
        """Get total count of filtered alerts (for pagination).

        Shares _alert_window with get_alerts/get_alerts_summary so the
        paginator total tracks the SAME window — otherwise time_range='all'
        (or a custom start/end) would count a different set than the list,
        breaking the page count.
        """
        conditions, params = self._alert_window(time_range, start, end)

        if severity:
            conditions.append("severity = %(severity)s")
            params['severity'] = severity
        if alert_type:
            conditions.append("type = %(type)s")
            params['type'] = alert_type
        if status:
            conditions.append("status = %(status)s")
            params['status'] = status
        if search:
            conditions.append("(title ILIKE %(search)s OR description ILIKE %(search)s)")
            params['search'] = f'%{search}%'
        if asset_id:
            conditions.append("%(asset_id)s::uuid = ANY(asset_ids)")
            params['asset_id'] = asset_id

        where = " AND ".join(conditions) if conditions else "TRUE"
        query = f"SELECT COUNT(*) as cnt FROM alerts WHERE {where}"
        results = self.db.execute_query(query, params)
        return results[0]['cnt'] if results else 0

    def get_alerts_summary(
        self, time_range: str = '7d', severity: str = None,
        alert_type: str = None, status: str = None, search: str = None,
        asset_id: str = None,
        *, start: Any = None, end: Any = None,
    ) -> Dict[str, Any]:
        """Get alert summary counts by type and severity.

        ``total`` is the full window count (not a page), so callers can use it as
        the pagination total. ``start``/``end``/``'all'`` window semantics match
        get_alerts.
        """
        conditions, params = self._alert_window(time_range, start, end)

        if severity:
            conditions.append("severity = %(severity)s")
            params['severity'] = severity
        if alert_type:
            conditions.append("type = %(type)s")
            params['type'] = alert_type
        if status:
            conditions.append("status = %(status)s")
            params['status'] = status
        if search:
            conditions.append("(title ILIKE %(search)s OR description ILIKE %(search)s)")
            params['search'] = f'%{search}%'
        if asset_id:
            conditions.append("%(asset_id)s::uuid = ANY(asset_ids)")
            params['asset_id'] = asset_id

        where = " AND ".join(conditions) if conditions else "TRUE"
        query = f"""
            SELECT type, severity, COUNT(*) as cnt
            FROM alerts
            WHERE {where}
            GROUP BY type, severity
        """
        rows = self.db.execute_query(query, params)
        rows = rows if rows else []

        total = 0
        by_type = {'security': 0, 'compliance': 0, 'operational': 0, 'data_quality': 0}
        by_severity = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}

        for row in rows:
            cnt = row['cnt']
            total += cnt
            by_type[row['type']] = by_type.get(row['type'], 0) + cnt
            by_severity[row['severity']] = by_severity.get(row['severity'], 0) + cnt

        return {'total': total, 'by_type': by_type, 'by_severity': by_severity}

    def get_alerts_timeline(
        self, time_range: str = '7d', severity: str = None,
        alert_type: str = None, status: str = None, search: str = None,
        asset_id: str = None,
        *, start: Any = None, end: Any = None,
        bucket: str = 'day', split_by_source: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get alerts grouped by time bucket for the timeline chart.

        ``bucket`` is 'day' (default — keeps the DATE() back-compat output) or
        'hour'. ``split_by_source`` adds a per-bucket ``by_source`` breakdown
        keyed by the RAW ``alerts.source`` value (system/user/prometheus)
        alongside ``by_severity`` — presentation layers map 'user'→'custom' as
        they do for the alert list. ``start``/``end``/``'all'`` window semantics
        match get_alerts.
        """
        conditions, params = self._alert_window(time_range, start, end)

        if severity:
            conditions.append("severity = %(severity)s")
            params['severity'] = severity
        if alert_type:
            conditions.append("type = %(type)s")
            params['type'] = alert_type
        if status:
            conditions.append("status = %(status)s")
            params['status'] = status
        if search:
            conditions.append("(title ILIKE %(search)s OR description ILIKE %(search)s)")
            params['search'] = f'%{search}%'
        if asset_id:
            conditions.append("%(asset_id)s::uuid = ANY(asset_ids)")
            params['asset_id'] = asset_id

        where = " AND ".join(conditions) if conditions else "TRUE"
        # 'day' keeps DATE() (a date → 'YYYY-MM-DD') for the existing UI; 'hour'
        # truncates to the hour. The expression is a controlled enum, never user
        # text, so interpolation is safe.
        bucket_expr = (
            "date_trunc('hour', created_at)" if bucket == 'hour'
            else "DATE(created_at)"
        )
        source_sel = ", source" if split_by_source else ""
        query = f"""
            SELECT {bucket_expr} as bucket, severity{source_sel}, COUNT(*) as cnt
            FROM alerts
            WHERE {where}
            GROUP BY {bucket_expr}, severity{source_sel}
            ORDER BY bucket
        """
        rows = self.db.execute_query(query, params)
        rows = rows if rows else []

        # Group by bucket. Output keeps the 'date' key the UI/blueprint expect.
        timeline: Dict[str, Any] = {}
        for row in rows:
            date_str = str(row['bucket'])
            if date_str not in timeline:
                entry: Dict[str, Any] = {
                    'date': date_str, 'total': 0,
                    'by_severity': {'critical': 0, 'high': 0,
                                    'medium': 0, 'low': 0},
                }
                if split_by_source:
                    entry['by_source'] = {}
                timeline[date_str] = entry
            entry = timeline[date_str]
            entry['total'] += row['cnt']
            sev = row.get('severity')
            if sev in entry['by_severity']:
                entry['by_severity'][sev] += row['cnt']
            if split_by_source:
                src = row.get('source') or 'unknown'
                entry['by_source'][src] = entry['by_source'].get(src, 0) + row['cnt']

        return list(timeline.values())

    def update_alert_status(self, alert_id: str, status: str) -> Optional[Dict[str, Any]]:
        """Update alert status (e.g., acknowledge). Returns updated alert."""
        resolved_clause = ", resolved_at = now()" if status == 'resolved' else ""
        query = f"""
            UPDATE alerts
            SET status = %(status)s, updated_at = now(){resolved_clause}
            WHERE id = %(alert_id)s
            RETURNING id, status, updated_at
        """
        results = self.db.execute_query(query, {'alert_id': alert_id, 'status': status})
        return results[0] if results else None

    def get_asset_details(self, asset_ids: list) -> List[Dict[str, Any]]:
        """Get asset details for embedding in alert response."""
        if not asset_ids:
            return []
        query = f"""
            SELECT id, hostname[1] as hostname, category, subcategory,
                   ip[1]::text as ip, model[1] as model,
                   os_normalized as os, last_time as last_seen,
                   {_ASSET_LABEL_AS_LABEL}
            FROM assets
            WHERE id = ANY(%(asset_ids)s::uuid[])
        """
        results = self.db.execute_query(query, {'asset_ids': asset_ids})
        return results if results else []

    def count_active_alerts_for_asset(self, asset_id: str) -> int:
        """Count active alerts whose asset_ids array contains this asset.

        'Active' means status IN ('active', 'acknowledged') — the same open-alert
        definition used by get_active_alert_for_rule and the rule-delete resolve
        path; 'resolved' alerts are excluded. Powers the per-asset alert_count
        badge on the asset-detail header (#891).
        """
        query = """
            SELECT COUNT(*) as cnt
            FROM alerts
            WHERE status IN ('active', 'acknowledged')
              AND %(asset_id)s::uuid = ANY(asset_ids)
        """
        results = self.db.execute_query(query, {'asset_id': asset_id})
        return results[0]['cnt'] if results else 0

    def execute_filter_rule(
        self,
        target_filters: List[Dict[str, Any]],
        compliance_criteria: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Execute a filter-based user rule. Returns matching asset rows."""
        if self._compiler is None:
            raise RuntimeError(
                "AlertsRepository requires an AssetQueryCompiler to evaluate filter-based rules"
            )

        # Merge target filters + compliance criteria into one list with AND logic.
        # compliance_criteria.logic ('all'/'any') maps onto nested clauses; for v1
        # we AND the target filters with the criteria block (matching tag_rules).
        combined = [_to_compiler_filter(c) for c in target_filters]
        if compliance_criteria:
            combined.extend(
                _to_compiler_filter(c) for c in compliance_criteria.get('conditions', [])
            )

        sql, params = self._compiler.build_asset_query(
            combined,
            select_columns=['id', 'hostname[1] AS hostname', 'category',
                            'subcategory', 'ip[1]::text AS ip',
                            'os_normalized AS os', 'last_time AS last_seen',
                            'data_source', _ASSET_LABEL_SQL],
        )
        rows = self.db.execute_query(sql, params)
        return rows if rows else []

    def count_target_universe(
        self,
        target_filters: List[Dict[str, Any]],
    ) -> int:
        """Count assets matching target_filters only (percent-threshold denominator)."""
        if self._compiler is None:
            raise RuntimeError(
                "AlertsRepository requires an AssetQueryCompiler to count the target universe"
            )
        translated = [_to_compiler_filter(c) for c in target_filters]
        sql, params = self._compiler.build_count_query(translated)
        rows = self.db.execute_query(sql, params)
        if not rows:
            return 0
        return int(rows[0].get('count', 0))

    # DML/DDL keywords that must never appear as a leading statement
    _FORBIDDEN_STMT_RE = re.compile(
        r'^\s*(?:INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|COPY|EXEC)\b',
        re.IGNORECASE,
    )
    # Positive check: must start with SELECT or WITH
    _SELECT_STMT_RE = re.compile(r'^\s*(?:SELECT|WITH)\b', re.IGNORECASE)

    def execute_rule_query(self, sql: str) -> List[Dict[str, Any]]:
        """Execute an alert rule's SQL query and return matching rows.

        Only SELECT (and WITH ... SELECT) statements are allowed.
        Raises ValueError for DML/DDL or stacked queries.
        """
        stripped = sql.strip()

        # Reject stacked queries (semicolon separating statements)
        if ';' in stripped.rstrip(';'):
            raise ValueError("Alert rule query must not contain a semicolon (stacked queries)")

        # Reject anything that isn't SELECT or WITH...SELECT
        if self._FORBIDDEN_STMT_RE.match(stripped):
            raise ValueError("Alert rule query must be a SELECT statement")

        if not self._SELECT_STMT_RE.match(stripped):
            raise ValueError("Alert rule query must be a SELECT statement")

        results = self.db.execute_query(sql)
        return results if results else []
