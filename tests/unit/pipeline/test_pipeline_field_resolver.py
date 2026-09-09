# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression for the scheduled-pipeline enrichment bug.

The scheduler worker runs the pipeline with NO Flask app context, so the
enrichment stage's FieldResolver is built by
``_build_pipeline_field_resolver``'s no-context fallback. That fallback must
read the integration-group mapping from the DB directly — not via
``get_cached_group_mapping``, which reads ``current_app`` and silently returns
``{}`` when there is no app context. An empty mapping erases every
``integration_group`` entry from the catalog, so a ``group:<group>:<attr>``
enrichment match_field fails to resolve and the dataset is silently skipped on
every scheduled pipeline run.

Observed on production 2026.26.x: the 'Legal Entities' dataset
(match_field ``group:SentinelOne:siteId``) matched 21,163 assets on a manual
Apply (API path, has app context) but 0 assets carried it because every
scheduled pipeline run skipped it. PR #789's ``catalog().invalidate()`` could
not help: the rebuild called the same context-dependent provider.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.modules.alerts.repository import AlertsRepository
from src.modules.alerts.service import AlertsService
from src.modules.asset_generation.pipeline.stages.s20_alert_evaluation import (
    AlertEvaluationStage,
)
from src.modules.asset_generation.services.pipeline_orchestrator import (
    _build_pipeline_field_resolver,
)
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.models import IntegrationGroupField
from src.modules.shared.asset_query_compiler import AssetQueryCompiler


class _FakeDB:
    """db_adapter stub routing only the queries a no-app-context resolver build
    runs: the integration-group mapping (repo) and the catalog's discovery scans."""

    def execute_query(self, query, params=None, fetch=True):
        if "integration_groups" in query:      # IntegrationGroupRepository.get_group_mapping
            return [{"integration_name": "Corp SentinelOne", "group_name": "SentinelOne"}]
        if "jsonb_object_keys" in query:        # catalog extended_attribute discovery
            return [{"source_name": "Corp SentinelOne", "field_name": "siteId"}]
        return []                               # tags, posture, unified_fields → empty


def test_no_app_context_resolver_resolves_integration_group_field():
    # Force the scheduler/CLI path: no Flask app context. The locally-built
    # resolver's catalog must still surface integration_group fields, which
    # requires a non-empty group mapping read straight from the DB.
    with patch("flask.has_app_context", return_value=False):
        resolver = _build_pipeline_field_resolver(_FakeDB())

        resolved = resolver.resolve(
            IntegrationGroupField(
                kind="integration_group", group="SentinelOne", attribute="siteId"
            )
        )

    # Resolves to a COALESCE over the grouped member source's extended_attributes
    # instead of raising InvalidFieldError (what the cached-mapping bug caused).
    where = resolved.where_expr()
    assert "extended_attributes" in where
    assert "Corp SentinelOne" in where
    assert "siteId" in where


def test_no_app_context_resolver_wires_the_shared_version_source():
    # The scheduler path's catalog must poll the shared version stamp (#1413)
    # like the app-scoped one; the source degrades to None/no-op without
    # REDIS_URL, so the no-context path works with or without Redis.
    from src.modules.fields import catalog_version
    with patch("flask.has_app_context", return_value=False):
        resolver = _build_pipeline_field_resolver(_FakeDB())
    assert resolver.catalog()._version_source is catalog_version.default_source()


def test_app_context_resolver_is_reused_not_rebuilt():
    # Inside a Flask app context the helper must return the app-scoped resolver
    # as-is (its catalog cache is shared/primed) rather than building a local one.
    sentinel = object()
    with patch("flask.has_app_context", return_value=True), patch(
        "flask.current_app", SimpleNamespace(field_resolver=sentinel)
    ):
        assert _build_pipeline_field_resolver(_FakeDB()) is sentinel


def test_alert_evaluation_no_app_context_resolves_integration_group_field():
    # s20 (AlertEvaluationStage) has the SAME no-app-context fallback as the
    # enrichment stage: its locally-built alerts service must resolve a
    # group:<group>:<attr> field, or group-based alert rules silently break on
    # every scheduled pipeline run. Reverting the s20 provider to
    # get_cached_group_mapping makes resolve() raise InvalidFieldError here.
    stage = AlertEvaluationStage(SimpleNamespace(db=_FakeDB()))
    with patch("flask.has_app_context", return_value=False):
        service = stage._get_alerts_service()

    resolver = service.repo._compiler._resolver
    resolved = resolver.resolve(
        IntegrationGroupField(
            kind="integration_group", group="SentinelOne", attribute="siteId"
        )
    )
    assert "extended_attributes" in resolved.where_expr()
    assert "Corp SentinelOne" in resolved.where_expr()


