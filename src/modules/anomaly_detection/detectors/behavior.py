# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavior detectors: B1a per-asset change burst, B1b persistent thrash,
B2 presence flapping, B3 identity swap.

B1a/B1b exclude A4's per-run systemic attribute set (the fleet-wide
administrative-change shape) so a bulk rename can't masquerade as per-asset
instability — the same noise-killer A4 established. B3 keys off durable
stable-binding memory, so it works even when UnionFind splits a swapped device
into a new asset row."""
from datetime import timedelta

from src.modules.anomaly_detection.detectors.volume import systemic_attrs
from src.modules.anomaly_detection.domain import AnomalyFinding


def _fleet_size(ctx):
    """Committed fleet size for the systemic-share denominator (not present in the
    in-stage gen-stats at s20 — fall back to the repo, like A4)."""
    return int(ctx.current_stats.get('total_assets') or 0) or ctx.repo.get_total_assets()


class AssetChangeBurstDetector:
    """B1a: one or more assets each changed an unusually large number of DISTINCT
    non-systemic attributes this run (re-image, identity instability, device
    replacement). One aggregate finding per run. Kind: event."""
    key = 'asset_change_burst'

    def evaluate(self, ctx):
        min_attrs = ctx.params.get('min_attrs', 8)
        bursts = ctx.repo.get_asset_change_bursts(ctx.generation_run_id, min_attrs) or []
        if not bursts:
            return []

        breakdown = ctx.repo.get_attribute_breakdown(ctx.generation_run_id) or []
        counts = {row.get('attr'): row.get('n') or 0 for row in breakdown if row.get('attr')}
        systemic = systemic_attrs(counts, _fleet_size(ctx), ctx.params.get('systemic_pct', 20))

        survivors = []
        for b in bursts:
            attrs = sorted(a for a in (b.get('attrs') or []) if a and a not in systemic)
            if len(attrs) >= min_attrs:
                survivors.append({'asset_id': b.get('asset_id'), 'attrs': attrs, 'n': len(attrs)})
        if not survivors:
            return []

        survivors.sort(key=lambda s: s['n'], reverse=True)
        asset_ids = [s['asset_id'] for s in survivors if s['asset_id']][:25]
        narrative = (f"{len(survivors)} asset(s) each changed >= {min_attrs} distinct "
                     f"non-systemic attributes this run — possible re-image, identity "
                     f"instability, or device replacement.")
        ev = {'detector': 'asset_change_burst', 'observed': len(survivors),
              'min_attrs': min_attrs, 'unit': 'assets',
              'assets': [{'asset_id': s['asset_id'], 'attr_count': s['n'],
                          'attributes': s['attrs'][:20]} for s in survivors[:25]],
              'sample_asset_ids': asset_ids, 'narrative': narrative}
        return [AnomalyFinding(
            detector_key='asset_change_burst', dimension_key='burst', kind='event',
            severity=ctx.params.get('severity', 'medium'), alert_type='data_quality',
            title=f"Per-asset change burst: {len(survivors)} asset(s) >= {min_attrs} attributes",
            description=narrative, asset_ids=asset_ids, evidence=ev)]


class AssetThrashDetector:
    """B1b: assets whose attributes oscillate across many recent runs — almost
    always two sources fighting over a value. Membership updates each run and
    auto-resolves when empty. Kind: condition."""
    key = 'asset_thrash'

    def evaluate(self, ctx):
        window_runs = ctx.params.get('window_runs', 10)
        min_runs = ctx.params.get('min_runs', 8)
        activity = ctx.repo.get_asset_run_activity(window_runs) or []
        if not activity:
            return []

        systemic_pct = ctx.params.get('systemic_pct', 20)
        fleet = _fleet_size(ctx)
        # Per-run systemic set: a run whose only churn on an asset was that run's
        # systemic attributes doesn't count toward thrash.
        per_run_counts = {}   # run_id -> {attr: n}
        for r in (ctx.repo.get_run_attr_totals(window_runs) or []):
            per_run_counts.setdefault(r.get('run_id'), {})[r.get('attr')] = r.get('n') or 0
        run_systemic = {rid: systemic_attrs(counts, fleet, systemic_pct)
                        for rid, counts in per_run_counts.items()}

        per_asset = {}   # asset_id -> {'runs': int, 'attrs': {attr: count}}
        for row in activity:
            aid, rid = row.get('asset_id'), row.get('run_id')
            non_systemic = set(row.get('attrs') or []) - run_systemic.get(rid, set())
            if not non_systemic:
                continue
            entry = per_asset.setdefault(aid, {'runs': 0, 'attrs': {}})
            entry['runs'] += 1
            for a in non_systemic:
                entry['attrs'][a] = entry['attrs'].get(a, 0) + 1

        members = [(aid, e) for aid, e in per_asset.items()
                   if aid and e['runs'] >= min_runs]
        if not members:
            return []          # empty membership -> service resolves the condition

        members.sort(key=lambda kv: kv[1]['runs'], reverse=True)
        asset_ids = [aid for aid, _ in members][:25]
        detail = []
        for aid, e in members[:25]:
            top = sorted(e['attrs'].items(), key=lambda kv: kv[1], reverse=True)[:5]
            detail.append({'asset_id': aid, 'runs_active': e['runs'],
                           'top_attributes': [a for a, _ in top]})
        narrative = (f"{len(members)} asset(s) changed non-systemic attributes in >= {min_runs} "
                     f"of the last {window_runs} runs — persistent thrash (two sources fighting).")
        ev = {'detector': 'asset_thrash', 'observed': len(members),
              'window_runs': window_runs, 'min_runs': min_runs, 'members': detail,
              'sample_asset_ids': asset_ids, 'narrative': narrative}
        return [AnomalyFinding(
            detector_key='asset_thrash', dimension_key='membership', kind='condition',
            severity=ctx.params.get('severity', 'medium'), alert_type='data_quality',
            title=f"Persistent asset thrash: {len(members)} asset(s) oscillating",
            description=narrative, asset_ids=asset_ids, evidence=ev)]


class PresenceFlapDetector:
    """B2: assets flapping present<->missing across recent history snapshots.
    Membership condition, seeded disabled — post-grace weekend flap is expected;
    enable deliberately. Kind: condition."""
    key = 'presence_flap'

    def evaluate(self, ctx):
        window_days = ctx.params.get('window_days', 30)
        min_flaps = ctx.params.get('min_flaps', 4)
        flaps = ctx.repo.get_presence_flaps(window_days, min_flaps) or []
        if not flaps:
            return []

        flaps = sorted(flaps, key=lambda r: r.get('flips') or 0, reverse=True)
        asset_ids = [r.get('asset_id') for r in flaps if r.get('asset_id')][:25]
        detail = [{'asset_id': r.get('asset_id'), 'flips': int(r.get('flips') or 0)}
                  for r in flaps[:25]]
        narrative = (f"{len(flaps)} asset(s) flapped present<->missing >= {min_flaps} times in "
                     f"the last {window_days} days.")
        ev = {'detector': 'presence_flap', 'observed': len(flaps),
              'window_days': window_days, 'min_flaps': min_flaps, 'members': detail,
              'sample_asset_ids': asset_ids, 'narrative': narrative}
        return [AnomalyFinding(
            detector_key='presence_flap', dimension_key='membership', kind='condition',
            severity=ctx.params.get('severity', 'low'), alert_type='data_quality',
            title=f"Presence flapping: {len(flaps)} asset(s)",
            description=narrative, asset_ids=asset_ids, evidence=ev)]


class IdentitySwapDetector:
    """B3: a stable binding's value was replaced — a stable hostname now reports a
    different serial (hardware swap / name impersonation), or a stable IP a
    different MAC (spoofing / ARP takeover / re-cabling). The stability gate is
    the DHCP-noise killer: a pool address never accumulates a stable binding, so
    no static-segment config is needed. Kind: event, per binding."""
    key = 'identity_swap'

    # (profile kind, severity, human noun for key, human noun for value)
    _KINDS = (
        ('hostname_sn', 'high', 'Hostname', 'serial'),
        ('ip_mac', 'medium', 'IP', 'MAC'),
    )

    def evaluate(self, ctx):
        stability_days = ctx.params.get('stability_days', 14)
        min_seen = ctx.params.get('min_seen', 3)
        current = ctx.repo.get_current_bindings() or {}
        since = ctx.run_started_at - timedelta(days=stability_days)
        findings = []
        for kind, severity, key_noun, val_noun in self._KINDS:
            pairs = current.get(kind) or []
            stable = ctx.profiles.get_stable_bindings(kind, stability_days, min_seen)
            for key, value, asset_id in pairs:
                entry = stable.get(key)
                if entry is None or entry['value'] == value:
                    continue
                old = entry['value']
                # Cross-day dedup: within stability_days the OLD value still wins
                # get_stable_bindings, so the same flip would re-fire every run.
                # A dateless prefix (the service still appends :{date}) fired in
                # the window suppresses it. See plan Task 5 for the known tail
                # (a very long-stable old value can re-surface after the window).
                dim = f'{kind}:{key}:{old}>{value}'
                # Anchor with the trailing ':' the service inserts before the run
                # date, so the LIKE prefix can't match a DIFFERENT flip whose new
                # value string-extends this one (old>'SN1' must not dedup against a
                # prior old>'SN12').
                prefix = f'anomaly:{self.key}:{dim}:'
                if ctx.repo.get_recent_fingerprints_like(ctx.rule['id'], prefix, since):
                    continue
                findings.append(self._finding(
                    kind, severity, key_noun, val_noun, key, old, value, entry, asset_id))
            # Upsert AFTER detection so the new value only now starts accumulating
            # its own stability (and doesn't mask the flip we just reported).
            ctx.profiles.upsert_bindings(kind, pairs, ctx.run_started_at)
        return findings

    def _finding(self, kind, severity, key_noun, val_noun, key, old, new, entry, asset_id):
        age = entry['age_days']
        title = ("Stable asset now reports different hardware" if kind == 'hostname_sn'
                 else "Stable address now reports a different device")
        narrative = (f"{key_noun} {key} now reports {val_noun} {new} (was {old}). "
                     f"The prior binding was stable for ~{age} days "
                     f"(seen {entry['seen_count']} times) before this replacement.")
        return AnomalyFinding(
            detector_key=self.key, dimension_key=f'{kind}:{key}:{old}>{new}', kind='event',
            severity=severity, alert_type='security',
            title=title, description=narrative,
            asset_ids=[asset_id] if asset_id else [],
            evidence={'detector': self.key, 'binding_kind': kind, 'key': key,
                      'old_value': old, 'new_value': new, 'stability_age_days': age,
                      'seen_count': entry['seen_count'], 'narrative': narrative})
