# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phase-5 ML anomaly detectors: unsupervised, deployment-local outlier scoring.

Both fit on the deployment's OWN trailing history and score current observations —
no shipped model, no labels, air-gap-safe by construction (spec §4 phase 3). They
produce findings the same shape every other anomaly detector produces; the service
aggregates them and turns them into alerts through the normal alert lifecycle.

Scorer: the dependency-free HistogramOutlierScorer (spec §14.1 substitutes
scikit-learn's IsolationForest, which is not in the runtime image).

  * MlNewcomerDetector — fit on the pre-existing fleet, score THIS run's created
    assets. Generalises C1/C2/D2 to attribute *combinations* rules can't express.
  * MlBehaviorDetector — fit AND score on every asset's recent change-behaviour
    window (fleet distribution vs each asset's own window). Generalises B1/B2.
"""
import logging
import math
import time

from src.modules.anomaly_detection.detectors.ml_scoring import HistogramOutlierScorer
from src.modules.anomaly_detection.domain import ASSET_LABEL_TOKEN, AnomalyFinding

logger = logging.getLogger(__name__)


def _norm(value):
    """Categorical feature value -> a stable non-empty string ('<none>' for
    missing/blank), so nulls form their own category instead of crashing the log."""
    if value is None:
        return '<none>'
    text = str(value).strip()
    return text or '<none>'


def _first(seq):
    return seq[0] if seq else None


def newcomer_features(row):
    """Feature vector shared by the fit set (pre-existing fleet) and the score set
    (this run's created assets). Both carry the SAME raw columns, so applying this
    one function to both guarantees fit/score feature parity — the reason
    get_created_assets was widened to also return data_source_category + confidence.
    Categoricals: category, OS, manufacturer, and the sorted source-category combo
    (a source-coverage fingerprint). Numerics: source count and confidence."""
    sources = row.get('data_source') or []
    # De-duplicate: data_source_category holds one entry per source, so an asset
    # covered by two same-category sources yields e.g. ['directory','directory'].
    # Without set() that fragments into a distinct 'directory|directory' combo
    # (24% of the dev fleet has such duplicates) and reads as a rare source mix —
    # a spurious outlier. The fingerprint is the SET of categories, order-free.
    categories = sorted({str(c).strip() for c in (row.get('data_source_category') or [])
                         if c and str(c).strip()})
    confidence = row.get('confidence')
    if confidence is not None:
        confidence = float(confidence)
        if math.isnan(confidence):    # NaN confidence is missing, not a value to echo
            confidence = None         # (a raw NaN would break the jsonb stats write)
    return {
        'category': _norm(row.get('category')),
        'os': _norm(row.get('os_normalized')),
        'manufacturer': _norm(_first(row.get('manufacturer'))),
        'source_categories': '|'.join(categories) or '<none>',
        'source_count': len(sources),
        'confidence': confidence,
    }


def behavior_features(row):
    """Per-asset change-behaviour features over the window (all numeric): how many
    runs the asset was active in, how many distinct attributes it touched, and its
    total change volume."""
    return {
        'runs_active': int(row.get('runs_active') or 0),
        'distinct_attrs': int(row.get('distinct_attrs') or 0),
        'change_events': int(row.get('event_count') or 0),
    }


def _severity(percentile, params):
    """Fixed percentile bands from rule params: top pct_high% -> high, top
    pct_medium% -> medium, else no finding."""
    if percentile >= 100.0 - float(params.get('pct_high', 0.1)):
        return 'high'
    if percentile >= 100.0 - float(params.get('pct_medium', 0.5)):
        return 'medium'
    return None


def _fit_within_budget(key, fit_features, categorical, numeric, top_k, budget):
    """Fit the scorer, timing only the fit; over budget -> log + None (skip the run,
    per spec §4 phase 3). The scorer output itself is deterministic — this guard is
    the only time-dependent path, kept out of scoring."""
    scorer = HistogramOutlierScorer(top_k=top_k)
    started = time.perf_counter()
    scorer.fit(fit_features, categorical, numeric)
    elapsed = time.perf_counter() - started
    if elapsed > budget:
        logger.info("%s: fit took %.3fs > fit_budget_seconds %.1f — skipping this run",
                    key, elapsed, budget)
        return None
    return scorer


def _contributions_evidence(contributions):
    return [{'feature': c['feature'], 'value': c['value']} for c in contributions]


def _contributions_technical(contributions):
    return [{'feature': c['feature'], 'value': c['value'],
             'population_share': round(float(c['population_share']), 6)}
            for c in contributions]


def _contributions_phrase(contributions):
    """Plain 'feature (value)' fragments for the narrative — the raw population-share
    percentages live in evidence['technical']['contributions'], not in user copy."""
    parts = [f"{c['feature'].replace('_', ' ')} {c['value']!r}" for c in contributions]
    return ', '.join(parts)


def _score_to_findings(scorer, score_rows, by_id, params, finding_fn, fit_n):
    """Shared newcomer/behavior loop: score each row, keep the ones past the
    percentile gate, and build a finding via the detector's own finding_fn. The two
    detectors differ in their fit/score row sources and finding shape, not in this
    loop — keep it in one place so the scoring contract can't silently diverge."""
    findings = []
    for scored in scorer.score(score_rows):
        percentile = scorer.self_score_percentile(scored.score)
        severity = _severity(percentile, params)
        if severity is None:
            continue
        findings.append(
            finding_fn(by_id.get(scored.key, {}), scored, percentile, severity, fit_n))
    return findings


class MlNewcomerDetector:
    """ML newcomer flavor: score this run's created assets against the pre-existing
    fleet's feature distribution. Kind: event, one finding per outlier asset."""
    key = 'ml_newcomer'
    DEFAULT_PARAMS = {'pct_medium': 0.5, 'pct_high': 0.1,
                      'min_fit_rows': 500, 'fit_budget_seconds': 10,
                      'top_k_categories': 15, 'max_fit_rows': 25000}
    CATEGORICAL = ['category', 'os', 'manufacturer', 'source_categories']
    NUMERIC = ['source_count', 'confidence']

    def evaluate(self, ctx):
        params = ctx.params
        min_fit = int(params.get('min_fit_rows', 500))
        max_fit = int(params.get('max_fit_rows', 25000))
        fit_rows = ctx.repo.get_ml_newcomer_fit_rows(ctx.run_started_at, max_fit) or []
        if len(fit_rows) < min_fit:
            logger.info("ml_newcomer: %d fit rows < min_fit_rows %d — learning, skip",
                        len(fit_rows), min_fit)
            return []
        created = ctx.repo.get_created_assets(ctx.generation_run_id) or []
        if not created:
            return []
        scorer = _fit_within_budget(
            self.key, [newcomer_features(r) for r in fit_rows], self.CATEGORICAL,
            self.NUMERIC, int(params.get('top_k_categories', 15)),
            float(params.get('fit_budget_seconds', 10)))
        if scorer is None:
            return []

        by_id = {r['asset_id']: r for r in created}
        score_rows = [{**newcomer_features(r), 'key': r['asset_id']} for r in created]
        return _score_to_findings(
            scorer, score_rows, by_id, params, self._finding, len(fit_rows))

    def _finding(self, asset_row, scored, percentile, severity, fit_n):
        asset_id = scored.key
        category = asset_row.get('category')
        narrative = (
            f"New asset {ASSET_LABEL_TOKEN} looks unlike the rest of this fleet. "
            f"Most unlike the fleet: {_contributions_phrase(scored.contributions)}.")
        return AnomalyFinding(
            detector_key=self.key, dimension_key=asset_id, kind='event',
            severity=severity, alert_type='security',
            title=f"Unusual new asset — {ASSET_LABEL_TOKEN}", description=narrative,
            asset_ids=[asset_id], assets_by_category={category: 1} if category else {},
            evidence={'detector': self.key, 'asset_id': asset_id,
                      'contributions': _contributions_evidence(scored.contributions),
                      'technical': {
                          'score': round(float(scored.score), 4),
                          'self_percentile': round(percentile, 3),
                          'contributions': _contributions_technical(scored.contributions),
                          'fit_rows': fit_n,
                      },
                      'narrative': narrative})


class MlBehaviorDetector:
    """ML behavior flavor: fit on the fleet's change-behaviour distribution over the
    window and score every asset's own window against it. Kind: event, one finding
    per outlier asset."""
    key = 'ml_behavior'
    DEFAULT_PARAMS = {'pct_medium': 0.5, 'pct_high': 0.1,
                      'min_fit_rows': 500, 'fit_budget_seconds': 10, 'window_runs': 30}
    CATEGORICAL = []
    NUMERIC = ['runs_active', 'distinct_attrs', 'change_events']

    def evaluate(self, ctx):
        params = ctx.params
        min_fit = int(params.get('min_fit_rows', 500))
        rows = ctx.repo.get_ml_behavior_rows(int(params.get('window_runs', 30))) or []
        if len(rows) < min_fit:
            logger.info("ml_behavior: %d rows < min_fit_rows %d — learning, skip",
                        len(rows), min_fit)
            return []
        # fit == score for behavior, so build the feature vectors once and reuse.
        feats = [behavior_features(r) for r in rows]
        scorer = _fit_within_budget(
            self.key, feats, self.CATEGORICAL, self.NUMERIC, 15,
            float(params.get('fit_budget_seconds', 10)))
        if scorer is None:
            return []

        by_id = {r['asset_id']: r for r in rows}
        score_rows = [{**f, 'key': r['asset_id']} for f, r in zip(feats, rows)]
        return _score_to_findings(
            scorer, score_rows, by_id, params, self._finding, len(rows))

    def _finding(self, asset_row, scored, percentile, severity, fit_n):
        asset_id = scored.key
        narrative = (
            f"{ASSET_LABEL_TOKEN}'s recent change behavior is unusual for this fleet: "
            f"active in {asset_row.get('runs_active')} runs, "
            f"{asset_row.get('distinct_attrs')} distinct attributes, "
            f"{asset_row.get('event_count')} changes. Most unlike the fleet: "
            f"{_contributions_phrase(scored.contributions)}.")
        # kind='condition': this detector scores a rolling-window population, so
        # a persistently anomalous asset reappears run after run. Event-kind
        # fingerprints are date-suffixed by the service and would re-fire a new
        # alert every day; condition-kind updates one alert and auto-resolves when
        # the asset's behaviour normalizes (mirrors B1b/B2, the detectors this one
        # generalizes). PR #825 review finding.
        return AnomalyFinding(
            detector_key=self.key, dimension_key=asset_id, kind='condition',
            severity=severity, alert_type='data_quality',
            title=f"Unusual change behavior — {ASSET_LABEL_TOKEN}", description=narrative,
            asset_ids=[asset_id],
            evidence={'detector': self.key, 'asset_id': asset_id,
                      'runs_active': asset_row.get('runs_active'),
                      'distinct_attrs': asset_row.get('distinct_attrs'),
                      'change_events': asset_row.get('event_count'),
                      'contributions': _contributions_evidence(scored.contributions),
                      'technical': {
                          'score': round(float(scored.score), 4),
                          'self_percentile': round(percentile, 3),
                          'contributions': _contributions_technical(scored.contributions),
                          'fit_rows': fit_n,
                      },
                      'narrative': narrative})