class TestAlertStageRefreshesCatalog:
    """Regression (#790): the pipeline ``TRUNCATE``s + rebuilds ``assets`` before
    the post-commit stage-20 alert evaluation runs. The FieldCatalog behind the
    alerts compiler derives its ``ext:``/``group:`` entries by scanning
    ``assets.extended_attributes`` and caches that snapshot (TTL); a Flask-hosted
    run reuses the app catalog, which can hold one taken mid-rebuild (assets
    empty). The ``group:``/``ext:`` field then fails to resolve and
    ``AssetQueryCompiler`` SILENTLY DROPS the clause — an empty (or broadened)
    match set that fires wrong alert create/update/resolve transitions, with no
    error surfaced. The stage must invalidate the catalog first so resolution
    reflects the rebuilt assets and the clause is evaluated, not dropped. Mirrors
    the #789 enrichment fix (``TestApplyAllActiveRefreshesCatalog``).
    """

    _GROUP_FIELD = "group:SentinelOne:siteId"

    class _FlippableDB:
        """db_adapter stub whose extended_attributes discovery scan changes
        between the catalog being primed (assets mid-rebuild) and the alert stage
        running (assets rebuilt). Records the rule's compiled asset query so the
        test can assert the ``group:`` clause survived instead of being dropped."""

        def __init__(self):
            self.ext_attr_pairs = []   # (source, attribute) pairs present in assets
            self.rule_queries = []     # compiled SQL for the rule's target filter

        def execute_query(self, query, params=None, fetch=True):
            if "jsonb_object_keys" in query:        # catalog ext-attr discovery
                return [{"source_name": s, "field_name": a}
                        for (s, a) in self.ext_attr_pairs]
            if "asset_tags_v2" in query:            # catalog tag discovery
                return []
            if "posture_categories" in query:       # catalog posture discovery
                return []
            if "os_normalized AS os" in query:      # the rule's build_asset_query
                self.rule_queries.append(query)
                return []                           # no matches → no alert lifecycle
            return []

    def _build_service(self, fake_db):
        catalog = FieldCatalog(
            db_adapter=fake_db,
            group_mapping_provider=lambda: {"Corp SentinelOne": "SentinelOne"},
        )
        compiler = AssetQueryCompiler(resolver=FieldResolver(catalog=catalog))
        repo = AlertsRepository(fake_db, query_compiler=compiler)
        rule = {
            "id": "rule-1",
            "slug": "legal-entities-present",
            "name": "Legal entities present",
            "description": "",
            "type": "custom",
            "severity": "low",
            "source": "user",
            "query": None,
            "target_filters": [
                {"field": self._GROUP_FIELD, "op": "is_not_null", "value": None},
            ],
            "compliance_criteria": None,
            "threshold": {},
            "silence_duration": None,
        }
        # Keep field resolution + SQL compilation real (the code under test); stub
        # only the pure-DB lifecycle reads that the resolution path doesn't touch.
        repo.get_enabled_rules = lambda cadence="pipeline": [rule]
        repo.get_active_alert_for_rule = lambda rule_id: None
        return AlertsService(repo), catalog

    def test_stage_refreshes_catalog_so_group_clause_is_not_dropped(self):
        fake_db = self._FlippableDB()
        service, catalog = self._build_service(fake_db)
        group_filter = {"field_id": self._GROUP_FIELD,
                        "operator": "is_not_null", "value": None}

        # Catalog primed while assets are empty (post-truncate, mid-rebuild): the
        # integration_group entry does not exist yet, so the compiler drops the
        # clause — the #790 failure (empty WHERE → the rule would match everything).
        fake_db.ext_attr_pairs = []
        catalog.entries()
        stale_where, _ = service.repo._compiler.build_where_clause([group_filter])
        assert stale_where == ""

        # Assets are now rebuilt — SentinelOne's siteId is back. Running the stage
        # must invalidate the stale snapshot so the clause is compiled, not dropped.
        fake_db.ext_attr_pairs = [("Corp SentinelOne", "siteId")]
        stage = AlertEvaluationStage(SimpleNamespace(db=fake_db))
        stage._get_alerts_service = lambda: service
        stage._get_anomaly_service = lambda: MagicMock()
        ctx = SimpleNamespace(
            stats={"stages": {}}, pipeline_wall_start=0.0, generation_run_id="run-1",
            pipeline_run_id=None,
        )

        stage._execute(ctx)

        assert fake_db.rule_queries, "the rule's target-filter query never ran"
        compiled = fake_db.rule_queries[-1]
        assert "extended_attributes" in compiled
        assert "siteId" in compiled
