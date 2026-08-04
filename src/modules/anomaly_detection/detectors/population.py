# SPDX-License-Identifier: AGPL-3.0-or-later
"""Population detectors: C1 out-of-profile segment device, C2 first-seen value."""
import ipaddress

from src.modules.anomaly_detection.domain import AnomalyFinding

# RFC1918 blocks only. NOT `ip.is_private`, which in Python 3.13 also matches
# TEST-NET/benchmarking/documentation ranges (e.g. 203.0.113.0/24) — those are
# not real internal segments (spec §14.5 says "first valid RFC1918 address").
_RFC1918 = (
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
)


def derive_segment(ips):
    """First RFC1918 IPv4 in the list -> its /24 CIDR string, else None.

    Some deployments have empty ipam_networks and NULL ip_primary, so C1 keys off the
    first valid private address in the assets.ip[] array (spec §14.5)."""
    for raw in ips or []:
        try:
            ip = ipaddress.ip_address(str(raw).strip())
        except ValueError:
            continue
        if isinstance(ip, ipaddress.IPv4Address) and any(ip in net for net in _RFC1918):
            return str(ipaddress.ip_network(f'{ip}/24', strict=False))
    return None


class SegmentOutlierDetector:
    """C1: a created asset whose category doesn't fit its /24's homogeneous
    profile (e.g. a workstation appearing in a server VLAN). Kind: event."""
    key = 'segment_outlier'

    def evaluate(self, ctx):
        created = ctx.repo.get_created_assets(ctx.generation_run_id) or []
        asset_segments = []
        segments = set()
        for a in created:
            seg = derive_segment(a.get('ip'))
            if seg:
                asset_segments.append((a, seg))
                segments.add(seg)
        if not segments:
            return []

        rows = ctx.repo.get_segment_profiles(sorted(segments), ctx.run_started_at) or []
        profile = {}   # segment -> {category: n}
        for r in rows:
            profile.setdefault(r['segment'], {})[r['category']] = r['n']

        min_pop = ctx.settings.segment_min_population
        homogeneity = ctx.settings.segment_homogeneity_pct / 100.0
        findings = []
        for a, seg in asset_segments:
            dist = profile.get(seg, {})
            population = sum(dist.values())
            if population < min_pop:
                continue
            dominant_cat, dominant_n = max(dist.items(), key=lambda kv: kv[1])
            if dominant_n / population < homogeneity:
                continue
            newcomer = a.get('category')
            if newcomer is None or newcomer == dominant_cat:
                continue
            findings.append(self._finding(ctx, a, seg, dist, population, dominant_cat, newcomer))
        return findings

    def _finding(self, ctx, a, seg, dist, population, dominant_cat, newcomer):
        top3 = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)[:3]
        share = round(100.0 * dist[dominant_cat] / population, 1)
        narrative = (f"New {newcomer} in segment {seg}, which is {share}% {dominant_cat} "
                     f"({population} assets) — out of profile.")
        return AnomalyFinding(
            detector_key='segment_outlier', dimension_key=a['asset_id'], kind='event',
            severity=ctx.params.get('severity', 'medium'), alert_type='security',
            title=f"Out-of-profile {newcomer} in {dominant_cat} segment {seg}",
            description=narrative, asset_ids=[a['asset_id']],
            assets_by_category={newcomer: 1},
            evidence={'detector': 'segment_outlier', 'segment': seg, 'population': population,
                      'dominant_category': dominant_cat, 'dominant_share_pct': share,
                      'newcomer_category': newcomer,
                      'distribution': [{'category': c, 'count': n} for c, n in top3],
                      'newcomer': {'asset_id': a['asset_id'], 'ip': a.get('ip'),
                                   'manufacturer': a.get('manufacturer'),
                                   'os': a.get('os_normalized')},
                      'narrative': narrative})


class FirstSeenValueDetector:
    """C2: a manufacturer or OS never seen before in this fleet, on a created
    asset. One finding per new value (not per asset). Kind: event."""
    key = 'first_seen_value'

    _KINDS = (
        ('fleet_manufacturer', 'manufacturer', lambda a: a.get('manufacturer') or []),
        ('fleet_os', 'OS', lambda a: [a['os_normalized']] if a.get('os_normalized') else []),
    )

    def evaluate(self, ctx):
        # Seed the durable memory on first run (empty table) so day-one values
        # aren't all reported as first-seen.
        ctx.profiles.bootstrap_if_empty()
        created = ctx.repo.get_created_assets(ctx.generation_run_id) or []

        findings = []
        for kind, label, extract in self._KINDS:
            known = ctx.profiles.known_values(kind)
            new_values = {}     # normalized value -> [asset_id]
            all_values = []
            for a in created:
                for raw in extract(a):
                    norm = str(raw).strip().lower()
                    if not norm:
                        continue
                    all_values.append(norm)
                    if norm not in known:
                        new_values.setdefault(norm, []).append(a['asset_id'])
            for value, asset_ids in new_values.items():
                findings.append(self._finding(ctx, kind, label, value, asset_ids))
            # Always upsert (even when quiet) so this run's values are known next run.
            ctx.profiles.upsert_values(kind, all_values, ctx.run_started_at)
        return findings

    def _finding(self, ctx, kind, label, value, asset_ids):
        narrative = (f"First-seen {label} '{value}' appeared on {len(asset_ids)} new "
                     f"asset(s) — never recorded in this fleet before.")
        return AnomalyFinding(
            detector_key='first_seen_value', dimension_key=f'{kind}:{value}', kind='event',
            severity=ctx.params.get('severity', 'low'), alert_type='security',
            title=f"First-seen {label}: {value}", description=narrative,
            asset_ids=asset_ids[:25],
            evidence={'detector': 'first_seen_value', 'kind': kind, 'value': value,
                      'label': label, 'new_asset_count': len(asset_ids),
                      'sample_asset_ids': asset_ids[:25], 'narrative': narrative})
