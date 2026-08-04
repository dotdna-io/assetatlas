# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end grace-period behavior on a real DB (#795).

Marked ``integration``: needs a live Postgres/TimescaleDB at ``DATABASE_URL``
with the asset schema (migration 115 applied). The ``grace_db`` fixture refuses
the shared dev DB (192.0.2.10); use a disposable DB, e.g.:
    DATABASE_URL="postgresql://test:test@localhost:5434/assetatlas_test" \\
        pytest -m integration tests/integration/test_grace_period_pipeline.py -v

These tests drive the real absence-lifecycle path directly — seed an asset, then
simulate a run in which it is absent and run
``GraceCarryForwardStage`` -> ``save_assets`` -> ``_track_changes`` — rather than
the full 12-stage pipeline, so they exercise exactly the grace behavior without
depending on a synthetic input surviving every upstream stage. Each test runs
inside a connection scope that is intentionally rolled back, so the DB is left
unchanged and the tests are independent and safe.
"""
import os
from uuid import uuid4

import pandas as pd
import pytest

# The empty "present" set in these tests makes the stage concat an all-NA frame,
# which raises a pandas FutureWarning about concat dtype handling. It does not
# occur in production (scored_df always has the run's real assets) and is a
# pandas-version deprecation, not a logic issue — suppress it narrowly so test
# output stays pristine.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.filterwarnings(
        "ignore:The behavior of DataFrame concatenation with empty:FutureWarning"
    ),
]

SEED_ID = "aaaaaaaa-0000-4000-8000-000000000001"
SEED_HOST = "grace-seed-host-01"


def _set_grace(db, days):
    db.execute_query(
        "INSERT INTO system_settings (category, key, value, value_type, default_value) "
        "VALUES ('asset_generation','removal_grace_period_days',%(v)s,'integer','30') "
        "ON CONFLICT (category, key) DO UPDATE SET value = EXCLUDED.value",
        {"v": str(days)}, fetch=False,
    )


def _chronicle_count(db, asset_id, event_type, run_id):
    rows = db.execute_query(
        "SELECT count(*) AS n FROM asset_chronicle "
        "WHERE asset_id = %(id)s::uuid AND event_type = %(t)s "
        "AND generation_run_id = %(r)s::uuid",
        {"id": str(asset_id), "t": event_type, "r": str(run_id)},
    )
    return int(rows[0]["n"]) if rows else 0


def _asset_exists(db, asset_id):
    return bool(db.execute_query(
        "SELECT 1 FROM assets WHERE id = %(id)s::uuid", {"id": str(asset_id)}))


def _missing_since(db, asset_id):
    rows = db.execute_query(
        "SELECT missing_since FROM assets WHERE id = %(id)s::uuid", {"id": str(asset_id)})
    return rows[0]["missing_since"] if rows else None


def _seed_asset(service, run_id):
    """Persist one asset as 'previously present' (archive + truncate + insert)."""
    seed = pd.DataFrame([{
        "id": SEED_ID,
        "hostname": [SEED_HOST],
        "data_source": ["grace_seed"],
        "missing_since": None,
        "last_generation_run_id": str(run_id),
    }])
    service.asset_repo.save_assets(seed, generation_run_id=run_id)


def _absence_run(service, run_id):
    """Simulate a run where every prior asset is absent (empty present set) and
    drive the grace stage + persistence + change-tracking. Returns grace stats."""
    from src.modules.asset_generation.pipeline.context import PipelineContext
    from src.modules.asset_generation.pipeline.stages.s15c_grace_carry_forward import (
        GraceCarryForwardStage,
    )
    old = service.asset_repo.get_all_assets()
    historical = service.asset_repo.get_all_historical_asset_ids()
    scored = old.iloc[0:0].copy()  # nothing present -> all prior assets absent

    ctx = PipelineContext(raw_data_df=pd.DataFrame(), track_changes=True,
                          generation_run_id=run_id)
    ctx.scored_df = scored
    ctx.old_assets_df = old
    ctx.historical_ids = historical

    GraceCarryForwardStage(service)._execute(ctx)
    service.asset_repo.save_assets(ctx.scored_df, generation_run_id=run_id)
    service._track_changes(old, ctx.scored_df, run_id,
                           providers_df=None, historical_ids=historical)
    return ctx.stats["stages"].get("grace_carry_forward")


def _service(db):
    from src.modules.asset_generation.services.asset_generation_service import (
        AssetGenerationService,
    )
    return AssetGenerationService(db_adapter=db)


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="no DATABASE_URL")
def test_absent_asset_within_grace_is_carried_not_removed(grace_db):
    """grace>0: an asset present then absent must NOT fire asset_removed, must
    remain in the table, and be anchored with missing_since."""
    db = grace_db
    service = _service(db)
    cm = db.conn_manager
    cm.begin_scope()
    try:
        _set_grace(db, 30)
        _seed_asset(service, uuid4())
        run2 = uuid4()
        stats = _absence_run(service, run2)

        assert stats and stats["carried_forward"] >= 1
        assert _chronicle_count(db, SEED_ID, "asset_removed", run2) == 0, \
            "quiet asset within grace must not fire asset_removed"
        assert _asset_exists(db, SEED_ID), "carried asset must remain in the table"
        assert _missing_since(db, SEED_ID) is not None, "carried asset must be anchored"
    finally:
        cm.end_scope(error=RuntimeError("intentional test rollback"))


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="no DATABASE_URL")
def test_grace_zero_removes_immediately(grace_db):
    """grace=0 (disabled): an absent asset is hard-removed immediately —
    asset_removed fires and the row is gone."""
    db = grace_db
    service = _service(db)
    cm = db.conn_manager
    cm.begin_scope()
    try:
        _set_grace(db, 0)
        _seed_asset(service, uuid4())
        run2 = uuid4()
        stats = _absence_run(service, run2)

        assert stats and stats["carried_forward"] == 0, "grace=0 must carry nothing"
        assert _chronicle_count(db, SEED_ID, "asset_removed", run2) == 1, \
            "grace=0: absent asset must fire asset_removed"
        assert not _asset_exists(db, SEED_ID), "grace=0: hard-removed asset must be gone"
    finally:
        cm.end_scope(error=RuntimeError("intentional test rollback"))
