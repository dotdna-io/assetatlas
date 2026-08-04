# SPDX-License-Identifier: AGPL-3.0-or-later
"""Boot-time sync of monitoring/alerts/*.yml → alert_rules rows.

Each Prometheus rule becomes exactly one row in `alert_rules` with source='prometheus'.
Stale rows (source='prometheus' slugs not present in YAML anymore) are removed so the
DB view stays authoritative.

Non-fatal on parse error: we log a warning and skip the file rather than blocking API
startup. This module must never raise — a bad rule file must not take the API down.
"""
from __future__ import annotations

import glob
import logging
import os
from typing import Any, Dict, List

import yaml

from src.modules.alerts.prometheus_severity import map_severity

logger = logging.getLogger(__name__)


def _parse_rules_from_file(path: str) -> List[Dict[str, Any]]:
    """Parse a Prometheus rule-group YAML file. Returns a list of normalized rule dicts.

    Returns [] on parse error (logged).
    """
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return []

    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        logger.warning("Skipping %s: not a valid Prometheus rule-group YAML (groups must be a list)", path)
        return []

    out: List[Dict[str, Any]] = []
    for group in data["groups"]:
        if not isinstance(group, dict):
            continue
        for rule in group.get("rules", []) or []:
            if not isinstance(rule, dict):
                continue
            alertname = rule.get("alert")
            if not alertname:
                # recording rule — not an alert, skip
                continue
            expr = rule.get("expr")
            labels = rule.get("labels") or {}
            annotations = rule.get("annotations") or {}
            out.append({
                "slug": alertname,
                "name": annotations.get("summary") or alertname,
                "description": annotations.get("description") or annotations.get("summary") or alertname,
                "query": expr or "",
                "severity": map_severity(labels.get("severity")),
                "type": "operational",
                "source": "prometheus",
            })
    return out


def sync_prometheus_rules(yaml_dir: str, adapter) -> Dict[str, int]:
    """Upsert alert_rules rows from Prometheus YAML. Never raises.

    Returns: {processed, inserted, updated, removed}
    """
    counts = {"processed": 0, "inserted": 0, "updated": 0, "removed": 0}

    if not os.path.isdir(yaml_dir):
        logger.warning("Monitoring rules directory does not exist: %s — skipping sync", yaml_dir)
        return counts

    files = sorted(glob.glob(os.path.join(yaml_dir, "*.yml"))
                   + glob.glob(os.path.join(yaml_dir, "*.yaml")))
    rules: List[Dict[str, Any]] = []
    for f in files:
        rules.extend(_parse_rules_from_file(f))

    yaml_slugs = {r["slug"] for r in rules}

    # Fetch existing source='prometheus' slugs for stale detection.
    # NOTE: execute_query is called positionally (query_str, params_dict) to
    # match the existing repository pattern.
    try:
        existing_rows = adapter.execute_query(
            "SELECT slug FROM alert_rules WHERE source = %(src)s",
            {"src": "prometheus"},
        ) or []
    except Exception as e:
        logger.warning("Could not read existing prometheus alert_rules: %s — continuing with inserts only", e)
        existing_rows = []

    existing_slugs = {row["slug"] for row in existing_rows}

    # Upsert each rule.
    #
    # subject_type is pinned to 'system' rather than left to the column default
    # of 'asset': these are infrastructure alerts that carry no assets at all
    # (the webhook inserts them with asset_count 0 and empty asset_ids), and
    # migration 133 already uses 'system' for exactly this class of rule. Without
    # it the rows migration 139 hands over would silently downgrade to 'asset'.
    for r in rules:
        counts["processed"] += 1
        try:
            adapter.execute_query(
                """
                INSERT INTO alert_rules (name, slug, type, severity, description, query, source, enabled,
                                         subject_type)
                VALUES (%(name)s, %(slug)s, %(type)s, %(severity)s, %(description)s, %(query)s, 'prometheus', true,
                        'system')
                ON CONFLICT (slug) DO UPDATE SET
                    name = EXCLUDED.name,
                    type = EXCLUDED.type,
                    severity = EXCLUDED.severity,
                    description = EXCLUDED.description,
                    query = EXCLUDED.query,
                    source = 'prometheus',
                    subject_type = 'system',
                    updated_at = now()
                WHERE alert_rules.source = 'prometheus'
                """,
                r,
                fetch=False,
            )
            if r["slug"] in existing_slugs:
                counts["updated"] += 1
            else:
                counts["inserted"] += 1
        except Exception as e:
            logger.warning("Failed to upsert prometheus rule %s: %s", r["slug"], e)

    # Delete stale rows (source='prometheus' slugs not in YAML anymore)
    stale = existing_slugs - yaml_slugs
    for slug in stale:
        try:
            adapter.execute_query(
                "DELETE FROM alert_rules WHERE source = 'prometheus' AND slug = %(slug)s",
                {"slug": slug},
                fetch=False,
            )
            counts["removed"] += 1
        except Exception as e:
            logger.warning("Failed to delete stale prometheus rule %s: %s", slug, e)

    return counts
