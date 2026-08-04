# SPDX-License-Identifier: AGPL-3.0-or-later
"""Coverage detectors: D1 security-source coverage loss, D2 unprotected
newcomer in a secured segment."""
from src.modules.anomaly_detection.detectors.population import derive_segment
from src.modules.anomaly_detection.domain import ASSET_LABEL_TOKEN, AnomalyFinding, robust_z


def build_security_source_set(ctx):
    """data_source names treated as security tooling: those whose majority
    data_source_category is in settings.security_source_categories (compared
    case-insensitively — the live taxonomy is lowercase) plus the explicit
    names list. Shared by D1 (coverage loss) and D2 (unprotected newcomer)."""
    cat_map = ctx.repo.get_source_category_map() or {}
    cats = set(ctx.settings.security_source_categories)   # lowercased by load_settings
    sources = {name for name, cat in cat_map.items() if cat and cat.lower() in cats}
    sources |= set(ctx.settings.security_source_names)
    return sources


def _iso(v):
    return v.isoformat() if hasattr(v, 'isoformat') else v


class SecuritySourceLossDetector:
    """D1: N assets lost a security-class data source this run while still
    visible via other sources (agent uninstall / tampering sweep). Kind: event,
    one finding per security source. Baseline = generic per-run loss counts
    (deliberately broader than the security-filtered current value, so the
    threshold is conservative)."""
    key = 'security_source_loss'

    def evaluate(self, ctx):
        security = build_security_source_set(ctx)
        if not security:
            return []

        events = ctx.repo.get_datasource_loss_events(ctx.generation_run_id) or []
        by_source = {}   # source -> [events]
        for ev in events:
            for src in ((ev.get('ds_change') or {}).get('removed') or []):
                if src in security:          # case-sensitive: sources are canonical labels
                    by_source.setdefault(src, []).append(ev)
        if not by_source:
            return []

        history = ctx.repo.get_datasource_loss_history(
            ctx.params.get('window_days', 90), exclude_grid=ctx.generation_run_id) or []
        samples = [r['loss_count'] for r in history if r.get('loss_count') is not None]
        if len(samples) < ctx.params.get('min_samples', 8):
            return []            # learning: not enough loss history yet

        min_count = ctx.params.get('min_count', 5)
        z_medium = ctx.params.get('z_medium', 3)
        findings = []
        for source, evs in sorted(by_source.items()):
            count = len(evs)
            rz = robust_z(count, samples)
            if count < max(min_count, rz.median + z_medium * rz.mad):
                continue
            findings.append(self._finding(ctx, source, evs, rz))
        return findings

    def _finding(self, ctx, source, evs, rz):
        count = len(evs)
        asset_ids = [e['asset_id'] for e in evs if e.get('asset_id')][:25]
        categories = {}
        for e in evs:
            c = e.get('category') or 'unknown'
            categories[c] = categories.get(c, 0) + 1
        narrative = (f"{count} assets lost security source '{source}' this run while still "
                     f"present via other sources (baseline ~{int(rz.median)}/run) — "
                     f"possible agent uninstall or tampering sweep.")
        return AnomalyFinding(
            detector_key='security_source_loss', dimension_key=source, kind='event',
            severity=ctx.params.get('severity', 'high'), alert_type='security',
            title=f"Security coverage loss: {count} assets lost {source}",
            description=narrative, asset_ids=asset_ids, data_sources=[source],
            assets_by_category=categories,
            evidence={'detector': 'security_source_loss', 'source': source,
                      'observed': count, 'unit': 'assets', 'typical': int(rz.median),
                      'technical': {'median': rz.median, 'mad': rz.mad, 'window_runs': rz.n,
                                    'z': round(rz.z, 2)},
                      'categories': categories,
                      'sample_asset_ids': asset_ids, 'narrative': narrative})


