# SPDX-License-Identifier: AGPL-3.0-or-later
"""Volume detectors: A1 mass-new, A2 mass-missing, A3 source-volume,
A4 attribute-churn, A5 identity-churn, A6 confidence-shift."""
from src.modules.anomaly_detection.baselines import build_baseline
from src.modules.anomaly_detection.domain import (
    AnomalyFinding,
    robust_z,
    severity_from_z,
)

_SEVERITY_RANK = {'medium': 1, 'high': 2, 'critical': 3}


def _stats_series(rows, path):
    """Project a per-run scalar out of each run's stats jsonb, keeping the
    started_at/triggered_by that build_baseline selects on."""
    out = []
    for r in rows:
        v = r.get('stats') or {}
        for p in path:
            v = v.get(p) if isinstance(v, dict) else None
        out.append({'started_at': r['started_at'],
                    'triggered_by': r.get('triggered_by'), 'value': v})
    return out


def systemic_attrs(attr_counts, total_assets, systemic_pct):
    """Attributes that changed on >= systemic_pct% of the fleet this run — the
    "administrative/source change" shape (A4, the 47k-rename). Exported so the
    B-family (B1a burst, B1b thrash) can exclude the same set. attr_counts is
    {attr: change_count}; returns the set of systemic attribute names."""
    if not total_assets or total_assets <= 0:
        return set()
    threshold = systemic_pct / 100.0 * total_assets
    return {attr for attr, n in attr_counts.items() if attr and (n or 0) >= threshold}


def _baseline_phrase(observed, median):
    """Plain-language stand-in for the '(mode, MAD, z=…)' baseline jargon."""
    usual = int(median)
    return f"far above the usual ~{usual}" if observed >= median else f"well below the usual ~{usual}"


def _evidence(detector, observed, unit, baseline, rz, extra=None):
    return {
        'detector': detector, 'observed': observed, 'unit': unit,
        'typical': int(rz.median),
        'technical': {
            'median': rz.median, 'mad': rz.mad, 'window_runs': rz.n,
            'seasonality': baseline.mode, 'samples': baseline.samples[:20],
            'z': round(rz.z, 2),
        },
        **(extra or {}),
    }


def _source_series(rows, source_name):
    """Per-source unique_assets series from each run's stats.batch_results."""
    out = []
    for r in rows:
        val = None
        for br in ((r.get('stats') or {}).get('batch_results') or []):
            if br.get('integration_name') == source_name:
                val = br.get('unique_assets')
                break
        out.append({'started_at': r['started_at'],
                    'triggered_by': r.get('triggered_by'), 'value': val})
    return out


def _low_share_series(rows):
    """Per-run low-confidence share (percentage points) from each run's top-level
    confidence_histogram (lifted into pipeline_runs.stats since Phase 1). Runs
    without the stat contribute None -> dropped by build_baseline (learning)."""
    out = []
    for r in rows:
        h = (r.get('stats') or {}).get('confidence_histogram')
        share = None
        if isinstance(h, dict):
            total = (h.get('low') or 0) + (h.get('medium') or 0) + (h.get('high') or 0)
            if total > 0:
                share = 100.0 * (h.get('low') or 0) / total
        out.append({'started_at': r['started_at'],
                    'triggered_by': r.get('triggered_by'), 'value': share})
    return out


class MassNewAssetsDetector:
    key = 'mass_new_assets'

    def evaluate(self, ctx):
        events = (((ctx.current_stats.get('stages') or {}).get('chronicle') or {})
                  .get('event_type_counts') or {})
        observed = events.get('asset_created', 0) or 0
        if observed <= 0:
            return []

        history = _stats_series(
            ctx.repo.get_recent_run_stats(ctx.run_started_at),
            ('chronicle_events', 'asset_created'))
        b = build_baseline(history, ctx.run_started_at.weekday(), ctx.params)
        if b.learning:
            return []

        rz = robust_z(observed, b.samples)
        sev = severity_from_z(rz.z, ctx.params, b.tolerance_factor)
        if not sev or observed < ctx.params.get('min_count', 20):
            return []

        narrative = (f"{observed} new assets appeared this run — "
                     f"{_baseline_phrase(observed, rz.median)}.")
        ev = _evidence('mass_new_assets', observed, 'assets', b, rz,
                       extra={'narrative': narrative})
        return [AnomalyFinding(
            detector_key='mass_new_assets', dimension_key='', kind='event',
            severity=sev, alert_type='operational', subject_type='metric',
            title=f"Mass new assets: {observed} created",
            description=narrative, evidence=ev,
            sample_asset_ids=ctx.repo.get_event_asset_ids(
                ctx.generation_run_id, 'asset_created', 25))]


