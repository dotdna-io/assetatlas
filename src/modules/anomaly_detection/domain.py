# SPDX-License-Identifier: AGPL-3.0-or-later
"""Core types + pure statistics for anomaly detection. No I/O here."""
from dataclasses import dataclass, field
from statistics import median as _median
from typing import Any

# Detectors emit this where a subject asset's label goes in title/description/
# narrative; the service's label pass (AnomalyDetectionService._label_findings)
# substitutes it with the asset's resolved label — never a raw UUID.
ASSET_LABEL_TOKEN = "{{asset_label}}"


@dataclass
class RunEvalContext:
    """Everything a detector needs for one run, so detectors stay pure and are
    trivially unit-testable with a mock repo. Fields are added as later detector
    families need them (current_batches: A3/A4; profiles: C2)."""
    generation_run_id: str
    run_started_at: Any            # datetime
    current_stats: dict            # orchestrator ctx.stats (gen stats, incl. ['stages'])
    rule: dict                     # the alert_rules row
    params: dict                   # rule['threshold'] merged over detector defaults
    settings: Any                  # AnomalySettings
    repo: Any                      # AnomalyRepository
    current_batches: list = field(default_factory=list)  # [{integration_name, unique_assets, success, error}]
    profiles: Any = None           # ProfileStore (C2 durable value memory)


@dataclass
class AnomalyFinding:
    detector_key: str
    dimension_key: str            # '' for run-scoped detectors
    kind: str                     # 'event' | 'condition'
    severity: str                 # 'critical' | 'high' | 'medium' | 'low'
    title: str
    description: str
    evidence: dict
    alert_type: str = 'operational'
    asset_ids: list = field(default_factory=list)        # sample, <= 25
    data_sources: list = field(default_factory=list)
    assets_by_category: dict = field(default_factory=dict)
    subject_type: str = 'asset'          # 'asset' = per-asset grid; 'metric' = count/rate, summary-only
    sample_asset_ids: list = field(default_factory=list)  # optional labeled evidence sample, <= 25


@dataclass
class RobustResult:
    median: float
    mad: float
    z: float
    n: int


def robust_z(observed: float, samples: list, mad_floor_ratio: float = 0.05) -> RobustResult:
    """z = (observed - median) / max(MAD, floor). Median/MAD so one past
    incident can't poison the baseline; the floor keeps ultra-quiet series
    (MAD 0) from dividing by zero."""
    vals = [float(v) for v in samples if v is not None]
    if not vals:
        return RobustResult(0.0, 0.0, 0.0, 0)
    med = _median(vals)
    mad = _median([abs(v - med) for v in vals])
    floor = max(abs(med) * mad_floor_ratio, 1.0)
    denom = max(mad, floor)
    return RobustResult(med, mad, (float(observed) - med) / denom, len(vals))


def severity_from_z(z: float, params: dict, tolerance_factor: float = 1.0) -> str | None:
    z_med = params.get('z_medium', 3) * tolerance_factor
    z_high = params.get('z_high', 5) * tolerance_factor
    z_crit = params.get('z_critical', 8) * tolerance_factor
    if z >= z_crit:
        return 'critical'
    if z >= z_high:
        return 'high'
    if z >= z_med:
        return 'medium'
    return None
