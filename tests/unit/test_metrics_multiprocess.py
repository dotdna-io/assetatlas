# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prometheus multiprocess wiring (#1128).

The api runs four gunicorn workers. With plain single-process prometheus_client
each keeps its OWN counters and Prometheus scrapes whichever worker answers, so
the scraped series jumps between four values. Prometheus reads every decrease as
a counter reset and adds the new value, so rate() inflates and never decays —
which is how system-api-error-rate latched on after any 5xx burst and could only
be cleared by restarting the api.

Measured on a clean 2026.31.7 install before the fix, from one un-restarted
container:

    43 53 61 43 61 53 61 61 41 41 61 61 53 43 43 53 43 53 61 53 53
    decreases: 7 of 20      resets(...[15m]) = 14

Those four values (41/43/53/61) summed to exactly the 198 requests sent.

These tests pin the parts that are easy to break silently and that no unit test
would otherwise touch: a metrics change that looks fine locally (single process,
always correct) only misbehaves under gunicorn in production.
"""

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
METRICS = PROJECT_ROOT / "src" / "api" / "utils" / "metrics.py"
GUNICORN_CONF = PROJECT_ROOT / "gunicorn.conf.py"
DOCKERFILE = PROJECT_ROOT / "Dockerfile"
COMPOSE_FILES = (
    PROJECT_ROOT / "deploy" / "compose" / "docker-compose.yml",
    PROJECT_ROOT / "docker-compose.yml",
)

# Gauges whose values are genuinely per-worker, so the fleet number is the sum
# over live workers. Anything left at prometheus_client's default ('all')
# exports one series PER PID, which turns a single number into four labelled
# ones and breaks every dashboard and alert reading it.
_LIVESUM_GAUGES = {
    "assetatlas_db_connection_pool_size",
    "assetatlas_db_connection_pool_available",
    "assetatlas_http_request_in_flight",
}


def _gauge_calls():
    """Every Gauge(...) constructed in metrics.py, as (metric_name, kwargs)."""
    tree = ast.parse(METRICS.read_text(), filename=str(METRICS))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id == "Gauge"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        kwargs = {k.arg: getattr(k.value, "value", None) for k in node.keywords}
        out.append((node.args[0].value, kwargs))
    return out


def test_there_are_gauges_to_check():
    """Collection pin — the assertions below are vacuous over an empty list."""
    assert _gauge_calls(), "no Gauge() constructions found in metrics.py"


@pytest.mark.parametrize("name", sorted(_LIVESUM_GAUGES))
def test_per_worker_gauges_sum_over_live_workers(name):
    found = dict(_gauge_calls())
    assert name in found, f"{name} is no longer defined in metrics.py"
    assert found[name].get("multiprocess_mode") == "livesum", (
        f"{name} holds per-worker state, so without multiprocess_mode='livesum' "
        f"prometheus_client defaults to 'all' and exports one series per worker "
        f"pid instead of one fleet number"
    )


def test_every_gauge_declares_a_multiprocess_mode():
    """The default is the wrong answer for all of them, so none may rely on it."""
    missing = [n for n, kw in _gauge_calls() if "multiprocess_mode" not in kw]
    assert not missing, (
        f"gauges with no explicit multiprocess_mode: {missing}. Under gunicorn "
        f"these export one series per worker pid. Pick 'livesum' for per-worker "
        f"state or 'max' for a global fact only one worker observes."
    )


@pytest.mark.parametrize(
    "compose",
    COMPOSE_FILES,
    ids=lambda p: str(p.relative_to(PROJECT_ROOT)),
)
def test_compose_requires_nothing_the_published_image_may_lack(compose):
    """No compose file may override the api's gunicorn command.

    compose and the image do not arrive together. An online install applies
    the bundle's compose but pulls `:latest`. Every piece of the multiprocess
    setup therefore lives in the image; requiring any of it from compose kills
    the api outright against an older image. Both failure modes were observed
    in the E2E install job:

      * `-c /app/gunicorn.conf.py` in compose -> gunicorn aborts,
        "'/app/gunicorn.conf.py' doesn't exist"
      * PROMETHEUS_MULTIPROC_DIR in compose -> prometheus_client switches to
        mmap mode (it tests for PRESENCE, not truthiness) while no code in the
        old image creates the directory, and every worker dies with
        FileNotFoundError on histogram_<pid>.db

    The root dev/build compose is held to the same rule even though it builds
    the image locally: its `command:` override carried no `-c`, silently
    shadowing the image CMD and switching multiprocess metrics off (#1416).
    Keeping the image CMD the single source of the api's gunicorn args is
    what makes drift like that impossible.
    """
    import yaml
    api = yaml.safe_load(compose.read_text())["services"]["api"]

    assert "command" not in api, (
        f"the api has a command override again ({api.get('command')!r}); it "
        f"shadows the image CMD that carries -c, and if it names "
        f"gunicorn.conf.py an older image fails to boot at all"
    )
    assert "PROMETHEUS_MULTIPROC_DIR" not in (api.get("environment") or {}), (
        "compose sets PROMETHEUS_MULTIPROC_DIR again; against an image whose "
        "gunicorn config does not create that directory, every worker dies "
        "with FileNotFoundError and the api never starts"
    )


def test_the_env_var_is_scoped_to_the_gunicorn_config():
    """Set in gunicorn.conf.py so ONLY the api gets multiprocess mode.

    A Dockerfile ENV would hit the scheduler and mcp too — same image, three
    names — and the single-process scheduler would serve an empty registry,
    emptying assetatlas_scheduler_last_heartbeat_seconds and with it the
    system-scheduler-stalled rule.

    It works because gunicorn executes the config in the master before the WSGI
    app is imported, and workers inherit the environment across the fork — so
    it is set before metrics.py constructs its metric objects, which is when
    prometheus_client decides whether to use mmap files.
    """
    conf = GUNICORN_CONF.read_text()
    assert "PROMETHEUS_MULTIPROC_DIR" in conf and "setdefault" in conf, (
        "gunicorn.conf.py no longer sets PROMETHEUS_MULTIPROC_DIR, so nothing "
        "does and the api silently reverts to per-worker counters"
    )
    assert "makedirs" in conf, (
        "gunicorn.conf.py must create the directory; prometheus_client writes "
        "a file per metric at construction and a missing dir is fatal"
    )
    for line in DOCKERFILE.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("ENV") and "PROMETHEUS_MULTIPROC_DIR" in stripped:
            raise AssertionError(
                f"PROMETHEUS_MULTIPROC_DIR set in the shared image "
                f"({stripped}); the scheduler and mcp share it and must stay "
                f"single-process"
            )


def test_image_cmd_loads_the_hooks_and_keeps_access_logging():
    """The image CMD is now the only place the api's gunicorn args live."""
    text = DOCKERFILE.read_text()
    cmd = text[text.rindex("CMD ["):]
    for needed in ("-c", "/app/gunicorn.conf.py", "--access-logfile",
                   "--error-logfile", "src.api.factory:create_app()"):
        assert needed in cmd, (
            f"image CMD lost {needed!r}; compose no longer supplies it"
        )


def test_gunicorn_hooks_clear_stale_files_and_retire_dead_workers():
    src = GUNICORN_CONF.read_text()
    tree = ast.parse(src, filename=str(GUNICORN_CONF))
    hooks = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "on_starting" in hooks, (
        "no on_starting hook: mmap files are keyed by pid and outlive the "
        "process, so a restart reusing low pids resurrects dead counters"
    )
    assert "child_exit" in hooks, (
        "no child_exit hook: livesum gauges count only live processes, so a "
        "worker that dies leaves its in-flight count on the books forever"
    )
    assert "mark_process_dead" in src


def test_extra_collectors_survive_the_multiprocess_scrape():
    """The subtle half of #1128, and the one that fails silently.

    prometheus_flask_exporter's multiprocess classes discard the registry they
    are handed: generate_metrics() builds a fresh CollectorRegistry per scrape
    containing only the MultiProcessCollector. Every collector that is not
    mmap-backed is therefore dropped with no error — which for us means the
    scrape-time DatabaseGaugeCollector (assets total, active alerts by
    severity) and the app info series. Verified live: they were missing.

    So we must serve /metrics ourselves and re-attach them per scrape.
    """
    text = METRICS.read_text()
    assert "_install_multiprocess_metrics_route" in text, (
        "no owned /metrics route; the library's multiprocess route drops every "
        "collector that is not mmap-backed"
    )
    assert "MultiProcessCollector" in text, "scrape no longer aggregates workers"
    assert "_extra_collectors" in text, (
        "nothing re-attaches the scrape-time collectors to the per-scrape "
        "registry"
    )
    # The library classes are the trap, not the fix.
    for banned in ("GunicornInternalPrometheusMetrics",
                   "MultiprocessPrometheusMetrics"):
        assert banned not in text, (
            f"{banned} is back; it rebuilds the registry per scrape and would "
            f"silently drop assetatlas_assets_total / active_alerts / app info"
        )


def test_db_gauges_go_through_the_extra_collector_path():
    text = METRICS.read_text()
    assert "_register_extra_collector(_db_gauge_collector)" in text, (
        "register_db_gauges bypasses the extra-collector path, so under "
        "multiprocess the assets/alerts series disappear from /metrics"
    )
