# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pipeline metrics — structured logging only (Prometheus deferred)."""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def log_pipeline_summary(stats: Dict[str, Any]) -> None:
    """Log pipeline summary at WARNING level for production visibility."""
    timer = logging.getLogger("pipeline.timing")

    stage_lines = []
    for sname, sdata in stats.get('stages', {}).items():
        if isinstance(sdata, dict) and 'duration' in sdata:
            stage_lines.append(f"{sname}={sdata['duration']:.2f}s")

    timer.warning(
        "PIPELINE DONE  | run=%s | total=%.2fs | assets=%d | stages: %s",
        stats.get('generation_run_id', '?'),
        stats.get('pipeline_duration', 0),
        stats.get('final_assets', 0),
        ", ".join(stage_lines),
    )


def detect_slow_stages(
    stats: Dict[str, Any],
    slow_ratio: float = 0.30,
    slow_threshold_seconds: float = 120,
) -> None:
    """Flag slow stages and overall slow pipeline in stats."""
    total = stats.get('pipeline_duration', 0)
    if total <= 0:
        return

    slow = []
    for sname, sdata in stats.get('stages', {}).items():
        if isinstance(sdata, dict) and 'duration' in sdata:
            dur = sdata['duration']
            if dur > total * slow_ratio:
                slow.append({'stage': sname, 'duration': round(dur, 2),
                             'pct': round(dur / total * 100)})

    if slow:
        stats['slow_stages'] = slow
        for s in slow:
            logger.warning(
                "Slow stage: %s took %.1fs (%d%% of total)",
                s['stage'], s['duration'], s['pct'],
            )

    if total > slow_threshold_seconds:
        stats['pipeline_slow'] = True
        logger.warning(
            "Pipeline exceeded threshold: %.1fs (threshold: %.0fs)",
            total, slow_threshold_seconds,
        )
