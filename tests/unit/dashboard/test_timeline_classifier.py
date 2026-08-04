# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the net-distinct-per-day asset-timeline classifier."""
from datetime import date

from src.modules.dashboard.services.timeline_classifier import classify_net_timeline

WS = date(2026, 6, 10)          # window start
BASE = date(2026, 6, 9)         # baseline sentinel day (window_start - 1)
D1 = date(2026, 6, 10)
D2 = date(2026, 6, 11)


def _row(asset_id, day, end_enter, last_evt):
    return {'asset_id': asset_id, 'day': day, 'end_enter': end_enter, 'last_evt': last_evt}


def test_new_asset_counts_as_added():
    rows = [_row('a', D1, True, 'asset_created')]
    out = classify_net_timeline(rows, WS)
    assert out[D1] == {'added': 1, 'returned': 0, 'removed': 0}


def test_returned_asset_counts_as_returned_not_added():
    rows = [_row('a', D1, True, 'asset_readded')]
    out = classify_net_timeline(rows, WS)
    assert out[D1] == {'added': 0, 'returned': 1, 'removed': 0}


def test_departure_counts_as_removed():
    # present at window start (baseline enter), then leaves on D1
    rows = [_row('a', BASE, True, 'asset_created'), _row('a', D1, False, 'asset_removed')]
    out = classify_net_timeline(rows, WS)
    assert out[D1] == {'added': 0, 'returned': 0, 'removed': 1}


def test_same_day_round_trip_cancels():
    # absent at start, last event of the day is a departure -> ended where it started -> no-op
    rows = [_row('a', D1, False, 'asset_removed')]
    out = classify_net_timeline(rows, WS)
    assert out.get(D1, {'added': 0, 'returned': 0, 'removed': 0}) == {'added': 0, 'returned': 0, 'removed': 0}


def test_multi_event_same_day_dedupes_to_last_state():
    # two rows for same asset/day must never happen (SQL collapses), but a flap
    # across days: removed D1, readded D2 -> one removed, one returned
    rows = [
        _row('a', BASE, True, 'asset_created'),
        _row('a', D1, False, 'asset_removed'),
        _row('a', D2, True, 'asset_readded'),
    ]
    out = classify_net_timeline(rows, WS)
    assert out[D1] == {'added': 0, 'returned': 0, 'removed': 1}
    assert out[D2] == {'added': 0, 'returned': 1, 'removed': 0}


def test_baseline_day_never_emitted():
    rows = [_row('a', BASE, True, 'asset_created')]
    out = classify_net_timeline(rows, WS)
    assert BASE not in out


def test_already_present_readd_is_noop():
    # present at start AND ends present (readd while present) -> no transition
    rows = [_row('a', BASE, True, 'asset_created'), _row('a', D1, True, 'asset_readded')]
    out = classify_net_timeline(rows, WS)
    assert out.get(D1, {'added': 0, 'returned': 0, 'removed': 0}) == {'added': 0, 'returned': 0, 'removed': 0}
