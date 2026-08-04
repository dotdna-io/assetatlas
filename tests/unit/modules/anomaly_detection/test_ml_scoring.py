# SPDX-License-Identifier: AGPL-3.0-or-later
"""HistogramOutlierScorer — dependency-free HBOS-style outlier scoring.

The scorer fits per-feature rarity tables on a fit population and scores rows by
summed surprise (-log p). These tests pin the behaviour the ML detectors
rely on: a planted outlier out-scores the fleet, unseen/out-of-range values get a
smoothed (never -inf/NaN) minimum, contributions name the genuinely rare features,
and everything is deterministic (no RNG)."""
import math

import pytest

from src.modules.anomaly_detection.detectors.ml_scoring import (
    HistogramOutlierScorer,
    ScoredRow,
)

CAT = ['category', 'os', 'manufacturer']


def _fit_rows():
    """A fleet with one dominant profile and two progressively rarer ones, so the
    fit-set scores form a real distribution rather than a single spike."""
    rows = []
    rows += [{'category': 'Workstation', 'os': 'Windows 10', 'manufacturer': 'Dell'}] * 970
    rows += [{'category': 'Server', 'os': 'Linux', 'manufacturer': 'HP'}] * 25
    rows += [{'category': 'Printer', 'os': 'embedded', 'manufacturer': 'HP'}] * 5
    return rows


def test_planted_outlier_scores_above_999th_percentile_typical_does_not():
    scorer = HistogramOutlierScorer(top_k=15)
    scorer.fit(_fit_rows(), categorical=CAT, numeric=[])

    typical = {'key': 't', 'category': 'Workstation', 'os': 'Windows 10',
               'manufacturer': 'Dell'}
    outlier = {'key': 'o', 'category': 'HVAC Controller', 'os': 'VxWorks',
               'manufacturer': 'Siemens'}
    [st] = scorer.score([typical])
    [so] = scorer.score([outlier])

    # WHY: the whole detector fires off "this device is in the extreme tail of the
    # fleet's own rarity distribution". A never-before-seen combo must exceed the
    # 99.9th self-percentile; the fleet's dominant profile must sit well below it.
    assert so.score > st.score
    assert scorer.self_score_percentile(so.score) >= 99.9
    assert scorer.self_score_percentile(st.score) < 99.9
    assert isinstance(so, ScoredRow) and so.key == 'o'


def test_contributions_rank_the_rare_feature_first_and_carry_population_shares():
    scorer = HistogramOutlierScorer(top_k=15)
    scorer.fit(_fit_rows(), categorical=CAT, numeric=[])

    # Common category + manufacturer, but a never-seen OS: OS must dominate the
    # explanation, and the common features must report their real fleet shares.
    row = {'key': 'x', 'category': 'Workstation', 'os': 'TempleOS', 'manufacturer': 'Dell'}
    [sr] = scorer.score([row])

    assert sr.contributions[0]['feature'] == 'os'
    assert sr.contributions[0]['value'] == 'TempleOS'
    assert sr.contributions[0]['population_share'] == 0.0     # 0% of the fleet had it
    shares = {c['feature']: c['population_share'] for c in sr.contributions}
    assert shares['category'] > 0.9 and shares['manufacturer'] > 0.9   # ~97% Dell/WS
    assert len(sr.contributions) == 3                         # top-3


def test_unseen_categorical_gets_smoothed_minimum_no_inf_or_nan():
    scorer = HistogramOutlierScorer(top_k=15, laplace=1.0)
    rows = [{'os': 'A'}] * 50 + [{'os': 'B'}] * 50    # 2 distinct, nothing collapses
    scorer.fit(rows, categorical=['os'], numeric=[])

    [sr] = scorer.score([{'key': 'u', 'os': 'ZZZ'}])   # never seen in the fit set
    # K = kept(2) + '<other>'(1) = 3; N=100; laplace=1 -> p = 1/103, the smoothed
    # floor. The surprise is finite (log 103), never +inf, never NaN.
    assert math.isfinite(sr.score)
    assert sr.score == pytest.approx(math.log(103))
    assert sr.contributions[0]['population_share'] == 0.0


