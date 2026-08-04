# SPDX-License-Identifier: AGPL-3.0-or-later
"""Baseline selection: which historical runs a detector compares against."""
from dataclasses import dataclass


@dataclass
class BaselineResult:
    samples: list
    mode: str                 # 'same_weekday' | 'window' | 'all_runs_fallback'
    tolerance_factor: float   # 1.0 seasonal, 1.5 in window/fallback modes
    learning: bool            # True => detector must stay silent


def build_baseline(history, run_weekday, params, value_key='value'):
    window = params.get('window_runs', 30)
    min_samples = params.get('min_samples', 8)
    min_seasonal = params.get('min_seasonal_samples', 4)

    rows = [r for r in history if r.get(value_key) is not None]
    scheduled = [r for r in rows if r.get('triggered_by') == 'schedule']

    # Manual re-runs reproduce batch volumes but collapse churn metrics —
    # prefer scheduled runs; fall back to everything when a deployment
    # (like the dev DB) never runs the scheduler.
    if len(scheduled) >= min_samples:
        pool, fallback = scheduled[:window], False
    else:
        pool, fallback = rows[:window], True

    # Learning is about total history depth (the window pool), not the seasonal
    # subset — with enough runs we trust a same-weekday comparison even though
    # its subset is ~1/7 the pool. Basing it on the subset would keep A1/A3/…
    # in learning mode for 8 weeks on a fresh deployment.
    learning = len(pool) < min_samples

    seasonal = [r for r in pool if r['started_at'].weekday() == run_weekday]
    if not fallback and len(seasonal) >= min_seasonal:
        chosen, mode, tol = seasonal, 'same_weekday', 1.0
    elif fallback:
        chosen, mode, tol = pool, 'all_runs_fallback', 1.5
    else:
        chosen, mode, tol = pool, 'window', 1.5

    samples = [float(r[value_key]) for r in chosen]
    return BaselineResult(samples, mode, tol, learning=learning)