class MassMissingDetector:
    key = 'mass_missing'

    def evaluate(self, ctx):
        observed = ctx.repo.get_missing_onset_count(ctx.run_started_at) or 0
        if observed <= 0:
            return []

        # grace_missing_onsets is lifted to the top of run stats by the grace
        # stage (Task 11); until it accumulates, the series is None -> learning.
        history = _stats_series(
            ctx.repo.get_recent_run_stats(ctx.run_started_at),
            ('grace_missing_onsets',))
        b = build_baseline(history, ctx.run_started_at.weekday(), ctx.params)
        if b.learning:
            return []

        rz = robust_z(observed, b.samples)
        sev = severity_from_z(rz.z, ctx.params, b.tolerance_factor)
        if not sev or observed < ctx.params.get('min_count', 20):
            return []

        narrative = (f"{observed} assets went missing from all sources this run — "
                     f"{_baseline_phrase(observed, rz.median)}.")
        ev = _evidence('mass_missing', observed, 'assets', b, rz,
                       extra={'narrative': narrative})
        return [AnomalyFinding(
            detector_key='mass_missing', dimension_key='', kind='event',
            severity=sev, alert_type='security', subject_type='metric',
            title=f"Mass disappearance: {observed} assets missing",
            description=narrative, evidence=ev)]


