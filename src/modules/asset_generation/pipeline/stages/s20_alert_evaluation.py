# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage 12 — alert evaluation and dashboard refresh."""

import logging
from datetime import datetime, timezone

from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


class AlertEvaluationStage(PipelineStage):

    # Reads the committed asset set and writes only alerts — never atomic with
    # the asset write. Runs after the core commit, outside the scope (#813).
    post_commit = True

    def __init__(self, service):
        self.service = service

    def _get_alerts_service(self):
        """Reuse the app-scoped alerts service when available.

        `FieldCatalog` carries a TTL cache; constructing one per pipeline run
        triples the per-rule query cost. In Flask-hosted runs the singleton
        built in `factory.py` has the catalog primed — use it. For runs
        outside the app context (CLI, tests), fall back to a local build.
        """
        try:
            from flask import current_app, has_app_context
            if has_app_context():
                svc = getattr(current_app, 'alerts_service', None)
                if svc is not None:
                    return svc
        except RuntimeError:
            pass

        from src.modules.alerts.repository import AlertsRepository
        from src.modules.alerts.service import AlertsService
        from src.modules.alert_delivery.wiring import build_alert_delivery_service
        from src.modules.shared.asset_query_compiler import AssetQueryCompiler
        from src.modules.shared.field_resolver_factory import build_field_resolver

        # DB-direct resolver (no app context): build_field_resolver reads the
        # integration-group mapping straight from the DB. The cached provider
        # would read current_app, silently return {}, erase every
        # integration_group entry, and break alert rules that filter on a
        # group:<group>:<attr> field. See src/modules/shared/field_resolver_factory.py.
        compiler = AssetQueryCompiler(resolver=build_field_resolver(self.service.db))
        alerts_repo = AlertsRepository(self.service.db, query_compiler=compiler)
        return AlertsService(
            alerts_repo,
            delivery_service=build_alert_delivery_service(self.service.db))

    @staticmethod
    def _refresh_field_catalog(alerts_service) -> None:
        """Drop the alerts compiler's cached FieldCatalog snapshot before evaluating.

        The pipeline TRUNCATEs + rebuilds `assets` every run. The catalog
        discovers `ext:<source>:<key>` / `group:<group>:<attr>` fields by scanning
        `assets.extended_attributes` and caches that under a TTL. In a Flask-hosted
        run the alerts compiler reuses the app-scoped catalog, which may still hold
        a snapshot taken mid-rebuild (assets empty) — so an `ext:`/`group:`
        alert-rule field would fail to resolve and `AssetQueryCompiler` would
        SILENTLY DROP that filter clause, broadening (or, if it was the rule's only
        clause, emptying → matching every asset) the match set and firing wrong
        alert create/update/resolve transitions. This stage runs post-commit, so
        re-scanning now reflects the rebuilt + committed assets. Mirrors the #789
        enrichment fix (`EnrichmentService.apply_all_active`); see #790.

        `_get_alerts_service` always wires a compiler + resolver + catalog (both the
        app-scoped and the locally-built path), so this chain resolves. If it ever
        breaks, `_execute`'s outer handler surfaces it (logged, non-fatal) — rather
        than a swallowed error silently reverting to the stale-catalog bug this
        guards against, which is exactly why there is no bare `except` here (the
        #789 mirror invalidates without one too).
        """
        alerts_service.repo._compiler._resolver.catalog().invalidate()

    def _get_anomaly_service(self):
        """Reuse the app-scoped anomaly service when available, else build a
        DB-direct one (CLI/tests) — mirrors `_get_alerts_service`."""
        try:
            from flask import current_app, has_app_context
            if has_app_context():
                svc = getattr(current_app, 'anomaly_service', None)
                if svc is not None:
                    return svc
        except RuntimeError:
            pass

        from src.modules.alerts.repository import AlertsRepository
        from src.modules.alert_delivery.wiring import build_alert_delivery_service
        from src.modules.anomaly_detection.service import AnomalyDetectionService
        return AnomalyDetectionService(
            self.service.db, AlertsRepository(self.service.db),
            delivery_service=build_alert_delivery_service(self.service.db))

    @staticmethod
    def _current_batches(ctx: PipelineContext):
        """Build A3's per-source view: batch-level unique_assets from the
        pipeline's per_source stats (not the grace-masked assets table), keyed by
        the integration name s06 captured, MERGED with the real per-batch
        success/error the generation service recorded in
        ctx.stats['current_batches'] (absent on non-scheduled paths -> assume
        success). A source that produced no batch this run is simply absent ->
        A3's missing-batch routing; a batch that loaded but failed carries
        success=False -> A3's failed-batch routing."""
        outcomes = {o.get('integration_name'): o
                    for o in (ctx.stats.get('current_batches') or [])
                    if o.get('integration_name')}
        out, seen = [], set()
        for batch_id, ps in (ctx.stats.get('per_source') or {}).items():
            name = ps.get('integration_name') or batch_id
            seen.add(name)
            outcome = outcomes.get(name, {})
            out.append({
                'integration_name': name,
                'unique_assets': ps.get('unique_assets', 0),
                'success': outcome.get('success', True),
                'error': outcome.get('error'),
            })
        # Failed/empty batches contribute no per_source rows — surface them so the
        # failed routing is reachable (they would otherwise look 'missing').
        for name, outcome in outcomes.items():
            if name not in seen and outcome.get('success') is False:
                out.append({'integration_name': name, 'unique_assets': 0,
                            'success': False, 'error': outcome.get('error')})
        return out

    @property
    def name(self) -> str:
        return "Alert Evaluation"

    @property
    def key(self) -> str:
        return "alert_evaluation"

    @property
    def stage_label(self) -> str:
        return "Alert Evaluation"

    def _execute(self, ctx: PipelineContext) -> None:
        try:
            alerts_service = self._get_alerts_service()
            # Refresh the catalog so `ext:`/`group:` rule fields resolve against
            # the freshly rebuilt assets instead of a stale mid-rebuild snapshot
            # (otherwise the clause is silently dropped — see _refresh_field_catalog).
            self._refresh_field_catalog(alerts_service)
            alert_stats = alerts_service.evaluate_all_rules(pipeline_run_id=ctx.pipeline_run_id)
            ctx.stats['stages'][self.key] = {**alert_stats}
        except Exception as e:
            logger.error(
                "  Alert evaluation failed (non-fatal): %s",
                e, exc_info=True,
            )
            ctx.stats['stages'][self.key] = {'error': str(e)}

        # Anomaly detection — history-relative detectors over this run (non-fatal,
        # isolated from the asset write exactly like alert evaluation above).
        try:
            anomaly_service = self._get_anomaly_service()
            # The run's ACTUAL start (set at pipeline creation), not now(): detectors
            # use it as the boundary for "this run's" onsets (missing_since) and
            # created assets (first_seen), and for weekday seasonality — a stage-end
            # now() would sit after those writes and silently zero A2 / pollute C1.
            run_started_at = datetime.fromtimestamp(ctx.pipeline_wall_start, tz=timezone.utc)
            summary = anomaly_service.evaluate_run(
                generation_run_id=str(ctx.generation_run_id),
                run_started_at=run_started_at,
                current_stats=ctx.stats,
                current_batches=self._current_batches(ctx),
                pipeline_run_id=ctx.pipeline_run_id,
            )
            # Store the summary before the histogram so a histogram failure can't
            # discard a run that already created/updated alerts.
            ctx.stats['stages']['anomaly_detection'] = summary
            try:
                summary['confidence_histogram'] = anomaly_service.fleet_confidence_histogram()
            except Exception as e:
                logger.warning("  Confidence histogram failed (non-fatal): %s", e)
        except Exception as e:
            logger.error("  Anomaly evaluation failed (non-fatal): %s", e, exc_info=True)
            ctx.stats['stages']['anomaly_detection'] = {'error': str(e)}

        # Refresh dashboard materialized views
        try:
            from src.modules.dashboard.repositories.dashboard_repository import (
                DashboardRepository,
            )

            dashboard_repo = DashboardRepository(self.service.db)
            refresh_result = dashboard_repo.refresh_materialized_views()
            refreshed = refresh_result.get('refreshed_views', [])
            failed = refresh_result.get('failed_views', {})
            if refreshed:
                logger.info(
                    "  Dashboard views refreshed: %d ok, %d failed",
                    len(refreshed), len(failed),
                )
            if failed:
                logger.warning(
                    "  Dashboard view refresh failures: %s", failed,
                )
        except Exception as e:
            logger.warning(
                "  Dashboard view refresh failed (non-fatal): %s", e,
            )
