# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validation tests for the anomaly-threshold patch schema (#878 Gap 1)."""
import pytest
from pydantic import ValidationError

from src.modules.alerts.schemas import AnomalyThreshold


def test_partial_single_param_is_valid():
    t = AnomalyThreshold(pct_medium=0.5)
    # exclude_unset is how the endpoint extracts the patch — only the sent key present.
    assert t.model_dump(exclude_unset=True) == {'pct_medium': 0.5}


def test_multiple_params_valid():
    t = AnomalyThreshold(pct_medium=0.5, min_fit_rows=500, z_high=5)
    assert t.model_dump(exclude_unset=True) == {
        'pct_medium': 0.5, 'min_fit_rows': 500, 'z_high': 5}


def test_empty_is_valid_noop():
    assert AnomalyThreshold().model_dump(exclude_unset=True) == {}


def test_removed_mode_field_rejected():
    # `mode` was removed (#902); extra='forbid' now rejects it as an unknown field.
    with pytest.raises(ValidationError):
        AnomalyThreshold(mode='active')


def test_unknown_param_rejected():
    with pytest.raises(ValidationError):
        AnomalyThreshold(pct_lowest=0.1)  # typo / unknown → extra='forbid'


def test_negative_zscore_rejected():
    with pytest.raises(ValidationError):
        AnomalyThreshold(z_medium=-1)
