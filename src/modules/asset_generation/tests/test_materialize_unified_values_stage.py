# SPDX-License-Identifier: AGPL-3.0-or-later
"""MaterializeUnifiedValuesStage — scope filtering, stats, skip, propagation,
and its exact position in the orchestrator's core sequence (#1405).

The refresh itself (SQL shape) is covered in
tests/unit/modules/fields/unified/test_materialize.py; here the refresh is
stubbed so the stage's own behaviour is what is under test.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg2.errors
import pytest

from src.modules.asset_generation.pipeline.stages.s16b_materialize_unified_values import (
    MaterializeUnifiedValuesEnrichmentStage,
    MaterializeUnifiedValuesStage,
)
from src.modules.asset_generation.services.pipeline_orchestrator import (
    PipelineOrchestrator,
)
from src.modules.fields.unified import materialize


@pytest.fixture(autouse=True)
def _reset_migration_149_probe_state(monkeypatch):
    """No cross-test leaks of the process-wide probe cache / once-warning."""
    monkeypatch.setattr(materialize, "_COLUMN_PRESENT", {})
    monkeypatch.setattr(materialize, "_MIGRATION_149_WARNED", False)


def _catalog_row(slug, sources, function="coalesce"):
    return {"slug": slug, "name": slug, "function": function,
            "sources": sources, "data_type": "string", "materialized_at": None}


def _service(catalog_rows):
    svc = MagicMock()
    svc.db.execute_query.return_value = catalog_rows
    return svc


def _ctx():
    return SimpleNamespace(
        stats={'stages': {}},
        pipeline_wall_start=time.time(),
        notify_stage=lambda label: None,
    )


def _stub_refresh(monkeypatch):
    """Record the refresh call; return counts derived from what it was given."""
    seen = {}

    def _fake(db_adapter, resolver, definitions, **kwargs):
        seen['definitions'] = list(definitions)
        seen['kwargs'] = kwargs
        return {"fields": len(definitions), "rows": 42, "chunks": 1}

    monkeypatch.setattr(
        "src.modules.asset_generation.pipeline.stages.s16b_materialize_unified_values"
        ".refresh_unified_values", _fake)
    return seen


# ── Scope filtering ───────────────────────────────────────────────────────────

def test_all_scope_refreshes_every_materializable_field(monkeypatch):
    seen = _stub_refresh(monkeypatch)
    svc = _service([
        _catalog_row("lentity", ["ext:A:x", "ext:B:x"]),
        _catalog_row("enriched", ["ext:enrichment:legal_entity", "ext:A:y"]),
        _catalog_row("posture_mix", ["static:Category", "posture_state:secured"]),
        _catalog_row("tag_mix", ["static:Category", "tag:critical"]),
    ])

    MaterializeUnifiedValuesStage(svc, scope="all").execute(_ctx())

    assert [d["slug"] for d in seen['definitions']] == ["lentity", "enriched"]


def test_enrichment_scope_refreshes_only_enrichment_sourced_fields(monkeypatch):
    seen = _stub_refresh(monkeypatch)
    svc = _service([
        _catalog_row("lentity", ["ext:A:x", "ext:B:x"]),
        _catalog_row("enriched", ["ext:enrichment:legal_entity", "ext:A:y"]),
        _catalog_row("posture_mix", ["static:Category", "posture_state:secured"]),
    ])

    MaterializeUnifiedValuesStage(svc, scope="enrichment_sourced").execute(_ctx())

    assert [d["slug"] for d in seen['definitions']] == ["enriched"]


# ── Stats / skip / failure ────────────────────────────────────────────────────

def test_stats_record_fields_rows_chunks_and_ms(monkeypatch):
    _stub_refresh(monkeypatch)
    svc = _service([_catalog_row("lentity", ["ext:A:x", "ext:B:x"])])
    ctx = _ctx()

    MaterializeUnifiedValuesStage(svc, scope="all").execute(ctx)

    stats = ctx.stats['stages']["materialize_unified_values"]
    assert stats['fields'] == 1
    assert stats['rows'] == 42
    assert stats['chunks'] == 1
    assert isinstance(stats['ms'], int)


def test_should_skip_when_nothing_materializable_for_the_scope(monkeypatch):
    seen = _stub_refresh(monkeypatch)
    svc = _service([
        _catalog_row("lentity", ["ext:A:x", "ext:B:x"]),
        _catalog_row("posture_mix", ["static:Category", "posture_state:secured"]),
    ])
    ctx = _ctx()

    # No ext:enrichment: source anywhere → the enrichment pass skips.
    MaterializeUnifiedValuesStage(svc, scope="enrichment_sourced").execute(ctx)

    assert seen == {}
    assert ctx.stats['stages']["materialize_unified_values_enrichment"] == {'skipped': True}


def test_a_refresh_failure_propagates_and_aborts_the_run(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(
        "src.modules.asset_generation.pipeline.stages.s16b_materialize_unified_values"
        ".refresh_unified_values", _boom)
    svc = _service([_catalog_row("lentity", ["ext:A:x", "ext:B:x"])])

    with pytest.raises(RuntimeError, match="refresh failed"):
        MaterializeUnifiedValuesStage(svc, scope="all").execute(_ctx())


def test_an_unknown_scope_is_rejected():
    with pytest.raises(ValueError):
        MaterializeUnifiedValuesStage(MagicMock(), scope="everything")


def test_the_enrichment_subclass_is_the_parameterized_form():
    stage = MaterializeUnifiedValuesEnrichmentStage(MagicMock())
    assert isinstance(stage, MaterializeUnifiedValuesStage)
    assert stage.scope == "enrichment_sourced"
    assert stage.key == "materialize_unified_values_enrichment"


def test_keys_are_readable_off_a_bare_new_instance():
    """The phase-mapping gate (tests/unit/modules/test_phase_mapping.py) reads
    .key via cls.__new__(cls) — no __init__ — per stage class, so both classes
    must answer without a constructor and between them surface both keys."""
    assert MaterializeUnifiedValuesStage.__new__(
        MaterializeUnifiedValuesStage).key == "materialize_unified_values"
    assert MaterializeUnifiedValuesEnrichmentStage.__new__(
        MaterializeUnifiedValuesEnrichmentStage
    ).key == "materialize_unified_values_enrichment"


# ── Orchestrator wiring ───────────────────────────────────────────────────────

def _core_sequence():
    """Core stage class names with the materialize stages annotated by key."""
    orch = PipelineOrchestrator(db_adapter=MagicMock())
    out = []
    for stage in orch.core_stages:
        if isinstance(stage, MaterializeUnifiedValuesStage):
            out.append(f"MaterializeUnifiedValuesStage[{stage.key}]")
        else:
            out.append(type(stage).__name__)
    return out, orch


def test_the_materialize_stages_sit_in_the_exact_core_positions():
    seq, _ = _core_sequence()
    interesting = [n for n in seq if n in (
        "PersistenceStage",
        "MaterializeUnifiedValuesStage[materialize_unified_values]",
        "TagRulesStage",
        "CsvEnrichmentStage",
        "MaterializeUnifiedValuesStage[materialize_unified_values_enrichment]",
        "ChangeTrackingStage",
    )]
    assert interesting == [
        "PersistenceStage",
        "MaterializeUnifiedValuesStage[materialize_unified_values]",
        "TagRulesStage",
        "CsvEnrichmentStage",
        "MaterializeUnifiedValuesStage[materialize_unified_values_enrichment]",
        "ChangeTrackingStage",
    ]


def test_both_materialize_stages_are_core_not_post_commit():
    _, orch = _core_sequence()
    materialize = [s for s in orch.core_stages
                   if isinstance(s, MaterializeUnifiedValuesStage)]
    assert len(materialize) == 2
    assert all(s.post_commit is False for s in materialize)
    assert not [s for s in orch.post_commit_stages
                if isinstance(s, MaterializeUnifiedValuesStage)]
    assert {s.key for s in materialize} == {
        "materialize_unified_values", "materialize_unified_values_enrichment"}
    assert {s.scope for s in materialize} == {"all", "enrichment_sourced"}


# ── Pre-migration-149 tolerance ───────────────────────────────────────────────
# base.execute() calls should_skip outside any try, so on a database without
# migration 149 the stage must probe BEFORE list_for_catalog and skip — never
# raise UndefinedColumn and abort every run. Real failures with the columns
# present still propagate (test_a_refresh_failure_propagates_and_aborts_the_run).

class _Pre149Db:
    """The pre-149 database: probes find no columns; any other SQL (a stray
    list_for_catalog SELECT or UPDATE) raises UndefinedColumn — must never run."""
    def __init__(self):
        self.sql = []

    def execute_query(self, sql, params=None):
        self.sql.append(sql)
        if "information_schema.columns" in sql:
            return []
        raise psycopg2.errors.UndefinedColumn("column does not exist")


def _pre149_service():
    svc = MagicMock()
    svc.db = _Pre149Db()
    return svc


def test_should_skip_does_not_raise_on_a_pre149_database():
    stage = MaterializeUnifiedValuesStage(_pre149_service(), scope="all")
    assert stage.should_skip(_ctx()) is True


def test_execute_on_a_pre149_database_skips_with_the_reason_and_no_writes():
    svc = _pre149_service()
    ctx = _ctx()

    MaterializeUnifiedValuesStage(svc, scope="all").execute(ctx)

    assert ctx.stats['stages']["materialize_unified_values"] == {
        'skipped': True, 'reason': 'migration 149 not applied'}
    # nothing but probe SQL was issued — no catalog SELECT, no UPDATE
    assert svc.db.sql
    assert all("information_schema.columns" in sql for sql in svc.db.sql)


def test_the_enrichment_pass_skips_the_same_way_on_a_pre149_database():
    svc = _pre149_service()
    ctx = _ctx()

    MaterializeUnifiedValuesEnrichmentStage(svc).execute(ctx)

    assert ctx.stats['stages']["materialize_unified_values_enrichment"] == {
        'skipped': True, 'reason': 'migration 149 not applied'}