class SourceVolumeDetector:
    """A3: per-source drop/spike (banded z), plus failed-batch and missing-batch
    routings. Kind: condition per (source, routing) — persists while abnormal."""
    key = 'source_volume'

    def evaluate(self, ctx):
        rows = ctx.repo.get_recent_run_stats(ctx.run_started_at)
        current = ctx.current_batches or []
        current_names = {b.get('integration_name') for b in current}
        findings = []
        for b in current:
            name = b.get('integration_name')
            if not name:
                continue
            if b.get('success') is False:
                findings.append(self._failed(name, b))
                continue
            vf = self._volume(ctx, rows, name, b)
            if vf:
                findings.append(vf)
        findings.extend(self._collapse_missing(ctx, self._missing(rows, current_names)))
        return findings

    def _failed(self, name, b):
        # Reached when s20's `_current_batches` reports success=False for a batch
        # that loaded but produced no usable records (the generation service
        # threads real per-batch outcomes into ctx.stats['current_batches']).
        # Distinct from the missing routing (a source that produced no batch at
        # all) and the volume z (a batch that produced an abnormal count).
        error = b.get('error')
        narrative = f"Source '{name}' batch failed this run: {error or 'connector error'}."
        ev = {'detector': 'source_volume', 'source': name, 'routing': 'failed',
              'error': error, 'narrative': narrative}
        return AnomalyFinding(
            detector_key='source_volume', dimension_key=f'{name}:failed', kind='condition',
            severity='high', alert_type='operational', subject_type='metric',
            title=f"Source '{name}' connector batch failed", description=narrative,
            evidence=ev, data_sources=[name])

    def _volume(self, ctx, rows, name, b):
        observed = b.get('unique_assets') or 0
        base = build_baseline(_source_series(rows, name),
                              ctx.run_started_at.weekday(), ctx.params)
        if base.learning:
            return None
        rz = robust_z(observed, base.samples)
        sev = severity_from_z(abs(rz.z), ctx.params, base.tolerance_factor)
        abs_change = abs(observed - rz.median)
        rel_change = abs_change / max(abs(rz.median), 1)
        if (not sev or rel_change < ctx.params.get('min_pct', 15) / 100.0
                or abs_change < ctx.params.get('min_count', 25)):
            return None
        direction = 'drop' if observed < rz.median else 'spike'
        narrative = (f"Source '{name}' {direction}: {observed} devices this run — "
                     f"{_baseline_phrase(observed, rz.median)}.")
        ev = _evidence('source_volume', observed, 'devices', base, rz, extra={
            'source': name, 'routing': 'volume', 'direction': direction,
            'absolute_change': int(abs_change),
            'relative_change_pct': round(rel_change * 100, 1), 'narrative': narrative})
        return AnomalyFinding(
            detector_key='source_volume', dimension_key=f'{name}:volume', kind='condition',
            severity=sev, alert_type='operational', subject_type='metric',
            title=f"Source '{name}' volume {direction}: {observed}",
            description=narrative, evidence=ev, data_sources=[name])

    def _missing(self, rows, current_names):
        recent = rows[:5]
        presence = {}
        for r in recent:
            for name in {br.get('integration_name')
                         for br in ((r.get('stats') or {}).get('batch_results') or [])
                         if br.get('integration_name')}:
                presence[name] = presence.get(name, 0) + 1
        out = []
        for name, cnt in sorted(presence.items()):
            if cnt >= 3 and name not in current_names:
                narrative = (f"Source '{name}' produced no batch this run "
                             f"(present in {cnt} of the last {len(recent)} runs).")
                out.append(AnomalyFinding(
                    detector_key='source_volume', dimension_key=f'{name}:missing',
                    kind='condition', severity='high', alert_type='operational', subject_type='metric',
                    title=f"Source '{name}' missing batch this run",
                    description=narrative, data_sources=[name],
                    evidence={'detector': 'source_volume', 'source': name,
                              'routing': 'missing', 'recent_presence': cnt,
                              'recent_runs': len(recent), 'narrative': narrative}))
        return out

    def _collapse_missing(self, ctx, missing):
        """Many sources missing at once is one story (a partial/manual run or an
        integration-schedule change), not N connector failures — collapse above
        the aggregation threshold so a partial run can't flood the Alerts page."""
        try:
            limit = int(ctx.settings.aggregation_threshold)
        except (TypeError, ValueError):
            limit = 10
        if len(missing) <= limit:
            return missing
        names = sorted(f.data_sources[0] for f in missing if f.data_sources)
        shown = ', '.join(names[:15]) + ('…' if len(names) > 15 else '')
        narrative = (f"{len(missing)} sources produced no batch this run: {shown}. "
                     "Likely a partial/manual run or an integration-schedule change "
                     "rather than per-source connector failures.")
        return [AnomalyFinding(
            detector_key='source_volume', dimension_key='missing:aggregate',
            kind='condition', severity='high', alert_type='operational', subject_type='metric',
            title=f"{len(missing)} sources produced no batch this run",
            description=narrative, data_sources=names[:25],
            evidence={'detector': 'source_volume', 'routing': 'missing',
                      'aggregated': True, 'count': len(missing),
                      'sources': names, 'narrative': narrative})]