def test_numeric_sparse_tail_is_more_surprising_than_dense_mode():
    scorer = HistogramOutlierScorer(n_bins=10)
    # 900 assets clustered tightly near 1; 100 spread across a wide sparse tail —
    # the shape of a real behaviour feature (most assets quiet, a few thrash).
    dense = [{'n': 1.0 + (i % 5) * 0.01} for i in range(900)]     # ~1.00..1.04
    sparse = [{'n': float(50 + 10 * i)} for i in range(100)]      # 50..1040, very spread
    scorer.fit(dense + sparse, categorical=[], numeric=['n'])

    [d] = scorer.score([{'key': 'd', 'n': 1.0}])       # in the dense mode
    [s] = scorer.score([{'key': 's', 'n': 500.0}])     # in the sparse tail
    [beyond] = scorer.score([{'key': 'b', 'n': 1e6}])  # clamps to the sparse edge bin
    # WHY: a per-asset behaviour window in the sparse tail must out-score the dense
    # mode. Equal-mass quantile bins would give every decile the same probability
    # and never flag this, so density (mass / bin width) is the discriminator.
    assert s.score > d.score
    assert beyond.score > d.score                      # out-of-range flagged too
    assert math.isfinite(d.score) and math.isfinite(beyond.score)


def test_missing_numeric_value_is_finite():
    scorer = HistogramOutlierScorer(n_bins=10)
    scorer.fit([{'n': float(v)} for v in range(1, 101)], categorical=[], numeric=['n'])
    # None must not blow up the log/binning — it scores finite (NaN-safety).
    [sr] = scorer.score([{'key': 'm', 'n': None}])
    assert math.isfinite(sr.score)


def test_mode_dominated_high_cardinality_numeric_still_flags_the_tail():
    # A behaviour-shaped feature: a dominant mode (value 1) plus a long tail of MORE
    # than n_bins DISTINCT values. Here every quantile edge collapses onto the mode
    # (np.unique -> one bin) and density scores every value 0 — the detector goes
    # blind. The freq-fallback (edges.size<=2 -> frequency scoring) must keep the
    # tail surprising. Before the fix quiet and thrash both scored exactly 0.0.
    scorer = HistogramOutlierScorer(n_bins=10)
    rows = [{'n': 1.0}] * 9500
    for v in range(2, 62):                      # 60 distinct tail values -> distinct=61
        rows += [{'n': float(v)}] * 8
    scorer.fit(rows, categorical=[], numeric=['n'])
    quiet = scorer.score([{'key': 'q', 'n': 1.0}])[0]
    thrash = scorer.score([{'key': 't', 'n': 61.0}])[0]
    assert thrash.score > quiet.score + 0.5
    assert scorer.self_score_percentile(quiet.score) == 0.0     # the mode is least surprising
    assert scorer.self_score_percentile(thrash.score) > 90.0    # the tail sits high


def test_collapsed_categorical_reports_true_population_share():
    # A value ranked below top_k collapses to '<other>' for SCORING, but its
    # evidence population_share must still be its real fleet share (not 0.0), or the
    # detector's narrative tells an operator "0% of the fleet" had a value 0.2% did.
    scorer = HistogramOutlierScorer(top_k=1)      # keep only 'common'; everything else collapses
    rows = [{'v': 'common'}] * 900 + [{'v': f'r{i}'} for i in range(50) for _ in range(2)]
    scorer.fit(rows, categorical=['v'], numeric=[])
    [seen] = scorer.score([{'key': 'x', 'v': 'r7'}])    # collapsed, but appeared twice
    assert seen.contributions[0]['population_share'] == pytest.approx(2 / 1000)
    [unseen] = scorer.score([{'key': 'y', 'v': 'never'}])   # truly unseen -> 0.0
    assert unseen.contributions[0]['population_share'] == 0.0


def test_learning_needs_no_rng_scoring_is_deterministic():
    s1 = HistogramOutlierScorer(top_k=15)
    s1.fit(_fit_rows(), CAT, [])
    s2 = HistogramOutlierScorer(top_k=15)
    s2.fit(_fit_rows(), CAT, [])
    row = {'key': 'x', 'category': 'HVAC', 'os': 'VxWorks', 'manufacturer': 'Siemens'}
    a = s1.score([row])[0]
    b = s2.score([row])[0]
    assert a.score == b.score
    assert a.contributions == b.contributions
    assert s1.self_score_percentile(a.score) == s2.self_score_percentile(b.score)
