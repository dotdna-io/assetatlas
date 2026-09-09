# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Pipeline Orchestrator — composes pipeline stages into a full run.

This replaces the monolithic generate_assets() method by running
extracted PipelineStage classes sequentially over a shared PipelineContext.

The legacy AssetGenerationService is preserved unchanged.
"""

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from uuid import uuid4

import pandas as pd

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.asset_generation.services.asset_generation_service import (
    AssetGenerationService,
)
from src.modules.asset_generation.pipeline.context import PipelineContext
from src.modules.asset_generation.pipeline.metrics import (
    log_pipeline_summary,
    detect_slow_stages,
)
from src.modules.pipeline.stage_duration_recorder import record_stage_durations
from src.api.utils.metrics import (
    track_asset_generation_from_stats,
    track_stage_durations_from_stats,
)

# Import all stages
from src.modules.asset_generation.pipeline.stages.s00_cleanup_expired_mappings import CleanupExpiredMappingsStage
from src.modules.asset_generation.pipeline.stages.s01_preprocessing import PreprocessingStage
from src.modules.asset_generation.pipeline.stages.s02_asset_field_generation import AssetFieldGenerationStage
from src.modules.asset_generation.pipeline.stages.s03_load_mappings import LoadMappingsStage
from src.modules.asset_generation.pipeline.stages.s04_asset_id_assignment import AssetIdAssignmentStage
from src.modules.asset_generation.pipeline.stages.s05_conflict_detection import ConflictDetectionStage
from src.modules.asset_generation.pipeline.stages.s06_excluded_records import ExcludedRecordsStage
from src.modules.asset_generation.pipeline.stages.s07_expand_rows import ExpandRowsStage
from src.modules.asset_generation.pipeline.stages.s08_data_enrichment import DataEnrichmentStage
from src.modules.asset_generation.pipeline.stages.s09_category_prediction import CategoryPredictionStage
from src.modules.asset_generation.pipeline.stages.s10_os_selection import OSSelectionStage
from src.modules.asset_generation.pipeline.stages.s11_provider_preparation import ProviderPreparationStage
from src.modules.asset_generation.pipeline.stages.s12_aggregation import AggregationStage
from src.modules.asset_generation.pipeline.stages.s13_category_selection import CategorySelectionStage
from src.modules.asset_generation.pipeline.stages.s14_scoring import ScoringStage
from src.modules.asset_generation.pipeline.stages.s14b_ip_classification import IpClassificationStage
from src.modules.asset_generation.pipeline.stages.s14c_ip_geolocation import IpGeolocationStage
from src.modules.asset_generation.pipeline.stages.s15_os_eol_enrichment import OsEolEnrichmentStage
from src.modules.asset_generation.pipeline.stages.s15b_label_overrides import AssetLabelOverridesStage
from src.modules.asset_generation.pipeline.stages.s15c_grace_carry_forward import GraceCarryForwardStage
from src.modules.asset_generation.pipeline.stages.s16_persistence import PersistenceStage
from src.modules.asset_generation.pipeline.stages.s16b_materialize_unified_values import (
    MaterializeUnifiedValuesEnrichmentStage,
    MaterializeUnifiedValuesStage,
)
from src.modules.asset_generation.pipeline.stages.s17_tag_rules import TagRulesStage
from src.modules.asset_generation.pipeline.stages.s17b_evaluate_posture_rules import PostureRulesEvaluationStage
from src.modules.asset_generation.pipeline.stages.s18_change_tracking import ChangeTrackingStage
from src.modules.asset_generation.pipeline.stages.s20_alert_evaluation import AlertEvaluationStage
from src.modules.asset_generation.pipeline.stages.s21_csv_enrichment import CsvEnrichmentStage
from src.modules.asset_generation.pipeline.stages.s22_embedding_update import EmbeddingUpdateStage

logger = logging.getLogger(__name__)
pipeline_timer = logging.getLogger("pipeline.timing")


def _build_pipeline_field_resolver(db_adapter):
    """Pick the FieldResolver the pipeline's enrichment stage (s21) should use.

    Prefer the app-scoped resolver when running inside a Flask app context — its
    FieldCatalog TTL cache is already primed, so a pipeline run doesn't repeat
    the catalog's discovery queries (mirrors s20_alert_evaluation). Otherwise —
    the scheduler/CLI case, which runs with NO app context — build a local
    resolver whose catalog reads its integration-group mapping straight from the
    DB.

    The group-mapping provider MUST be DB-direct in the no-context path: the
    cached provider (``get_cached_group_mapping``) reads ``current_app`` and
    silently returns ``{}`` when there is no app context, which erases every
    integration_group entry from the catalog. A ``group:<group>:<attr>``
    enrichment match_field would then fail to resolve and the dataset would be
    silently skipped on every scheduled pipeline run — while the API "Apply"
    path (which has an app context) keeps working.
    """
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            resolver = getattr(current_app, "field_resolver", None)
            if resolver is not None:
                return resolver
    except RuntimeError:
        pass

    from src.modules.shared.field_resolver_factory import build_field_resolver
    return build_field_resolver(db_adapter)


def _resolve_embedding_auto_update() -> bool:
    """Whether the s22 embedding stage runs this pipeline.

    Prefer the Flask app config when running inside an app context; fall back to
    the ``EMBEDDING_AUTO_UPDATE`` env var for scheduler/CLI runs (no app
    context). Mirrors ``config.py``'s parsing. Gives operators a runtime escape
    hatch when the description-generation LLM is unhealthy, instead of having to
    stop the LLM container (#813).
    """
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            return bool(current_app.config.get('EMBEDDING_AUTO_UPDATE', True))
    except RuntimeError:
        pass
    return os.getenv('EMBEDDING_AUTO_UPDATE', 'true').lower() == 'true'


def _resolve_llm_descriptions_enabled(db_adapter) -> bool:
    """Whether s22 auto-generates LLM field descriptions/synonyms this run.

    Reads ``embedding.llm_descriptions_enabled``. Prefers the app settings
    manager inside an app context; falls back to a DB-direct read for the
    scheduler/CLI (no app context). Defaults on (opt-out); with no usable
    chat provider the generator no-ops, so nothing is spent until a provider
    is actually configured.
    """
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            sm = getattr(current_app, 'settings_manager', None)
            if sm is not None:
                return bool(sm.get('embedding', 'llm_descriptions_enabled', True))
    except RuntimeError:
        pass
    try:
        rows = db_adapter.execute_query(
            "SELECT value FROM system_settings "
            "WHERE category = 'embedding' AND key = 'llm_descriptions_enabled'"
        ) or []
        if rows:
            return str(rows[0].get('value')).lower() in ('true', '1', 'yes', 'on')
    except Exception:
        pass
    return True


class PipelineOrchestrator:
    """
    Composes pipeline stages and runs them sequentially.

    Wraps the legacy AssetGenerationService — delegates to its domain objects
    and repositories via stage classes that hold a service reference.
    """

    def __init__(
        self,
        db_adapter: PostgreSQLAdapter,
        posture_evaluation_service=None,
        **service_kwargs,
    ):
        # Create the legacy service (holds all domain objects + repos)
        self.service = AssetGenerationService(db_adapter=db_adapter, **service_kwargs)
        self.db = db_adapter
        self.posture_evaluation_service = posture_evaluation_service

        # Inject enrichment service for s21 (CSV enrichment)
        try:
            from src.modules.enrichment.repositories.enrichment_repository import EnrichmentRepository
            from src.modules.enrichment.services.enrichment_service import EnrichmentService

            field_resolver = _build_pipeline_field_resolver(db_adapter)
            enrichment_repo = EnrichmentRepository(db_adapter)
            self.service.enrichment_service = EnrichmentService(
                enrichment_repo, db_adapter, field_resolver=field_resolver
            )
        except Exception as e:
            logger.warning("Enrichment service not available for pipeline: %s", e)
            self.service.enrichment_service = None

        # Embedding auto-update flag — honour config/env so operators can skip
        # the LLM-backed s22 stage at runtime (#813).
        self.service._embedding_auto_update = _resolve_embedding_auto_update()

        # Opt-in gate for LLM description/synonym generation in s22 (default off).
        self.service._llm_descriptions_enabled = _resolve_llm_descriptions_enabled(db_adapter)

        # Build the ordered pipeline, then split by each stage's `post_commit`
        # flag. `core_stages` form ONE atomic asset transaction; the non-critical
        # tail (`post_commit_stages`, e.g. alert eval + embedding) runs AFTER
        # that commits and outside the scope, so a caught error or a slow LLM in
        # them can neither roll back the committed run nor hold the post-TRUNCATE
        # ACCESS EXCLUSIVE lock (#813). Classification lives on the stage
        # (PipelineStage.post_commit), not a hardcoded list here — so a new
        # non-critical stage can't be silently pulled back into the transaction.
        s = self.service
        ordered_stages = [
            CleanupExpiredMappingsStage(s),
            PreprocessingStage(s),
            AssetFieldGenerationStage(s),
            LoadMappingsStage(s),
            AssetIdAssignmentStage(s),
            ExcludedRecordsStage(s),
            ExpandRowsStage(s),
            DataEnrichmentStage(s),
            CategoryPredictionStage(s),
            OSSelectionStage(s),
            ProviderPreparationStage(s),
            ConflictDetectionStage(s),
            AggregationStage(s),
            CategorySelectionStage(s),
            IpClassificationStage(s),   # IP classification (pre-scoring)
            IpGeolocationStage(s),       # IP geolocation enrichment
            ScoringStage(s),
            OsEolEnrichmentStage(s),
            AssetLabelOverridesStage(s),
            GraceCarryForwardStage(s),
            PersistenceStage(s),
            # Materialize unified values before tag rules (s17) / posture (s17b)
            # consume them; core stage, so a failed refresh aborts the run.
            MaterializeUnifiedValuesStage(s, scope="all"),
            TagRulesStage(s),
        ]
        if self.posture_evaluation_service is not None:
            ordered_stages.append(PostureRulesEvaluationStage(self.posture_evaluation_service))
        # CSV enrichment must run before chronicle: persistence wipes
        # extended_attributes back to the freshly-computed pipeline value, so
        # without re-applying enrichment first, chronicle sees the previous
        # run's enrichment sub-dict drop and fires a spurious update for every
        # asset that had enrichment applied.
        ordered_stages += [
            CsvEnrichmentStage(s),
            # Second pass: fields with an ext:enrichment: source must see the
            # post-enrichment rows s21 just wrote.
            MaterializeUnifiedValuesEnrichmentStage(s),
            ChangeTrackingStage(s),
            AlertEvaluationStage(s),
            EmbeddingUpdateStage(s),
        ]
        self.core_stages = [st for st in ordered_stages if not st.post_commit]
        self.post_commit_stages = [st for st in ordered_stages if st.post_commit]

    def generate_assets(
        self,
        raw_data_df: pd.DataFrame,
        mac_vendor_df: Optional[pd.DataFrame] = None,
        ipam_df: Optional[pd.DataFrame] = None,
        track_changes: bool = False,
        on_stage_change=None,
        pipeline_run_id: Optional[Any] = None,
        batch_outcomes: Optional[list] = None,
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """
        Execute the full pipeline via composed stages.

        ``batch_outcomes`` (optional) carries the caller's real per-batch
        success/error so s20's anomaly A3 routing can flag failed batches; other
        callers omit it. Otherwise same signature as
        AssetGenerationService.generate_assets().
        """
        pipeline_start = time.time()

        # Validate input
        self.service._validate_input(raw_data_df)

        # Create run ID
        generation_run_id = uuid4()
        # Defensive collision check (same as legacy)
        try:
            existing = self.db.execute_query(
                "SELECT 1 FROM pipeline_runs WHERE id = %s::uuid",
                (str(generation_run_id),), fetch=True
            )
            if existing:
                logger.warning(f"Run ID collision detected: {generation_run_id}, regenerating")
                generation_run_id = uuid4()
        except Exception:
            pass

        # Open geo MMDB reader (optional — skips gracefully if missing)
        geo_reader = None
        try:
            import maxminddb  # deferred: allows pipeline to run without maxminddb installed
            mmdb_path = Path(__file__).resolve().parents[4] / "data" / "dbip-city-lite.mmdb"
            geo_reader = maxminddb.open_database(str(mmdb_path))
            logger.info(f"Loaded geolocation MMDB: {mmdb_path}")
        except FileNotFoundError:
            logger.info("No geolocation MMDB found — IP geolocation will be skipped")
        except Exception as e:
            logger.warning(f"Failed to open geolocation MMDB: {e}")

        # Build context
        ctx = PipelineContext(
            raw_data_df=raw_data_df,
            mac_vendor_df=mac_vendor_df,
            ipam_df=ipam_df,
            geo_reader=geo_reader,
            track_changes=track_changes,
            on_stage_change=on_stage_change,
            generation_run_id=generation_run_id,
            pipeline_run_id=pipeline_run_id,
            pipeline_start=pipeline_start,
            pipeline_wall_start=time.time(),
        )
        ctx.stats['generation_run_id'] = str(generation_run_id)
        ctx.stats['pipeline_start_time'] = datetime.now(timezone.utc)
        ctx.stats['input_records'] = len(raw_data_df)
        # Real per-batch outcomes for s20's A3 failed-batch routing (optional).
        if batch_outcomes is not None:
            ctx.stats['current_batches'] = batch_outcomes

        logger.info("=" * 80)
        logger.info("Starting Asset Generation Pipeline (v2 — staged)")
        logger.info(f"Generation Run ID: {generation_run_id}")
        logger.info("=" * 80)
        pipeline_timer.warning(
            "PIPELINE START | run=%s | input_records=%d",
            generation_run_id, len(raw_data_df),
        )

        # The core stages run inside ONE connection scope (the atomic asset
        # write); the non-critical tail runs AFTER that commits (see below).
        # Concurrent-run protection is enforced by pipeline_service.trigger_run()
        # via an atomic INSERT … WHERE NOT EXISTS into pipeline_runs, plus
        # pipeline_watchdog recovery for orphans. No advisory lock here — those
        # are session-scoped and leak across process restarts when psycopg2
        # keeps the underlying connection in its pool.
        manage_scope = not self.db.conn_manager.is_scope_active()
        if manage_scope:
            self.db.conn_manager.begin_scope()

        try:
            # --- Phase 1: core stages = the atomic asset transaction. ---
            core_error = None
            try:
                for stage in self.core_stages:
                    stage.execute(ctx)
            except Exception as e:
                core_error = e
                raise
            finally:
                # Commit (or roll back on failure) the asset transaction NOW —
                # before the non-critical tail — but only when WE own the scope.
                # When a caller owns it (no real pipeline path today) all stages
                # stay in that caller's transaction (the pre-#813 behavior).
                # raise_on_aborted: if a core stage swallowed a DB error and left
                # the txn aborted, surface it (fail the run) rather than a silent
                # commit-as-rollback.
                if manage_scope:
                    self.db.conn_manager.end_scope(
                        error=core_error, raise_on_aborted=True
                    )

            # --- Core committed: the run has SUCCEEDED. ---
            # Everything below is best-effort and must NEVER flip the run to
            # 'failed' — a 'failed' status sitting over a fully-committed dataset
            # invites a destructive TRUNCATE rerun (#813 review).
            ctx.stats['success'] = True

            # Phase 2: non-critical post-persistence stages. Run AFTER the core
            # commit and OUTSIDE the scope (their writes auto-commit). A failure
            # or a slow LLM here can neither roll back the committed run nor hold
            # the post-TRUNCATE ACCESS EXCLUSIVE lock (#813). Each stage already
            # catches its own errors; this guard is belt-and-suspenders so an
            # unexpected raise still can't fail an already-committed run.
            for stage in self.post_commit_stages:
                try:
                    stage.execute(ctx)
                except Exception as e:
                    stage_key = getattr(stage, 'key', stage.__class__.__name__)
                    logger.error(
                        "Post-commit stage '%s' failed (non-fatal): %s",
                        stage_key, e, exc_info=True,
                    )
                    # Merge, don't clobber — the stage may have already recorded
                    # partial telemetry under its key.
                    ctx.stats.setdefault('stages', {}).setdefault(
                        stage_key, {}
                    )['error'] = str(e)

            # Pipeline summary + stage-duration telemetry — best-effort. The
            # assets are already committed, so a failure here (e.g. the
            # record_stage_durations INSERT, or a KeyError in the summary) must
            # not mark the run failed.
            try:
                ctx.stats['pipeline_duration'] = time.time() - pipeline_start
                ctx.stats['pipeline_end_time'] = datetime.now(timezone.utc)
                ctx.stats['total_duration_seconds'] = round(ctx.stats['pipeline_duration'], 2)
                ctx.stats['final_assets'] = len(ctx.scored_df) if ctx.scored_df is not None else 0

                detect_slow_stages(ctx.stats)
                log_pipeline_summary(ctx.stats)
                # The phase view queries pipeline_stage_durations by
                # pipeline_runs.id (the only ID the UI knows). Persist under
                # that ID when the caller supplied one; fall back to the
                # orchestrator's internal generation_run_id otherwise so
                # standalone scripts still work.
                record_stage_durations(
                    self.db,
                    pipeline_run_id if pipeline_run_id is not None else generation_run_id,
                    ctx.stats['pipeline_start_time'],
                    ctx.stats,
                )

                logger.info("\n" + "=" * 80)
                logger.info("Asset Generation Pipeline Completed Successfully")
                logger.info(f"Total Duration: {ctx.stats['pipeline_duration']:.2f}s")
                logger.info(f"Input Records: {ctx.stats['input_records']}")
                logger.info(f"Final Assets: {ctx.stats['final_assets']}")
                logger.info("=" * 80)
            except Exception as e:
                logger.error(
                    "Pipeline summary/telemetry failed after a successful commit "
                    "(non-fatal; assets are persisted): %s", e, exc_info=True,
                )

            return ctx.scored_df, ctx.stats

        except Exception as e:
            ctx.stats['success'] = False
            ctx.stats['error'] = str(e)
            ctx.stats['pipeline_duration'] = time.time() - pipeline_start
            logger.error(
                f"Asset generation pipeline failed at stage "
                f"'{ctx.stats.get('last_stage', 'unknown')}': {e}",
                exc_info=True,
            )
            raise
        finally:
            # Close geo reader if opened. The connection scope, when owned, is
            # already finalized in Phase 1's inner finally.
            if geo_reader is not None:
                try:
                    geo_reader.close()
                except Exception:
                    pass
            # Prometheus: one asset-generation metric per run (success OR
            # failure), derived from the stats already computed above. Runs
            # last and best-effort — a metrics error here must never replace
            # the run's return value or its propagating exception (#922).
            try:
                track_asset_generation_from_stats(ctx.stats)
                track_stage_durations_from_stats(ctx.stats)
            except Exception:
                logger.debug(
                    "asset-generation metric emit failed (non-fatal)",
                    exc_info=True,
                )