class UnprotectedNewcomerDetector:
    """D2: a newly-seen eligible-category asset carries no security tooling while
    its network segment is otherwise security-covered (agent never installed /
    evasion). The settle window avoids "agent lags network scan" false positives;
    its upper bound stops re-firing on old assets. Kind: condition, per asset —
    auto-resolves when the asset gains a security source or ages out of the
    window."""
    key = 'unprotected_newcomer'

    def evaluate(self, ctx):
        security = build_security_source_set(ctx)
        if not security:
            return []          # can't judge coverage without a security-source set
        eligible = {c.lower() for c in ctx.settings.eligible_categories_for_coverage}
        settle_max = ctx.params.get('settle_max_days', 7)
        candidates = ctx.repo.get_settle_window_assets(
            ctx.run_started_at, ctx.params.get('settle_min_days', 2), settle_max) or []

        unprotected, segments = [], set()
        for a in candidates:
            if (a.get('category') or '').lower() not in eligible:
                continue
            if set(a.get('data_source') or []) & security:
                continue        # already protected
            seg = derive_segment(a.get('ip'))
            if not seg:
                continue
            unprotected.append((a, seg))
            segments.add(seg)
        if not segments:
            return []

        coverage = {r['segment']: r for r in (ctx.repo.get_segment_coverage(
            sorted(segments), sorted(eligible), sorted(security),
            ctx.run_started_at, settle_max) or [])}

        min_pop = ctx.settings.segment_min_population
        cov_pct = ctx.settings.segment_coverage_pct / 100.0
        findings = []
        for a, seg in unprotected:
            cov = coverage.get(seg)
            if not cov:
                continue
            eligible_n = int(cov.get('eligible_n') or 0)
            covered_n = int(cov.get('covered_n') or 0)
            if eligible_n <= 0 or eligible_n < min_pop:
                continue
            if covered_n / eligible_n < cov_pct:
                continue
            findings.append(self._finding(ctx, a, seg, eligible_n, covered_n))
        return self._collapse(ctx, findings)

    def _finding(self, ctx, a, seg, eligible_n, covered_n):
        share = round(100.0 * covered_n / eligible_n, 1)
        cat = a.get('category')
        narrative = (f"New {cat} {ASSET_LABEL_TOKEN} in segment {seg} carries no security "
                     f"tooling, but {covered_n}/{eligible_n} ({share}%) of eligible peers "
                     f"there are covered — agent never installed or evaded.")
        return AnomalyFinding(
            detector_key='unprotected_newcomer', dimension_key=a['asset_id'],
            kind='condition', severity=ctx.params.get('severity', 'high'),
            alert_type='security',
            title=f"Unprotected {cat} in secured segment {seg}",
            description=narrative, asset_ids=[a['asset_id']],
            assets_by_category={cat: 1} if cat else {},
            data_sources=list(a.get('data_source') or []),
            evidence={'detector': 'unprotected_newcomer', 'segment': seg,
                      'asset_id': a['asset_id'], 'category': cat,
                      'segment_eligible': eligible_n, 'segment_covered': covered_n,
                      'segment_coverage_pct': share, 'first_seen': _iso(a.get('first_seen')),
                      'data_source': list(a.get('data_source') or []),
                      'narrative': narrative})

    def _collapse(self, ctx, findings):
        """Per-asset conditions above the aggregation threshold collapse into ONE
        condition finding — a whole unprotected subnet is a single, more urgent
        story, and the Alerts page can't flood either way. The service's
        auto-aggregation only covers event kind, so D2 collapses in-detector (same
        shape as A3's missing-batch collapse)."""
        try:
            limit = int(ctx.settings.aggregation_threshold)
        except (TypeError, ValueError):
            limit = 10
        if len(findings) <= limit:
            return findings
        asset_ids = [f.asset_ids[0] for f in findings if f.asset_ids][:25]
        segments = sorted({f.evidence.get('segment') for f in findings
                           if f.evidence.get('segment')})
        by_cat = {}
        for f in findings:
            for c, cnt in (f.assets_by_category or {}).items():
                by_cat[c] = by_cat.get(c, 0) + cnt
        n = len(findings)
        shown = ', '.join(segments[:15]) + ('…' if len(segments) > 15 else '')
        narrative = (f"{n} unprotected newcomers across {len(segments)} secured segment(s): "
                     f"{shown}. A whole unprotected subnet is a more urgent story than "
                     f"{n} separate cards.")
        return [AnomalyFinding(
            detector_key='unprotected_newcomer', dimension_key='aggregate',
            kind='condition', severity=ctx.params.get('severity', 'high'),
            alert_type='security',
            title=f"{n} unprotected newcomers in secured segments",
            description=narrative, asset_ids=asset_ids, assets_by_category=by_cat,
            evidence={'detector': 'unprotected_newcomer', 'aggregated': True, 'count': n,
                      'segments': segments, 'sample_asset_ids': asset_ids,
                      'narrative': narrative})]
