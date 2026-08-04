"""Gunicorn config for the API image.

Exists for one reason: prometheus_client's multiprocess mode (#1128).

Without it the four workers each keep their own counters, Prometheus scrapes
whichever worker answers, and the scraped series jumps between four values.
Prometheus reads every decrease as a counter reset and adds the new value, so
rate() inflates and never decays — which is how system-api-error-rate came to
latch on permanently after any 5xx burst.

WHY THE ENV VAR IS SET HERE and not in the Dockerfile or compose:

* Not compose. compose and the image do not arrive together — an online install
  applies the bundle's compose but pulls `:latest`. Setting it there against an
  older image gives prometheus_client a directory nothing creates, and every
  worker dies with FileNotFoundError on histogram_<pid>.db. The api never
  starts: a total outage, strictly worse than the bug being fixed.

* Not a Dockerfile ENV. api, scheduler and mcp are ONE image under three names.
  An ENV would switch the single-process scheduler into multiprocess mode too,
  where its start_http_server serves the default registry — which holds no mmap
  values — silently emptying assetatlas_scheduler_last_heartbeat_seconds and
  with it the system-scheduler-stalled rule.

* Here it is scoped exactly right. Only the api passes `-c`, gunicorn executes
  this file in the master BEFORE the WSGI app is imported, and workers inherit
  the environment across the fork — so it is set before
  src.api.utils.metrics constructs its metric objects, which is the moment
  prometheus_client decides whether to back them with mmap files. Nothing else
  in the image is affected, and compose needs no change at all.

Server settings stay on the CMD line so `docker run ... gunicorn --workers N`
still overrides them; only the metrics lifecycle lives here.
"""

import os
import shutil

MULTIPROC_DIR = os.environ.setdefault(
    'PROMETHEUS_MULTIPROC_DIR', '/tmp/prometheus_multiproc'
)

# Created at import, not just in on_starting: prometheus_client writes a file
# per metric as soon as the objects are constructed, and a missing directory is
# a hard FileNotFoundError in every worker rather than a degraded metric.
os.makedirs(MULTIPROC_DIR, exist_ok=True)


def on_starting(server):
    """Empty the mmap directory before any worker forks.

    The files are keyed by pid and outlive the process that wrote them. A
    container restart reuses low pids, so a stale file is silently adopted by
    an unrelated new worker and its counter values are resurrected — the
    metrics equivalent of reading a deleted file's contents. Prometheus cannot
    tell that from real traffic.

    Runs in the master before the fork, so no worker can be mid-write.
    """
    shutil.rmtree(MULTIPROC_DIR, ignore_errors=True)
    os.makedirs(MULTIPROC_DIR, exist_ok=True)
    server.log.info("Cleared Prometheus multiprocess dir at %s", MULTIPROC_DIR)


def child_exit(server, worker):
    """Retire a dead worker's gauge samples.

    Gauges declared `livesum` sum over live processes only, so without this a
    worker that dies mid-request leaves its in-flight count on the books
    forever and the gauge drifts upward with every restart. Counters and
    histograms are deliberately left behind — their totals are still true.
    """
    from prometheus_client import multiprocess
    multiprocess.mark_process_dead(worker.pid)
