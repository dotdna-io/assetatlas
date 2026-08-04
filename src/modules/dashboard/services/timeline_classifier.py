# SPDX-License-Identifier: AGPL-3.0-or-later
"""Net-distinct-per-day classifier for the asset add/remove timeline.

Each asset is reduced to one row per day it changed state, ordered by day,
with a baseline row (day == window_start - 1) seeding its pre-window
presence. We walk each asset's days; a day is counted only when the asset's
end-of-day presence differs from its start-of-day presence (so same-day
round-trips cancel and multiple runs/day do not inflate the bars):

  absent -> present, via asset_created  => added (genuinely new)
  absent -> present, via asset_readded  => returned (came back)
  present -> absent                     => removed (genuinely departed)
"""
from collections import defaultdict
from datetime import date
from typing import Dict, List


def classify_net_timeline(rows: List[dict], window_start: date) -> Dict[date, dict]:
    by_asset = defaultdict(list)
    for r in rows:
        by_asset[r['asset_id']].append(r)

    buckets: Dict[date, dict] = {}

    def bucket(day):
        return buckets.setdefault(day, {'added': 0, 'returned': 0, 'removed': 0})

    for evs in by_asset.values():
        evs = sorted(evs, key=lambda r: r['day'])
        prev_present = False
        for r in evs:
            start_present = prev_present
            end_present = bool(r['end_enter'])
            day = r['day']
            if day >= window_start:
                if not start_present and end_present:
                    if r['last_evt'] == 'asset_created':
                        bucket(day)['added'] += 1
                    elif r['last_evt'] == 'asset_readded':
                        bucket(day)['returned'] += 1
                elif start_present and not end_present:
                    bucket(day)['removed'] += 1
            prev_present = end_present

    return buckets