class AttributeChurnDetector:
    """A4: systemic (one attribute changed fleet-wide — the 47k-rename shape)
    vs scattered churn (broad change spike vs baseline). Kind: event."""
    key = 'attribute_churn'

    def evaluate(self, ctx):
        events = (((ctx.current_stats.get('stages') or {}).get('chronicle') or {})
                  .get('event_type_counts') or {})
        total_events = events.get('attribute_updated', 0) or 0
        if total_events <= 0:
            return []
        # Fleet size isn't in the in-stage gen-stats at s20, so fall back to the
        # committed count (post-commit) — otherwise the systemic branch is dead.
        total_assets = int(ctx.current_stats.get('total_assets') or 0) or ctx.repo.get_total_assets()
        breakdown = ctx.repo.get_attribute_breakdown(ctx.generation_run_id) or []
        counts = {row.get('attr'): row.get('n') or 0 for row in breakdown if row.get('attr')}
        systemic = systemic_attrs(counts, total_assets, ctx.params.get('systemic_pct', 20))

        findings = []
        for row in breakdown:
            if row.get('attr') in systemic:
                findings.append(self._systemic(row.get('attr'), row.get('n') or 0, total_assets))

        # Exact scattered count: events touching >= 1 non-systemic attribute. An
        # event that changed BOTH a systemic and a non-systemic attribute counts
        # once here; the old `total_events - systemic_sum` double-subtracted it.
        scattered = self._scattered(
            ctx, ctx.repo.get_scattered_event_count(ctx.generation_run_id, systemic))
        if scattered:
            findings.append(scattered)
        return findings

    def _systemic(self, attr, n, total_assets):
        share = round(100.0 * n / total_assets, 1)
        attr_label = attr.replace('_', ' ')
        narrative = (f"The '{attr_label}' field changed on {n} assets ({share}% of the fleet) "
                     f"this run — likely an administrative or source change.")
        return AnomalyFinding(
            detector_key='attribute_churn', dimension_key=f'systemic:{attr}', kind='event',
            severity='low', alert_type='data_quality', subject_type='metric',
            title=f"Systemic change: '{attr_label}' on {n} assets ({share}% of fleet)",
            description=narrative,
            evidence={'detector': 'attribute_churn', 'routing': 'systemic',
                      'attribute': attr, 'count': n, 'fleet': total_assets,
                      'share_pct': share, 'narrative': narrative})

    def _scattered(self, ctx, scattered):
        if scattered < ctx.params.get('min_count', 100):
            return None
        base = build_baseline(
            _stats_series(ctx.repo.get_recent_run_stats(ctx.run_started_at),
                          ('chronicle_events', 'attribute_updated')),
            ctx.run_started_at.weekday(), ctx.params)
        if base.learning:
            return None
        rz = robust_z(scattered, base.samples)
        sev = severity_from_z(rz.z, ctx.params, base.tolerance_factor)
        if not sev:
            return None
        narrative = (f"{scattered} scattered attribute changes this run — "
                     f"{_baseline_phrase(scattered, rz.median)}.")
        ev = _evidence('attribute_churn', scattered, 'events', base, rz,
                       extra={'routing': 'scattered', 'narrative': narrative})
        return AnomalyFinding(
            detector_key='attribute_churn', dimension_key='scattered', kind='event',
            severity=sev, alert_type='data_quality', subject_type='metric',
            title=f"Scattered attribute churn: {scattered} changes",
            description=narrative, evidence=ev)


