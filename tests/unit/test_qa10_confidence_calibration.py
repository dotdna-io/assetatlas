# SPDX-License-Identifier: AGPL-3.0-or-later
"""QA-10: Test confidence calibration curve.

The multiplicative scoring formula compresses realistic best-case scores to
0.70-0.80. Users interpret 0.65 as "low confidence" when it actually
represents a well-corroborated asset. Calibration stretches the compressed
raw distribution into a more intuitive 0-1 range.
"""
import pytest
import sys
sys.path.insert(0, '.')

from src.modules.asset_generation.domain.asset_scorer import AssetScorer


class TestDefaultCalibrationEnabled:
    """Calibration is enabled by default."""

    def test_default_scorer_has_calibration(self):
        scorer = AssetScorer()
        assert scorer.calibration_curve is not None
        assert scorer.calibration_curve == AssetScorer.DEFAULT_CALIBRATION_CURVE

    def test_disabled_scorer_has_no_calibration(self):
        scorer = AssetScorer(enable_calibration=False)
        assert scorer.calibration_curve is None

    def test_custom_curve_overrides_default(self):
        custom = {0.0: 0.0, 0.5: 0.5, 1.0: 1.0}
        scorer = AssetScorer(calibration_curve=custom)
        assert scorer.calibration_curve == custom


class TestCalibrationStretching:
    """Calibration should stretch compressed scores into a wider range."""

    @pytest.fixture
    def scorer(self):
        return AssetScorer()

    def test_low_raw_maps_lower(self, scorer):
        """Raw 0.3 (poor) should map to 0.30."""
        assert scorer._apply_calibration(0.3) == pytest.approx(0.30, abs=0.01)

    def test_mid_raw_maps_higher(self, scorer):
        """Raw 0.5 (average) should map to 0.60."""
        assert scorer._apply_calibration(0.5) == pytest.approx(0.60, abs=0.01)

    def test_good_raw_maps_much_higher(self, scorer):
        """Raw 0.7 (good in old scale) should map to 0.82."""
        assert scorer._apply_calibration(0.7) == pytest.approx(0.82, abs=0.01)

    def test_excellent_raw_maps_to_90(self, scorer):
        """Raw 0.8 (excellent) should map to 0.90."""
        assert scorer._apply_calibration(0.8) == pytest.approx(0.90, abs=0.01)

    def test_perfect_stays_perfect(self, scorer):
        """Raw 1.0 stays 1.0."""
        assert scorer._apply_calibration(1.0) == pytest.approx(1.0, abs=0.01)

    def test_zero_stays_near_zero(self, scorer):
        """Raw 0.0 maps to 0.05 (not zero — at least some data exists)."""
        assert scorer._apply_calibration(0.0) == pytest.approx(0.05, abs=0.01)


class TestCalibrationMonotonicity:
    """Calibration must be monotonically increasing."""

    def test_monotonic(self):
        scorer = AssetScorer()
        inputs = [i / 20.0 for i in range(21)]  # 0.0, 0.05, ..., 1.0
        outputs = [scorer._apply_calibration(x) for x in inputs]
        for i in range(len(outputs) - 1):
            assert outputs[i] <= outputs[i + 1], (
                f"Non-monotonic at {inputs[i]:.2f}: "
                f"{outputs[i]:.4f} > {outputs[i+1]:.4f}"
            )


class TestCalibrationInterpolation:
    """Values between curve points are linearly interpolated."""

    def test_continuous_interpolation_between_buckets(self):
        scorer = AssetScorer()
        # 0.55 interpolates between 0.5 (→0.60) and 0.6 (→0.72) → 0.66
        assert scorer._apply_calibration(0.55) == pytest.approx(0.66, abs=0.01)
        # 0.75 interpolates between 0.7 (→0.82) and 0.8 (→0.90) → 0.86
        assert scorer._apply_calibration(0.75) == pytest.approx(0.86, abs=0.01)

    def test_interpolation_between_sparse_buckets(self):
        """With a sparse custom curve, interpolation fills gaps."""
        scorer = AssetScorer(calibration_curve={0.0: 0.0, 1.0: 1.0})
        # 0.5 is not an exact bucket → interpolate between 0.0 and 1.0
        result = scorer._apply_calibration(0.5)
        assert result == pytest.approx(0.5, abs=0.01)


class TestRawConfidenceInBreakdown:
    """Breakdown should include raw_confidence for transparency."""

    def test_breakdown_has_raw_confidence(self):
        import pandas as pd
        from datetime import datetime, timezone

        scorer = AssetScorer()
        assets_df = pd.DataFrame([{
            'id': 'a1',
            'sn': ['SN123'],
            'hostname': ['host1'],
            'data_source': ['qualys'],
            'data_source_category': ['vulnerability management'],
            'last_seen': datetime.now(timezone.utc),
        }])
        providers_df = pd.DataFrame([{'id': 'a1', 'sn': 'SN123'}])

        result = scorer.calculate_confidence(assets_df, providers_df)
        breakdown = result['confidence_breakdown'].iloc[0]

        assert 'raw_confidence' in breakdown
        assert 'calibrated' in breakdown
        assert breakdown['calibrated'] is True
        # Calibrated confidence should be >= raw (curve stretches upward)
        assert result['confidence'].iloc[0] >= breakdown['raw_confidence']
