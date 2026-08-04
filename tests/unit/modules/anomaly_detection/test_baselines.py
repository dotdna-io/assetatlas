# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime, timedelta

from src.modules.anomaly_detection.baselines import build_baseline

P = {'window_runs': 30, 'min_samples': 8, 'min_seasonal_samples': 4}


def _rows(n, weekday_anchor, triggered_by='schedule', value=100):
    base = datetime(2026, 6, 1)  # a Monday
    rows = []
    for i in range(n):
        d = base + timedelta(days=i)
        rows.append({'started_at': d, 'triggered_by': triggered_by, 'value': value})
    return rows


def test_same_weekday_selected_when_enough():
    rows = _rows(30, 0)  # daily => >=4 same-weekday samples
    b = build_baseline(rows, run_weekday=1, params=P)
    assert b.mode == 'same_weekday' and b.tolerance_factor == 1.0 and not b.learning
    assert len(b.samples) >= 4


def test_window_mode_when_sparse_weekdays():
    rows = _rows(10, 0)  # only 1-2 per weekday
    b = build_baseline(rows, run_weekday=1, params=P)
    assert b.mode == 'window' and b.tolerance_factor == 1.5


def test_all_runs_fallback_when_no_scheduled():
    rows = _rows(20, 0, triggered_by='manual')
    b = build_baseline(rows, run_weekday=1, params=P)
    assert b.mode == 'all_runs_fallback' and b.tolerance_factor == 1.5 and not b.learning


def test_learning_when_too_little_history():
    b = build_baseline(_rows(3, 0), run_weekday=1, params=P)
    assert b.learning


def test_none_values_dropped():
    rows = _rows(12, 0)
    rows[0]['value'] = None
    b = build_baseline(rows, run_weekday=1, params=P)
    assert None not in b.samples
