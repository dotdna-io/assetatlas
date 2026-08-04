# SPDX-License-Identifier: AGPL-3.0-or-later
"""The alert expressions we actually ship in monitoring/alerts/.

test_prometheus_sync.py drives the parser with synthetic fixtures; nothing
asserted anything about the real rule files, so a rule that fired permanently on
every install shipped unnoticed.

The defect these tests pin: a metric can be *exported* by more than one job while
only one job ever *sets* it. `assetatlas_scheduler_last_heartbeat_seconds` is
defined in src/api/utils/metrics.py — which the api imports, so the api exports
it at the Gauge default of 0 — but only src/scheduler_worker.py calls .set() on
it. Unscoped, `time() - 0` is ~1.8e9, so `> 600` was true for the api series from
the first scrape and the critical scheduler-stalled alert never cleared.
"""
import glob
import os
import re

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
ALERTS_DIR = os.path.join(REPO_ROOT, "monitoring", "alerts")

# Metrics exported by several jobs but authoritative for exactly one. Any rule
# reading one of these has to name the job it trusts.
SINGLE_WRITER_METRICS = {
    "assetatlas_scheduler_last_heartbeat_seconds": "assetatlas-scheduler",
}


def _shipped_alert_rules():
    """(file, alertname, expr) for every alert rule we ship."""
    out = []
    for path in sorted(glob.glob(os.path.join(ALERTS_DIR, "*.yml"))):
        with open(path) as f:
            data = yaml.safe_load(f)
        for group in data.get("groups") or []:
            for rule in group.get("rules") or []:
                if rule.get("alert"):
                    out.append((os.path.basename(path), rule["alert"], rule.get("expr", "")))
    return out


def test_shipped_rules_are_discoverable():
    """Collection pin.

    Every assertion below iterates the shipped rules. If the directory moves or
    the glob stops matching, those tests would pass by iterating nothing.
    """
    rules = _shipped_alert_rules()
    assert len(rules) >= 10, f"expected the shipped rule set, found {len(rules)}"


def test_single_writer_metrics_carry_the_writing_job():
    """The regression: an unscoped read of a metric only one job sets.

    Without the job selector this alert is true from the first scrape on every
    install that runs the monitoring overlay, and stays true forever.
    """
    offenders = []
    for fname, alertname, expr in _shipped_alert_rules():
        for metric, job in SINGLE_WRITER_METRICS.items():
            # A bare reference is the metric name NOT immediately followed by `{`.
            for match in re.finditer(re.escape(metric) + r"\s*(\{[^}]*\})?", expr):
                selector = match.group(1)
                if selector is None or f'job="{job}"' not in selector:
                    offenders.append(f"{fname}:{alertname} reads {metric} without job=\"{job}\"")
    assert not offenders, "\n".join(offenders)


def test_scheduler_stalled_rule_still_exists():
    """Guards the test above from passing because the rule was renamed away."""
    names = {alertname for _, alertname, _ in _shipped_alert_rules()}
    assert "system-scheduler-stalled" in names


@pytest.mark.parametrize("metric,job", sorted(SINGLE_WRITER_METRICS.items()))
def test_dashboard_tiles_scope_the_same_metrics(metric, job):
    """The MCP/chat metrics tiles carry copies of these expressions.

    Fixing only the alert leaves the "Scheduler heartbeat age" tile rendering a
    second series whose age reads as roughly 56 years.
    """
    tools_path = os.path.join(REPO_ROOT, "src", "mcp", "tools", "metrics_tool.py")
    with open(tools_path) as f:
        source = f.read()

    assert metric in source, f"{metric} no longer referenced in metrics_tool.py — update this test"
    for match in re.finditer(re.escape(metric) + r"\s*(\{[^}]*\})?", source):
        selector = match.group(1)
        # The comment above the tile names the metric too; skip prose by
        # requiring the match to sit inside a query string.
        line_start = source.rfind("\n", 0, match.start()) + 1
        line = source[line_start:source.find("\n", match.start())]
        if line.lstrip().startswith("#"):
            continue
        assert selector and f'job="{job}"' in selector, \
            f"metrics_tool.py reads {metric} without job=\"{job}\": {line.strip()}"
