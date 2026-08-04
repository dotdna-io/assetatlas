# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dependency-free histogram outlier scorer (HBOS-style), numpy-only.

Spec §14.1 substitutes scikit-learn's IsolationForest — which is NOT in the
runtime image (`Dockerfile.base` installs `requirements-prod.txt` only) — with
this per-feature histogram model. It fits rarity tables on a population, scores a
row by summed surprise ``-log(p)`` across features, and derives severity from the
fit set's own score percentiles. Per-feature contributions are native, so it is at
least as explainable as an isolation forest while adding no dependency, and the
``Detector`` interface is unchanged so a sklearn scorer can swap in behind it later.

Design guarantees the ML detectors rely on:
  * Deterministic — no RNG anywhere (numpy quantiles/bincount + Counter only).
  * NaN-safe — every probability is Laplace-smoothed to a positive floor, so
    ``-log(p)`` is always finite: unseen categories fall back to the collapsed
    '<other>' mass, and missing / out-of-range numerics get a smoothed floor
    probability, never -inf.
"""
import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np


@dataclass
class ScoredRow:
    key: str
    score: float
    contributions: list = field(default_factory=list)  # top-3 {feature,value,population_share}


def _is_number(value):
    if value is None:
        return False
    try:
        return not math.isnan(float(value))
    except (TypeError, ValueError):
        return False


class _CategoricalFeature:
    """Frequency table over a categorical feature. Values beyond the ``top_k`` most
    frequent collapse into '<other>'; every probability is Laplace-smoothed, so an
    unseen value maps to '<other>' and still gets a positive floor probability."""

    def __init__(self, name):
        self.name = name
        self.counts = {}         # kept value -> fit count (bounds the smoothing table)
        self.full_counts = {}    # every value -> fit count (accurate population_share)
        self.other_count = 0     # collapsed (below-top_k) fit mass
        self.n = 0
        self.k = 1
        self.laplace = 1.0

    def fit(self, values, top_k, laplace):
        self.laplace = laplace
        counter = Counter(values)
        self.full_counts = counter
        # Deterministic top_k: by count desc, then value asc (stable tie-break).
        ordered = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
        self.counts = {v: c for v, c in ordered[:top_k]}
        self.other_count = sum(c for _v, c in ordered[top_k:])
        self.n = sum(counter.values())
        # Categories = kept values + the always-present '<other>' bucket, so an
        # unseen value has a defined (floor) probability even when nothing collapsed.
        self.k = len(self.counts) + 1

    def prob(self, value):
        raw = self.counts.get(value)
        num = (raw if raw is not None else self.other_count) + self.laplace
        return num / (self.n + self.laplace * self.k)

    def population_share(self, value):
        """True fraction of the fit population that carried this exact value —
        from the FULL counter, so a value ranked below top_k (collapsed into
        '<other>' for scoring) still reports its real share, not 0.0. Only a value
        never seen in the fit set reports 0.0."""
        return self.full_counts.get(value, 0) / self.n if self.n else 0.0


class _NumericFeature:
    """Numeric feature scored so that rare values are surprising, adapting to the
    feature's shape:

      * Discrete / mode-dominated (the behaviour counts runs_active,
        distinct_attrs, …): scored by exact-value FREQUENCY. A common value (the
        quiet mode) is unsurprising; a rare value (the thrasher) is surprising.
        This is what quantile-density CANNOT do — with a dominant mode, np.unique
        collapses every decile edge into a single bin and every value scores 0.
      * Genuinely continuous (e.g. confidence): scored by quantile-bin DENSITY
        (mass / bin width) — the HBOS treatment where the sparse tail (a wide,
        thinly-populated bin) has low density and thus high surprise.

    The mode is chosen by BOTH the distinct-value count AND whether quantile
    binning actually produces distinct bins: a feature with > n_bins distinct
    values but a dominant mode (so its quantile edges collapse to ≤ 1 usable bin)
    is discrete-in-disguise and must use frequency, not density — otherwise the
    tail is invisible (a real false-negative for high-cardinality behaviour data).

    Both are Laplace-smoothed to a positive floor so ``-log`` is always finite;
    out-of-range values fall to the nearest edge, missing values get their own
    smoothed probability."""

    def __init__(self, name):
        self.name = name
        self.mode = 'freq'
        self.laplace = 1.0
        self.n = 0
        self.n_num = 0
        self.missing_count = 0
        self.missing_p = 1.0
        self.counts = Counter()          # freq mode: value -> count
        self.k = 1
        self.edges = np.array([0.0, 1.0])  # density mode
        self.bin_counts = np.array([0])
        self.densities = np.array([1.0])
        self.max_density = 1.0

    def fit(self, values, n_bins, laplace):
        self.laplace = laplace
        self.n = len(values)
        nums = [float(v) for v in values if _is_number(v)]
        self.n_num = len(nums)
        self.missing_count = self.n - self.n_num
        arr = np.array(nums, dtype=float)
        distinct = int(np.unique(arr).size) if arr.size else 0
        edges = (np.unique(np.quantile(arr, np.linspace(0.0, 1.0, n_bins + 1)))
                 if arr.size else np.array([0.0, 1.0]))
        # Freq mode for discrete/mode-dominated features. `edges.size <= 2` (a
        # dominant mode collapsed every quantile edge into a single bin) is the key
        # clause: without it a high-cardinality-but-mode-dominated feature would
        # take the density path and score every value 0 — the tail invisible.
        self.mode = 'freq' if (distinct <= n_bins or edges.size <= 2) else 'density'
        if self.mode == 'freq':
            self.counts = Counter(nums)
            self.k = len(self.counts) + 1            # +1 unseen/other bucket
            cats = self.k
        else:
            self.edges = edges
            self.bin_counts = np.bincount(
                self._bin_index(arr), minlength=edges.size - 1)[:edges.size - 1]
            widths = np.diff(edges)
            positive = widths[widths > 0]
            floor = float(positive.min()) if positive.size else 1.0   # no zero-width divide
            widths = np.maximum(widths, floor)
            smoothed = (self.bin_counts + laplace) / (self.n_num + laplace * (edges.size - 1))
            self.densities = smoothed / widths
            self.max_density = float(self.densities.max())
            cats = edges.size - 1
        self.missing_p = ((self.missing_count + laplace) / (self.n + laplace * (cats + 1))
                          if self.n else 1.0)

    def _bin_index(self, x):
        """Half-open binning [e0,e1),…,[e_{m-1},e_m]; out-of-range clamped to an edge."""
        idx = np.searchsorted(self.edges[1:-1], x, side='right')
        return np.clip(idx, 0, self.edges.size - 2)

    def prob(self, value):
        if not _is_number(value):
            return self.missing_p
        x = float(value)
        if self.mode == 'freq':
            return (self.counts.get(x, 0) + self.laplace) / (self.n_num + self.laplace * self.k)
        # Normalise density to (0, 1]: densest bin -> 1 (surprise 0), sparse -> low.
        return float(self.densities[int(self._bin_index(np.array([x]))[0])]) / self.max_density

    def population_share(self, value):
        if not self.n:
            return 0.0
        if not _is_number(value):
            return self.missing_count / self.n
        x = float(value)
        if self.mode == 'freq':
            return self.counts.get(x, 0) / self.n
        return float(self.bin_counts[int(self._bin_index(np.array([x]))[0])]) / self.n


class HistogramOutlierScorer:
    def __init__(self, top_k=15, n_bins=10, laplace=1.0):
        self.top_k = top_k
        self.n_bins = n_bins
        self.laplace = laplace
        self.categorical = []
        self.numeric = []
        self._cat = {}
        self._num = {}
        self._fit_scores = np.array([], dtype=float)

    def fit(self, rows, categorical, numeric):
        self.categorical = list(categorical)
        self.numeric = list(numeric)
        self._cat = {}
        for name in self.categorical:
            f = _CategoricalFeature(name)
            f.fit([r.get(name) for r in rows], self.top_k, self.laplace)
            self._cat[name] = f
        self._num = {}
        for name in self.numeric:
            f = _NumericFeature(name)
            f.fit([r.get(name) for r in rows], self.n_bins, self.laplace)
            self._num[name] = f
        self._fit_scores = np.sort(
            np.array([self._score_row(r)[0] for r in rows], dtype=float))

    def score(self, rows):
        out = []
        for r in rows:
            total, contribs = self._score_row(r)
            out.append(ScoredRow(key=r.get('key'), score=total, contributions=contribs))
        return out

    def self_score_percentile(self, score):
        """Percentile (0–100) of ``score`` within the fit set's own score
        distribution — the fraction of fit rows *strictly less* surprising than
        ``score``. Strictly-less (not ≤) is deliberate: on a uniform fleet where
        every asset ties, each gets percentile 0, so nothing is flagged as an
        outlier — a ``≤`` rank would report 100 for the whole tied population."""
        n = self._fit_scores.size
        if n == 0:
            return 0.0
        rank = int(np.searchsorted(self._fit_scores, score, side='left'))
        return 100.0 * rank / n

    def _score_row(self, row):
        surprises = []   # (surprise, feature, original_value, population_share)
        for name in self.categorical:
            f = self._cat[name]
            v = row.get(name)
            surprises.append((-math.log(f.prob(v)), name, v, f.population_share(v)))
        for name in self.numeric:
            f = self._num[name]
            v = row.get(name)
            surprises.append((-math.log(f.prob(v)), name, v, f.population_share(v)))
        total = math.fsum(s for s, *_ in surprises)
        # Top-3 by surprise desc, feature name asc for deterministic tie-breaks.
        top = sorted(surprises, key=lambda t: (-t[0], t[1]))[:3]
        contribs = [{'feature': feat, 'value': val, 'population_share': share}
                    for _s, feat, val, share in top]
        return total, contribs
