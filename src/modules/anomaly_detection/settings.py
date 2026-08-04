# SPDX-License-Identifier: AGPL-3.0-or-later
"""Global anomaly-detection config (system_settings category 'anomaly_detection')."""
import json
from dataclasses import dataclass


@dataclass
class AnomalySettings:
    security_source_categories: list
    security_source_names: list
    segment_min_population: int
    segment_homogeneity_pct: int
    aggregation_threshold: int
    segment_coverage_pct: int
    eligible_categories_for_coverage: list


# Code defaults mirror migrations 117/118's seeds so the engine works even before
# the settings rows exist (fresh DB, failed seed, restored dump).
_DEFAULTS = {
    'security_source_categories': ['endpoint security'],
    'security_source_names': [],
    'segment_min_population': 10,
    'segment_homogeneity_pct': 90,
    'aggregation_threshold': 10,
    'segment_coverage_pct': 80,
    'eligible_categories_for_coverage': ['Server', 'Workstation', 'Laptop'],
}

_SETTINGS_SQL = (
    "SELECT key, value, value_type FROM system_settings "
    "WHERE category = 'anomaly_detection'"
)


def load_settings(db_adapter) -> AnomalySettings:
    rows = db_adapter.execute_query(_SETTINGS_SQL) or []
    raw = {}
    for r in rows:
        key, value, vtype = r.get('key'), r.get('value'), r.get('value_type')
        if key not in _DEFAULTS:
            continue
        try:
            if vtype == 'json':
                raw[key] = json.loads(value)
            elif vtype == 'integer':
                raw[key] = int(value)
            else:
                raw[key] = value
        except (ValueError, TypeError, json.JSONDecodeError):
            pass  # malformed value -> fall back to the code default below

    def g(k):
        return raw.get(k, _DEFAULTS[k])

    # Categories are compared case-insensitively against data_source_category
    # (lowercase phrases on the live taxonomy); names stay verbatim (canonical labels).
    categories = [str(c).strip().lower() for c in g('security_source_categories')]
    names = [str(n) for n in g('security_source_names')]
    # eligible_categories_for_coverage is kept verbatim for display (canonical
    # labels like 'Server'/'Workstation'); assets.category is actually stored
    # lowercase, so the D2 detector lowercases both sides at comparison time
    # (Python set + SQL LOWER(a.category)) rather than relying on this casing.
    eligible_categories = [str(c) for c in g('eligible_categories_for_coverage')]
    return AnomalySettings(
        security_source_categories=categories,
        security_source_names=names,
        segment_min_population=int(g('segment_min_population')),
        segment_homogeneity_pct=int(g('segment_homogeneity_pct')),
        aggregation_threshold=int(g('aggregation_threshold')),
        segment_coverage_pct=int(g('segment_coverage_pct')),
        eligible_categories_for_coverage=eligible_categories,
    )
