# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock

from src.modules.anomaly_detection.settings import AnomalySettings, load_settings


def _db(rows=None):
    db = MagicMock()
    db.execute_query.return_value = rows if rows is not None else []
    return db


def test_load_settings_parses_json_and_ints():
    rows = [
        {'key': 'security_source_categories', 'value': '["endpoint security"]', 'value_type': 'json'},
        {'key': 'security_source_names', 'value': '["Custom EDR"]', 'value_type': 'json'},
        {'key': 'segment_min_population', 'value': '15', 'value_type': 'integer'},
        {'key': 'segment_homogeneity_pct', 'value': '85', 'value_type': 'integer'},
        {'key': 'aggregation_threshold', 'value': '12', 'value_type': 'integer'},
        {'key': 'segment_coverage_pct', 'value': '75', 'value_type': 'integer'},
        {'key': 'eligible_categories_for_coverage', 'value': '["Server", "Kiosk"]', 'value_type': 'json'},
    ]
    db = _db(rows)
    s = load_settings(db)
    assert isinstance(s, AnomalySettings)
    assert s.security_source_categories == ['endpoint security']
    assert s.security_source_names == ['Custom EDR']
    assert s.segment_min_population == 15
    assert s.segment_homogeneity_pct == 85
    assert s.aggregation_threshold == 12
    assert s.segment_coverage_pct == 75
    assert s.eligible_categories_for_coverage == ['Server', 'Kiosk']
    assert "category = 'anomaly_detection'" in db.execute_query.call_args.args[0]


def test_load_settings_uses_code_defaults_when_missing():
    s = load_settings(_db([]))
    assert s.security_source_categories == ['endpoint security']
    assert s.security_source_names == []
    assert s.segment_min_population == 10
    assert s.segment_homogeneity_pct == 90
    assert s.aggregation_threshold == 10
    assert s.segment_coverage_pct == 80
    assert s.eligible_categories_for_coverage == ['Server', 'Workstation', 'Laptop']


def test_load_settings_normalizes_categories_to_lower():
    rows = [{'key': 'security_source_categories',
             'value': '["Endpoint Security", "EDR"]', 'value_type': 'json'}]
    s = load_settings(_db(rows))
    assert s.security_source_categories == ['endpoint security', 'edr']


def test_load_settings_keeps_eligible_categories_verbatim():
    # eligible_categories_for_coverage is kept verbatim at load (not lowercased)
    # so it can be displayed as-is. assets.category is stored lowercase, so the D2
    # detector lowercases BOTH sides at comparison time (Python membership +
    # LOWER(a.category) in SQL) — a capitalized setting still matches real data.
    rows = [{'key': 'eligible_categories_for_coverage',
             'value': '["Server", "Workstation"]', 'value_type': 'json'}]
    s = load_settings(_db(rows))
    assert s.eligible_categories_for_coverage == ['Server', 'Workstation']


def test_load_settings_bad_value_falls_back_to_default():
    rows = [{'key': 'segment_min_population', 'value': 'not-an-int', 'value_type': 'integer'}]
    s = load_settings(_db(rows))
    assert s.segment_min_population == 10