class IdentityChurnDetector:
    """A5: identity-instability rate. Three independent sub-signals, each its own
    event finding — merged-asset surge, re-add surge, and a simultaneous
    created+removed spike (the re-mint / over-merge signature; incidents #802,
    Proxmox guest-MAC overmerge). Complements the per-asset 059 conflict rules at
    the rate level. Kind: event."""
    key = 'identity_churn'

    def evaluate(self, ctx):
        rows = ctx.repo.get_recent_run_stats(ctx.run_started_at)
        events = (((ctx.current_stats.get('stages') or {}).get('chronicle') or {})
                  .get('event_type_counts') or {})
        findings = []
        merged = int(ctx.current_stats.get('merged_assets') or 0)
        f = self._spike(ctx, rows, 'merged', merged, ('merged_assets',),
                        'merged assets', 'identity consolidation')
        if f:
            findings.append(f)
        readded = int(events.get('asset_readded') or 0)
        f = self._spike(ctx, rows, 'readded', readded,
                        ('chronicle_events', 'asset_readded'),
                        're-added assets', 'assets reappearing after removal',
                        sample_event_type='asset_readded')
        if f:
            findings.append(f)
        f = self._remint(ctx, rows, events)
        if f:
            findings.append(f)
        return findings

    def _spike(self, ctx, rows, dim, observed, path, unit, gloss, sample_event_type=None):
        if observed <= 0:
            return None
        base = build_baseline(_stats_series(rows, path),
                              ctx.run_started_at.weekday(), ctx.params)
        if base.learning:
            return None
        rz = robust_z(observed, base.samples)
        sev = severity_from_z(rz.z, ctx.params, base.tolerance_factor)
        if not sev or observed < ctx.params.get('min_count', 50):
            return None
        narrative = (f"{observed} {unit} this run — "
                     f"{_baseline_phrase(observed, rz.median)}; {gloss}.")
        ev = _evidence('identity_churn', observed, unit, base, rz,
                       extra={'sub_signal': dim, 'narrative': narrative})
        sample = (ctx.repo.get_event_asset_ids(ctx.generation_run_id, sample_event_type, 25)
                  if sample_event_type else [])
        return AnomalyFinding(
            detector_key='identity_churn', dimension_key=dim, kind='event',
            severity=sev, alert_type='data_quality', subject_type='metric',
            title=f"Identity churn: {observed} {unit}",
            description=narrative, evidence=ev, sample_asset_ids=sample)

    def _remint(self, ctx, rows, events):
        """Simultaneous created + removed spike in the same run — the re-mint
        signature. BOTH series must spike (a created-only spike is A1's job)."""
        created = int(events.get('asset_created') or 0)
        removed = int(events.get('asset_removed') or 0)
        min_count = ctx.params.get('min_count', 50)
        if created < min_count or removed < min_count:
            return None
        cb = build_baseline(_stats_series(rows, ('chronicle_events', 'asset_created')),
                            ctx.run_started_at.weekday(), ctx.params)
        rb = build_baseline(_stats_series(rows, ('chronicle_events', 'asset_removed')),
                            ctx.run_started_at.weekday(), ctx.params)
        if cb.learning or rb.learning:
            return None
        cz = robust_z(created, cb.samples)
        rz = robust_z(removed, rb.samples)
        sev_c = severity_from_z(cz.z, ctx.params, cb.tolerance_factor)
        sev_r = severity_from_z(rz.z, ctx.params, rb.tolerance_factor)
        if not sev_c or not sev_r:
            return None
        sev = min((sev_c, sev_r), key=lambda s: _SEVERITY_RANK[s])   # the weaker gates
        narrative = (
            f"Simultaneous spike of {created} created and {removed} removed assets in one run "
            f"— assets are being removed and re-created under new identities rather than "
            f"updated in place.")
        ev = {'detector': 'identity_churn', 'sub_signal': 'remint',
              'created': {'observed': created},
              'removed': {'observed': removed},
              'technical': {'window_runs': cz.n, 'seasonality': cb.mode,
                            'created_median': cz.median, 'created_z': round(cz.z, 2),
                            'removed_median': rz.median, 'removed_z': round(rz.z, 2)},
              'narrative': narrative}
        return AnomalyFinding(
            detector_key='identity_churn', dimension_key='remint', kind='event',
            severity=sev, alert_type='data_quality', subject_type='metric',
            title=f"Identity re-mint signature: {created} created + {removed} removed same run",
            description=narrative, evidence=ev)


class ConfidenceShiftDetector:
    """A6: the fleet confidence distribution degraded — the low-confidence share
    rose vs this deployment's own baseline. Kind: condition (persists while
    degraded, auto-resolves when back in band)."""
    key = 'confidence_shift'

    def evaluate(self, ctx):
        med, high = ctx.repo.get_confidence_boundaries()
        cur = ctx.repo.get_confidence_histogram(med, high)
        total = (cur.get('low') or 0) + (cur.get('medium') or 0) + (cur.get('high') or 0)
        if total <= 0:
            return []
        share = 100.0 * (cur.get('low') or 0) / total

        base = build_baseline(
            _low_share_series(ctx.repo.get_recent_run_stats(ctx.run_started_at)),
            ctx.run_started_at.weekday(), ctx.params)
        if base.learning:
            return []
        rz = robust_z(share, base.samples)
        sev = severity_from_z(rz.z, ctx.params, base.tolerance_factor)
        delta = share - rz.median
        if not sev or delta < ctx.params.get('min_pp', 5):
            return []
        narrative = (
            f"Low-confidence share is {round(share, 1)}% this run — "
            f"{_baseline_phrase(share, rz.median)}% (up {round(delta, 1)} points). "
            f"The confidence distribution degraded.")
        ev = _evidence('confidence_shift', round(share, 1), 'percent', base, rz, extra={
            'histogram': cur, 'delta_pp': round(delta, 1),
            'boundaries': {'medium': med, 'high': high}, 'narrative': narrative})
        return [AnomalyFinding(
            detector_key='confidence_shift', dimension_key='low-confidence', kind='condition',
            severity=sev, alert_type='data_quality', subject_type='metric',
            title=f"Confidence degraded: low-confidence share {round(share, 1)}%",
            description=narrative, evidence=ev)]
