# SPDX-License-Identifier: AGPL-3.0-or-later
from src.modules.anomaly_detection.domain import robust_z, severity_from_z

BANDS = {'z_medium': 3, 'z_high': 5, 'z_critical': 8}


def test_robust_z_stable_series():
    r = robust_z(210, [200, 210, 205, 215, 208, 212, 209, 211])
    assert abs(r.z) < 1 and r.n == 8


def test_robust_z_spike():
    r = robust_z(1403, [190, 205, 210, 215, 208, 212, 209, 211])
    assert r.z > 8


def test_robust_z_mad_floor_prevents_blowup():
    # identical samples => MAD 0 => floor kicks in, z finite
    r = robust_z(30, [10] * 10)
    assert r.z > 0 and r.z != float('inf')


def test_robust_z_empty_samples():
    r = robust_z(5, [])
    assert r.n == 0 and r.z == 0.0


def test_severity_bands():
    assert severity_from_z(2.9, BANDS) is None
    assert severity_from_z(3.5, BANDS) == 'medium'
    assert severity_from_z(6.0, BANDS) == 'high'
    assert severity_from_z(9.0, BANDS) == 'critical'


def test_severity_tolerance_widening():
    # fallback mode widens bands x1.5: z=4 no longer medium at 3*1.5=4.5
    assert severity_from_z(4.0, BANDS, tolerance_factor=1.5) is None
    assert severity_from_z(5.0, BANDS, tolerance_factor=1.5) == 'medium'
