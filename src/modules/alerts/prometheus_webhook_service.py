# SPDX-License-Identifier: AGPL-3.0-or-later
"""Alertmanager webhook handler — maps payloads onto `alerts` rows.

Lifecycle (spec Q3):
- firing + no matching active row → INSERT new active alert
- firing + matching active row    → UPDATE updated_at only (dedup)
- resolved                        → UPDATE active row → status='resolved', resolved_at=now()

The match key is (rule_id, fingerprint) where fingerprint is the stable hash
Alertmanager computes from the alert's labels.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from pydantic import ValidationError as PydanticValidationError

from src.api.middleware.error_handler import ValidationError
from src.modules.alerts.prometheus_schemas import AlertmanagerPayload
from src.modules.alerts.prometheus_severity import map_severity

logger = logging.getLogger(__name__)


class PrometheusWebhookService:
    """Handles Alertmanager v4 webhook payloads."""

    def __init__(self, adapter):
        self.adapter = adapter

    def handle_alertmanager_payload(self, body: Dict[str, Any]) -> Dict[str, int]:
        try:
            payload = AlertmanagerPayload.model_validate(body)
        except PydanticValidationError as e:
            raise ValidationError(f"Invalid Alertmanager payload: {e}") from e

        counts = {
            "processed": 0,
            "firing": 0,
            "resolved": 0,
            "unknown_alertname": 0,
        }

        for alert in payload.alerts:
            counts["processed"] += 1
            alertname = alert.labels.get("alertname")
            if not alertname:
                logger.warning("Alertmanager alert missing 'alertname' label; skipping")
                counts["unknown_alertname"] += 1
                continue

            rule_id = self._lookup_rule_id(alertname)
            if rule_id is None:
                logger.warning("No alert_rules row for alertname=%s — skipping", alertname)
                counts["unknown_alertname"] += 1
                continue

            if alert.status == "firing":
                self._handle_firing(rule_id, alert)
                counts["firing"] += 1
            else:  # "resolved"
                self._handle_resolved(rule_id, alert)
                counts["resolved"] += 1

        return counts

    # Internal helpers -------------------------------------------------------

    def _lookup_rule_id(self, alertname: str) -> str | None:
        rows = self.adapter.execute_query(
            """
                SELECT id FROM alert_rules
                WHERE slug = %(slug)s AND source = 'prometheus'
                LIMIT 1
            """,
            {"slug": alertname},
        ) or []
        if not rows:
            return None
        return rows[0]["id"]

    def _handle_firing(self, rule_id: str, alert) -> None:
        existing = self.adapter.execute_query(
            """
                SELECT id FROM alerts
                WHERE rule_id = %(rid)s AND fingerprint = %(fp)s AND status = 'active'
                LIMIT 1
            """,
            {"rid": rule_id, "fp": alert.fingerprint},
        ) or []

        if existing:
            self.adapter.execute_query(
                "UPDATE alerts SET updated_at = now() WHERE id = %(id)s",
                {"id": existing[0]["id"]},
                fetch=False,
            )
            return

        self.adapter.execute_query(
            """
                INSERT INTO alerts
                    (rule_id, type, severity, title, description, status,
                     asset_count, assets_by_category, data_sources, asset_ids,
                     source, fingerprint, labels)
                VALUES
                    (%(rule_id)s, 'operational', %(severity)s,
                     %(title)s, %(description)s, 'active',
                     0, %(empty_jsonb)s::jsonb, %(data_sources)s, %(asset_ids)s::uuid[],
                     'prometheus', %(fingerprint)s, %(labels)s::jsonb)
            """,
            {
                "rule_id": rule_id,
                "severity": map_severity(alert.labels.get("severity")),
                "title": alert.annotations.get("summary") or alert.labels.get("alertname", "Alert"),
                "description": alert.annotations.get("description")
                               or alert.annotations.get("summary")
                               or "",
                "empty_jsonb": "{}",
                "data_sources": [],
                "asset_ids": [],
                "fingerprint": alert.fingerprint,
                "labels": json.dumps(dict(alert.labels)),
            },
            fetch=False,
        )

    def _handle_resolved(self, rule_id: str, alert) -> None:
        # Idempotent: a second resolved just matches 0 rows.
        self.adapter.execute_query(
            """
                UPDATE alerts
                   SET status = 'resolved', resolved_at = now(), updated_at = now()
                 WHERE rule_id = %(rid)s AND fingerprint = %(fp)s AND status = 'active'
            """,
            {"rid": rule_id, "fp": alert.fingerprint},
            fetch=False,
        )
