# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Prometheus Metrics Configuration

Provides comprehensive metrics collection for monitoring API performance,
business operations, and system health.

Metrics Categories:
- HTTP metrics: Request duration, status codes, error rates (automatic)
- Business metrics: Integrations, batches, assets, tag rules
- Cache metrics: Hit/miss rates, operation timing
- Database metrics: Connection pool usage, query performance

Usage:
    from src.api.utils.metrics import init_metrics, track_integration_execution

    app = Flask(__name__)
    init_metrics(app)

    # Track custom business metric
    track_integration_execution(integration_id, duration_seconds, success=True)
"""

import logging
import os
from flask import Flask, g, request
from prometheus_flask_exporter import PrometheusMetrics
from prometheus_client import (Counter, CollectorRegistry, Histogram, Gauge,
                               Info, REGISTRY)
from prometheus_client.core import GaugeMetricFamily
from psycopg2.errors import ObjectNotInPrerequisiteState, UndefinedTable

logger = logging.getLogger(__name__)

# Global metrics instance (initialized in init_metrics)
metrics = None

# Collectors that answer from shared state (a DB query, a constant) rather than
# per-process counters, so any single worker can serve them correctly. They are
# NOT mmap-backed, which means the multiprocess scrape would otherwise drop
# them; _install_multiprocess_metrics_route re-attaches them to each scrape.
_extra_collectors = []


def _register_extra_collector(collector) -> None:
    """Make `collector` visible on /metrics under either mode."""
    if multiprocess_enabled():
        if collector not in _extra_collectors:
            _extra_collectors.append(collector)
    else:
        REGISTRY.register(collector)


def _install_multiprocess_metrics_route(app: Flask) -> None:
    """Serve /metrics as MultiProcessCollector + the extra collectors.

    prometheus_flask_exporter's own multiprocess route cannot be used here: it
    discards the registry it is given and rebuilds one per scrape containing
    only the MultiProcessCollector, which drops every collector that is not
    mmap-backed.
    """
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    from prometheus_client import multiprocess as pc_multiprocess
    from flask import Response

    @app.route('/metrics')
    def _prometheus_metrics():
        registry = CollectorRegistry()
        # Aggregates every worker's mmap files — counters and histograms summed,
        # gauges folded per their multiprocess_mode.
        pc_multiprocess.MultiProcessCollector(registry)
        for collector in _extra_collectors:
            try:
                registry.register(collector)
            except Exception:
                logger.debug('extra collector registration failed',
                             exc_info=True)
        return Response(generate_latest(registry),
                        mimetype=CONTENT_TYPE_LATEST)


class _AppInfoCollector:
    """Static build metadata, as a collector rather than an Info metric.

    prometheus_client cannot represent Info in multiprocess mode, so the
    `assetatlas_application_info` series simply stopped being exported — by
    omission, with no error. The values are identical in every worker, so a
    plain collector reproduces it exactly.
    """

    def __init__(self, labels):
        self._labels = dict(labels)

    def collect(self):
        from prometheus_client.core import InfoMetricFamily
        yield InfoMetricFamily('assetatlas_application',
                               'AssetAtlas Backend API Information',
                               value=self._labels)


def multiprocess_enabled() -> bool:
    """Whether prometheus_client is in multiprocess mode.

    The env var is the switch prometheus_client itself reads, and it must be
    set before this module is imported: the metric objects below decide at
    construction time whether to back themselves with mmap files. Setting it
    later has no effect, which is why it belongs in the image rather than in
    application code.
    """
    return bool(os.environ.get('PROMETHEUS_MULTIPROC_DIR'))

# =============================================================================
# Business Metrics - Custom metrics for AssetAtlas operations
# =============================================================================

# Integration execution metrics
integration_executions = Counter(
    'assetatlas_integration_executions_total',
    'Total number of integration executions',
    ['integration_id', 'integration_name', 'connector_type', 'status']
)

integration_duration = Histogram(
    'assetatlas_integration_duration_seconds',
    'Integration execution duration in seconds',
    ['integration_id', 'integration_name', 'connector_type'],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600]  # 1s to 1h
)

integration_records_processed = Counter(
    'assetatlas_integration_records_processed_total',
    'Total number of records processed by integrations',
    ['integration_id', 'integration_name', 'connector_type']
)

# Asset generation metrics
asset_generation_runs = Counter(
    'assetatlas_asset_generation_runs_total',
    'Total number of asset generation runs',
    ['status']
)

asset_generation_duration = Histogram(
    'assetatlas_asset_generation_duration_seconds',
    'Asset generation duration in seconds',
    buckets=[5, 10, 30, 60, 120, 300, 600, 1800]  # 5s to 30min
)

assets_created = Counter(
    'assetatlas_assets_created_total',
    'Total number of assets created',
    ['source']
)

assets_updated = Counter(
    'assetatlas_assets_updated_total',
    'Total number of assets updated',
    ['source']
)

pipeline_stage_duration = Histogram(
    'assetatlas_pipeline_stage_duration_seconds',
    'Per-stage asset-generation pipeline duration in seconds',
    ['stage'],
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300]
)

# Tag rule metrics
tag_rule_executions = Counter(
    'assetatlas_tag_rule_executions_total',
    'Total number of tag rule executions',
    ['rule_id', 'rule_name', 'rule_type', 'status']
)

tag_assignments = Counter(
    'assetatlas_tag_assignments_total',
    'Total number of tag assignments',
    ['tag_name', 'operation']  # operation: 'added' or 'removed'
)

# Cache metrics
cache_operations = Counter(
    'assetatlas_cache_operations_total',
    'Total number of cache operations',
    ['operation', 'result']  # operation: 'get', 'set', 'delete' | result: 'hit', 'miss', 'success', 'error'
)

cache_operation_duration = Histogram(
    'assetatlas_cache_operation_duration_seconds',
    'Cache operation duration in seconds',
    ['operation'],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)

# Database metrics
# multiprocess_mode is load-bearing under gunicorn (see init_metrics). Without
# it prometheus_client defaults to 'all', which exports one series PER WORKER
# PID — turning a single number into four labelled ones and breaking every
# dashboard and alert that reads them. 'livesum' is the honest aggregate here:
# each worker owns its own pool, so the fleet's capacity is the sum over the
# workers currently alive. The kwarg is accepted and ignored in single-process
# mode, so the scheduler and teams-bridge are unaffected.
db_connection_pool_size = Gauge(
    'assetatlas_db_connection_pool_size',
    'Current size of database connection pool',
    multiprocess_mode='livesum'
)

db_connection_pool_available = Gauge(
    'assetatlas_db_connection_pool_available',
    'Number of available connections in pool',
    multiprocess_mode='livesum'
)

db_query_duration = Histogram(
    'assetatlas_db_query_duration_seconds',
    'Database query duration in seconds',
    ['query_type'],  # query_type: 'select', 'insert', 'update', 'delete'
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

# HTTP in-flight requests. prometheus_flask_exporter emits per-request counters
# and a duration histogram but no concurrency gauge, so track it ourselves via
# request hooks (see init_metrics). Powers the System dashboard's "Concurrent
# in-flight" card.
http_request_in_flight = Gauge(
    'assetatlas_http_request_in_flight',
    'Number of HTTP requests currently being processed',
    multiprocess_mode='livesum'
)

# Chat / LLM metrics
chat_turns_total = Counter(
    'assetatlas_chat_turns_total',
    'Total chat turns completed, labelled by outcome',
    ['provider', 'status']  # status: done, turn_timeout, tool_iterations_exceeded, tool_errors_exceeded, llm_error, stream_failed, session_not_found
)

chat_turn_duration_seconds = Histogram(
    'assetatlas_chat_turn_duration_seconds',
    'End-to-end chat turn duration in seconds',
    ['provider'],
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 45, 60, 120]
)

chat_tokens_input_total = Counter(
    'assetatlas_chat_tokens_input_total',
    'Input tokens consumed by chat turns',
    ['provider', 'model']
)

chat_tokens_output_total = Counter(
    'assetatlas_chat_tokens_output_total',
    'Output tokens produced by chat turns',
    ['provider', 'model']
)

chat_tool_calls_total = Counter(
    'assetatlas_chat_tool_calls_total',
    'Total chat tool invocations',
    ['tool_name', 'status']  # status: ok, error, denied, timeout
)

chat_tool_call_duration_seconds = Histogram(
    'assetatlas_chat_tool_call_duration_seconds',
    'Chat tool invocation duration in seconds',
    ['tool_name'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20]
)

chat_rate_limit_hits_total = Counter(
    'assetatlas_chat_rate_limit_hits_total',
    'Chat rate-limit / throttling hits',
    ['limit_type']  # daily, concurrent, turn_timeout
)

chat_feedback_total = Counter(
    'assetatlas_chat_feedback_total',
    'Chat feedback submissions',
    ['rating']  # up, down
)


# Scheduler metrics
# Owned by the scheduler process (single-instance, so not multiprocess); the
# api only ever exports the untouched default. 'max' keeps that 0 from dragging
# a real value down should anything ever set it here too.
scheduled_jobs_total = Gauge(
    'assetatlas_scheduled_jobs_total',
    'Total number of scheduled jobs',
    multiprocess_mode='max'
)

scheduled_job_executions = Counter(
    'assetatlas_scheduled_job_executions_total',
    'Total number of scheduled job executions',
    ['job_id', 'status']
)

scheduled_job_duration = Histogram(
    'assetatlas_scheduled_job_duration_seconds',
    'Scheduled job execution duration in seconds',
    ['job_id'],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600]
)

# Same ownership as above. The api exporting this at 0 is exactly what made
# system-scheduler-stalled fire permanently (#1114, fixed by scoping the rule to
# job="assetatlas-scheduler"); 'max' additionally stops the four api workers
# from disagreeing about it.
scheduler_last_heartbeat = Gauge(
    'assetatlas_scheduler_last_heartbeat_seconds',
    'Unix timestamp of the last scheduler worker heartbeat (seconds since epoch)',
    multiprocess_mode='max'
)

# Reference data update metrics
# A row count and an apply timestamp are global facts about the database, not
# per-worker state: whichever worker last applied a bundle holds the truth, and
# the others hold a stale 0. 'max' picks the real one.
reference_data_records_total = Gauge(
    'assetatlas_reference_data_records_total',
    'Number of rows currently in a reference dataset',
    ['dataset'],
    multiprocess_mode='max',
)

reference_data_last_updated_timestamp = Gauge(
    'assetatlas_reference_data_last_updated_timestamp',
    'Unix timestamp of the last successful reference-data apply for the dataset',
    ['dataset'],
    multiprocess_mode='max',
)


# =============================================================================
# Initialization
# =============================================================================

def init_metrics(app: Flask) -> PrometheusMetrics:
    """
    Initialize Prometheus metrics for the Flask application.

    This function:
    1. Creates PrometheusMetrics instance (adds HTTP metrics automatically)
    2. Exposes /metrics endpoint
    3. Tracks HTTP request duration, status codes, error rates
    4. Adds application info metric
    5. Excludes health check endpoints from detailed metrics

    Args:
        app: Flask application instance

    Returns:
        PrometheusMetrics instance

    Example:
        app = Flask(__name__)
        metrics = init_metrics(app)
    """
    global metrics

    # Create PrometheusMetrics instance
    # This automatically adds:
    # - flask_http_request_duration_seconds (histogram)
    # - flask_http_request_total (counter)
    # - flask_http_request_exceptions_total (counter)
    common = dict(
        defaults_prefix='assetatlas',
        group_by='endpoint',  # Group metrics by Flask endpoint name
        export_defaults=True,
        excluded_paths=['/health', '/metrics']  # Don't track health/metrics in HTTP metrics
    )

    if multiprocess_enabled():
        # gunicorn runs 4 workers, each with its own counters, and Prometheus
        # scrapes whichever one answers — so the scraped series jumped between
        # four values and Prometheus read every decrease as a counter reset.
        # rate() then inflated and never decayed, which is why
        # system-api-error-rate latched on after any 5xx burst and only a
        # restart cleared it (#1128). Measured before the fix: one counter
        # cycling 43/53/61/41 across scrapes, those four summing to exactly the
        # request count, with resets(...[15m]) = 14 on a container that had not
        # restarted.
        #
        # Multiprocess mode moves the values into mmap files under
        # PROMETHEUS_MULTIPROC_DIR that every worker writes, and the scrape
        # aggregates them, so /metrics reports the fleet no matter who answers.
        #
        # We serve /metrics ourselves (path=None disables the library's route)
        # instead of using prometheus_flask_exporter's multiprocess class.
        # That class ignores the registry you hand it: its generate_metrics()
        # builds a brand-new CollectorRegistry per scrape holding ONLY the
        # MultiProcessCollector. Anything that is not mmap-backed — the
        # scrape-time DatabaseGaugeCollector, the app Info — is therefore
        # dropped, silently and with no error. Verified live before writing
        # this. Owning the endpoint is what keeps those series.
        metrics = PrometheusMetrics(app, path=None, **common)
        _install_multiprocess_metrics_route(app)
    else:
        # Dev server, tests, and any single-process consumer: the default
        # in-memory registry is already correct and needs no mmap directory.
        metrics = PrometheusMetrics(app, **common)

    # In-flight request tracking. Bracket each dispatched request with an
    # inc/dec on http_request_in_flight. A `g` flag guards the balance: if an
    # earlier before_request short-circuits (skipping ours), teardown must not
    # decrement a count we never incremented. Health/metrics are excluded so
    # scrapes and probes don't inflate the gauge.
    _in_flight_excluded = ('/health', '/metrics')

    @app.before_request
    def _in_flight_inc():
        if request.path not in _in_flight_excluded:
            http_request_in_flight.inc()
            g._in_flight_counted = True

    @app.teardown_request
    def _in_flight_dec(exc=None):
        if g.pop('_in_flight_counted', False):
            http_request_in_flight.dec()

    # Add application info. Info has no multiprocess representation, so under
    # gunicorn it is emitted by a collector instead (identical values in every
    # worker, so whichever answers the scrape is right). Keeping it rather than
    # accepting the loss: a series vanishing by omission is exactly what nobody
    # notices until a dashboard is quietly empty.
    _info_labels = {
        'version': app.config.get('APP_VERSION', '1.0.0'),
        'environment': app.config.get('FLASK_ENV', 'development'),
        'name': app.config.get('APP_NAME', 'AssetAtlas Backend API')
    }
    if multiprocess_enabled():
        _register_extra_collector(_AppInfoCollector(_info_labels))
    else:
        try:
            Info('assetatlas_application',
                 'AssetAtlas Backend API Information').info(_info_labels)
        except ValueError:
            # Repeated app creation in tests/reloads; the series already exists.
            pass

    logger.info("Prometheus metrics initialized at /metrics endpoint")

    return metrics


# =============================================================================
# Helper Functions - Track Business Metrics
# =============================================================================

def track_integration_execution(
    integration_id: int,
    integration_name: str,
    connector_type: str,
    duration_seconds: float,
    status: str,
    records_processed: int = 0
) -> None:
    """
    Track integration execution metrics.

    Args:
        integration_id: Integration database ID
        integration_name: Integration name
        connector_type: Connector type (e.g., 'splunk', 'sentinel_one')
        duration_seconds: Execution duration in seconds
        status: Execution status ('success', 'failed', 'partial')
        records_processed: Number of records processed

    Example:
        track_integration_execution(
            integration_id=1,
            integration_name='Splunk Production',
            connector_type='splunk',
            duration_seconds=45.2,
            status='success',
            records_processed=1500
        )
    """
    integration_executions.labels(
        integration_id=str(integration_id),
        integration_name=integration_name,
        connector_type=connector_type,
        status=status
    ).inc()

    integration_duration.labels(
        integration_id=str(integration_id),
        integration_name=integration_name,
        connector_type=connector_type
    ).observe(duration_seconds)

    if records_processed > 0:
        integration_records_processed.labels(
            integration_id=str(integration_id),
            integration_name=integration_name,
            connector_type=connector_type
        ).inc(records_processed)


def track_asset_generation(duration_seconds: float, status: str, created: int = 0, updated: int = 0, source: str = 'integration') -> None:
    """
    Track asset generation metrics.

    Args:
        duration_seconds: Generation duration in seconds
        status: Generation status ('success', 'failed', 'partial')
        created: Number of assets created
        updated: Number of assets updated
        source: Asset source ('integration', 'manual', 'import')

    Example:
        track_asset_generation(
            duration_seconds=120.5,
            status='success',
            created=50,
            updated=200,
            source='integration'
        )
    """
    asset_generation_runs.labels(status=status).inc()
    asset_generation_duration.observe(duration_seconds)

    if created > 0:
        assets_created.labels(source=source).inc(created)
    if updated > 0:
        assets_updated.labels(source=source).inc(updated)


def track_asset_generation_from_stats(stats: dict) -> None:
    """Emit asset-generation metrics from the orchestrator's ``ctx.stats``.

    Adapts the pipeline's stats dict (see
    ``PipelineOrchestrator.generate_assets``) to :func:`track_asset_generation`.
    Kept here — beside the metric definitions and away from the heavy pipeline
    import graph — so it stays unit-testable without importing the full stage
    stack. Tolerant of missing keys (a run that didn't track changes reports 0
    created/updated); the caller is still responsible for best-effort wrapping.
    """
    duration = float(stats.get('pipeline_duration') or 0.0)
    status = 'success' if stats.get('success') else 'failed'
    created = int(stats.get('new_assets') or 0)
    updated = int(stats.get('updated_assets') or 0)
    track_asset_generation(duration, status, created, updated)


def track_stage_durations_from_stats(stats: dict) -> None:
    """Emit per-stage pipeline durations from the orchestrator's ``ctx.stats``.

    Mirrors the per-stage timings the orchestrator persists to
    ``pipeline_stage_durations`` (see ``record_stage_durations``) into a
    Prometheus histogram, so the Pipeline dashboard's "Stage timing breakdown"
    panel has data. Stages without a numeric ``duration`` (skipped or
    pre-error) are ignored. Best-effort: the caller wraps this so a metrics
    error never affects the run.
    """
    stages = stats.get('stages') or {}
    for stage_key, sdata in stages.items():
        if not isinstance(sdata, dict):
            continue
        duration = sdata.get('duration')
        if duration is None:
            continue
        # Convert before touching .labels() so a bad value doesn't create an
        # empty label child (which would pollute the series with a 0-count stage).
        try:
            seconds = float(duration)
        except (TypeError, ValueError):
            continue
        pipeline_stage_duration.labels(stage=str(stage_key)).observe(seconds)


def track_tag_rule_execution(rule_id: str, rule_name: str, rule_type: str, status: str, assignments_added: int = 0, assignments_removed: int = 0) -> None:
    """
    Track tag rule execution metrics.

    Args:
        rule_id: Rule ID
        rule_name: Rule name
        rule_type: Rule type ('field_match', 'regex', 'complex')
        status: Execution status ('success', 'failed')
        assignments_added: Number of tag assignments added
        assignments_removed: Number of tag assignments removed

    Example:
        track_tag_rule_execution(
            rule_id='rule_123',
            rule_name='Windows Servers',
            rule_type='field_match',
            status='success',
            assignments_added=10,
            assignments_removed=2
        )
    """
    tag_rule_executions.labels(
        rule_id=rule_id,
        rule_name=rule_name,
        rule_type=rule_type,
        status=status
    ).inc()

    if assignments_added > 0:
        tag_assignments.labels(tag_name=rule_name, operation='added').inc(assignments_added)
    if assignments_removed > 0:
        tag_assignments.labels(tag_name=rule_name, operation='removed').inc(assignments_removed)


def track_cache_operation(operation: str, result: str, duration_seconds: float = None) -> None:
    """
    Track cache operation metrics.

    Args:
        operation: Cache operation ('get', 'set', 'delete')
        result: Operation result ('hit', 'miss', 'success', 'error')
        duration_seconds: Optional operation duration in seconds

    Example:
        track_cache_operation('get', 'hit', 0.002)
        track_cache_operation('set', 'success', 0.005)
    """
    cache_operations.labels(operation=operation, result=result).inc()

    if duration_seconds is not None:
        cache_operation_duration.labels(operation=operation).observe(duration_seconds)


def update_db_pool_metrics(pool_size: int, available: int) -> None:
    """
    Update database connection pool metrics.

    Args:
        pool_size: Current pool size
        available: Number of available connections

    Example:
        update_db_pool_metrics(pool_size=20, available=15)
    """
    db_connection_pool_size.set(pool_size)
    db_connection_pool_available.set(available)


def track_db_query(query_type: str, duration_seconds: float) -> None:
    """
    Track database query metrics.

    Args:
        query_type: Query type ('select', 'insert', 'update', 'delete')
        duration_seconds: Query duration in seconds

    Example:
        track_db_query('select', 0.125)
    """
    db_query_duration.labels(query_type=query_type).observe(duration_seconds)


def track_scheduled_job(job_id: str, duration_seconds: float, status: str) -> None:
    """
    Track scheduled job execution metrics.

    Args:
        job_id: Job identifier
        duration_seconds: Job execution duration in seconds
        status: Job status ('success', 'failed')

    Example:
        track_scheduled_job('integration_1', 45.2, 'success')
    """
    scheduled_job_executions.labels(job_id=job_id, status=status).inc()
    scheduled_job_duration.labels(job_id=job_id).observe(duration_seconds)


def track_chat_turn(
    provider: str,
    model: str,
    status: str,
    duration_seconds: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """Record one completed chat turn plus any token usage.

    Called once per turn from ``ChatService.stream_turn`` regardless of outcome.
    ``status`` maps to the terminal StreamEvent ``kind``/``code``.
    """
    chat_turns_total.labels(provider=provider, status=status).inc()
    chat_turn_duration_seconds.labels(provider=provider).observe(duration_seconds)
    if input_tokens > 0:
        chat_tokens_input_total.labels(provider=provider, model=model).inc(input_tokens)
    if output_tokens > 0:
        chat_tokens_output_total.labels(provider=provider, model=model).inc(output_tokens)


def track_chat_tool_call(tool_name: str, status: str, duration_seconds: float) -> None:
    """Record one chat tool invocation. Called from ``ToolRegistry.invoke``."""
    chat_tool_calls_total.labels(tool_name=tool_name, status=status).inc()
    chat_tool_call_duration_seconds.labels(tool_name=tool_name).observe(duration_seconds)


def track_chat_rate_limit(limit_type: str) -> None:
    """Record a chat rate-limit / throttling hit."""
    chat_rate_limit_hits_total.labels(limit_type=limit_type).inc()


def track_chat_feedback(rating: str) -> None:
    """Record one chat feedback submission."""
    chat_feedback_total.labels(rating=rating).inc()


def update_scheduled_jobs_total(total: int) -> None:
    """
    Update total number of scheduled jobs.

    Args:
        total: Total number of scheduled jobs

    Example:
        update_scheduled_jobs_total(5)
    """
    scheduled_jobs_total.set(total)


# =============================================================================
# Point-in-time DB gauges (queried on scrape)
# =============================================================================
#
# Active-alerts-by-severity and the total-asset count are point-in-time DB
# values, not event counters, so a custom collector queries them at scrape
# time. This keeps them fresh without a background refresher AND — because the
# collector is only registered in the process that owns a db_adapter (the API,
# via register_db_gauges) — avoids a second process exporting a stale/zero copy
# of the same series (the bundled Prometheus scrapes both API and scheduler).
# Best-effort: a DB error yields no sample for that scrape rather than breaking
# /metrics.
#
# Scrape-safety rules (#1415):
# - Every query opens with SET LOCAL lock_timeout = '2s' IN THE SAME execute
#   string: the pipeline rebuilds `assets` under an ACCESS EXCLUSIVE lock for
#   the whole TRUNCATE+INSERT transaction, and a scrape query that waits past
#   Prometheus' 10s scrape_timeout fails the WHOLE scrape (up=0, api-down
#   alert). A separate SET LOCAL would be rolled back with the request scope's
#   error path; a session-level SET would leak onto the pooled connection
#   (_release_connection does no RESET).
# - assetatlas_assets_total never reads `assets` itself: it serves the count
#   "as of the last pipeline run" from lock-free relations — the dashboard
#   summary matview (refreshed CONCURRENTLY by the pipeline's post-commit
#   stage), falling back to the latest completed run's stats only when the
#   matview is unusable (unpopulated or missing). Do not cache the value in
#   the collector: under gunicorn each worker would cache its own, and the
#   series would flap between divergent values.

_LOCK_TIMEOUT_PREFIX = "SET LOCAL lock_timeout = '2s'; "

_ACTIVE_ALERTS_SQL = _LOCK_TIMEOUT_PREFIX + (
    "SELECT severity, COUNT(*) AS n FROM alerts "
    "WHERE status IN ('active', 'acknowledged') GROUP BY severity"
)

# A populated matview always returns exactly one row here (single
# view_key = 'summary' row, scalar subqueries, no GROUP BY). The failure mode
# that matters is the RAISE: an unpopulated matview — created WITH NO DATA and
# never refreshed, the fresh-install state — errors with SQLSTATE 55000
# (ObjectNotInPrerequisiteState), and a DB from before the matview's migration
# errors 42P01 (UndefinedTable). ONLY those two errors trigger the fallback
# below; a lock/timeout error says nothing about whether the matview is
# populated and must yield no sample for that scrape instead.
_ASSETS_TOTAL_SQL = _LOCK_TIMEOUT_PREFIX + (
    "SELECT total_assets AS n FROM mv_dashboard_summary "
    "WHERE view_key = 'summary'"
)

# Latest run that actually rebuilt `assets`: completed, because a failed run
# rolled back and never changed the table (its stats->>'total_assets', often
# '0', is meaningless as a count); the batches_processed guard drops provable
# no-op completions; data_settled_at orders by when the data settled. The
# count must be recorded: stats is nullable, and a run without one has no
# total to report.
_ASSETS_TOTAL_FALLBACK_SQL = _LOCK_TIMEOUT_PREFIX + (
    "SELECT stats->>'total_assets' AS n FROM pipeline_runs "
    "WHERE data_settled_at IS NOT NULL "
    "AND status = 'completed' "
    "AND stats->>'total_assets' IS NOT NULL "
    "AND NOT (status = 'completed' "
    "AND COALESCE((stats->>'batches_processed')::int, 1) = 0) "
    "ORDER BY data_settled_at DESC LIMIT 1"
)


class DatabaseGaugeCollector:
    """prometheus_client collector for the live DB gauges.

    ``assetatlas_active_alerts{severity}`` — open (active or acknowledged)
    alerts grouped by severity — and ``assetatlas_assets_total`` — the asset
    count as of the last pipeline run. Both are queried on every ``collect()``
    (i.e. per scrape), each bounded by a 2s lock timeout so a scrape never
    waits on the pipeline's TRUNCATE lock (#1415).
    """

    def __init__(self, db_adapter):
        self.db_adapter = db_adapter

    def collect(self):
        active = GaugeMetricFamily(
            'assetatlas_active_alerts',
            'Open (active or acknowledged) alerts, by severity',
            labels=['severity'],
        )
        try:
            rows = self.db_adapter.execute_query(_ACTIVE_ALERTS_SQL) or []
            for row in rows:
                active.add_metric([str(row['severity'])], float(row['n']))
        except Exception:
            logger.debug('active-alerts gauge collection failed', exc_info=True)
        yield active

        assets = GaugeMetricFamily(
            'assetatlas_assets_total',
            'Total number of assets as of the last pipeline run',
        )
        try:
            rows = self.db_adapter.execute_query(_ASSETS_TOTAL_SQL) or []
        except (ObjectNotInPrerequisiteState, UndefinedTable):
            # Unpopulated (55000, fresh install) or missing (42P01) matview —
            # the only two errors that mean "use pipeline_runs instead". The
            # scope path rolled the failed query back, so this second execute
            # runs in a fresh implicit transaction and its own SET LOCAL binds.
            try:
                rows = self.db_adapter.execute_query(
                    _ASSETS_TOTAL_FALLBACK_SQL) or []
            except Exception:
                logger.debug(
                    'assets-total fallback query failed', exc_info=True)
                rows = []
        except Exception:
            # LockNotAvailable, QueryCanceled, connection errors, ...: no
            # sample for this scrape and NO fallback — a lock wait on the
            # matview says nothing about whether it is populated.
            logger.debug('assets-total gauge collection failed', exc_info=True)
            rows = []
        if rows and rows[0].get('n') is not None:
            assets.add_metric([], float(rows[0]['n']))
        yield assets


_db_gauge_collector = None


def register_db_gauges(db_adapter) -> None:
    """Register (once) the live DB gauge collector on the scraped registry.

    Idempotent: repeated app creation (tests, reloads) re-points the existing
    collector at the new db_adapter instead of raising a duplicate-collector
    error. Call once per process that owns a db_adapter — currently the API
    process in create_app(); the scheduler leaves these two series to the API
    endpoint so the bundled Prometheus never scrapes two divergent copies.

    Under multiprocess these cannot live on the default REGISTRY: the scrape
    builds its own registry from the mmap files, so a collector left on the
    default one is never consulted and the series disappear without an error.
    _register_extra_collector re-attaches it to each scrape instead — safe
    precisely because these gauges are scrape-time DB queries, a global fact any
    worker can answer, rather than per-process state needing aggregation.
    """
    global _db_gauge_collector
    if _db_gauge_collector is None:
        _db_gauge_collector = DatabaseGaugeCollector(db_adapter)
        _register_extra_collector(_db_gauge_collector)
    else:
        _db_gauge_collector.db_adapter = db_adapter
