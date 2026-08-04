# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end anomaly engine on a real DB (#584 §4, phase 1).

Marked ``integration``: needs a disposable Postgres/TimescaleDB at
``DATABASE_URL`` carrying the asset schema. Reuses the ``grace_db`` fixture
(tests/integration/conftest.py), which refuses the shared dev DB
(192.0.2.10). Run:

    DATABASE_URL="postgresql://test:test@localhost:5434/assetatlas_test" \\
        pytest -m integration tests/integration/test_anomaly_engine.py -v

Everything runs inside one connection scope that is rolled back — migration 117
(DDL + seed rules), the seeded history, and the created alerts all disappear, so
the test is non-destructive and independent.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.modules.alerts.repository import AlertsRepository
from src.modules.anomaly_detection.service import AnomalyDetectionService

pytestmark = pytest.mark.integration

_MIGRATION = "src/database/migrations/117_add_anomaly_detection.sql"


def _ensure_anomaly_schema(db):
    """Apply migration 117 within the current txn (idempotent). Strip its own
    BEGIN/COMMIT so it composes with the test's rolled-back scope."""
    body = open(_MIGRATION).read().replace("BEGIN;", "").replace("COMMIT;", "")
    db.execute_query(body, fetch=False)


def _seed_security_assets(db, n, source="SentinelOne", category="endpoint security"):
    ids = []
    for _ in range(n):
        aid = str(uuid4())
        db.execute_query(
            "INSERT INTO assets (id, data_source, data_source_category, first_seen, "
            "last_seen, confidence) VALUES (%(id)s::uuid, %(ds)s, %(cat)s, "
            "now() - interval '20 days', now(), 0.9)",
            {"id": aid, "ds": [source], "cat": [category]}, fetch=False,
        )
        ids.append(aid)
    return ids


def _seed_loss_event(db, asset_id, grid, source, when):
    db.execute_query(
        'INSERT INTO asset_chronicle ("time", asset_id, event_type, generation_run_id, '
        "changed_attributes, attribute_changes, causation) VALUES "
        "(%(t)s, %(aid)s::uuid, 'attribute_updated', %(grid)s::uuid, "
        "ARRAY['data_source'], %(chg)s::jsonb, 'scheduled_scan')",
        {"t": when, "aid": asset_id, "grid": grid,
         "chg": json.dumps({"data_source": {"removed": [source]}})},
        fetch=False,
    )


def _anomaly_alerts(db, detector):
    out = []
    for a in AlertsRepository(db).get_alerts(time_range="all", per_page=200):
        ev = a.get("evidence")
        if isinstance(ev, str):
            ev = json.loads(ev)
        if isinstance(ev, dict) and ev.get("detector") == detector:
            out.append({**a, "evidence": ev})
    return out


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="no DATABASE_URL")
def test_d1_uninstall_sweep_fires_end_to_end_with_evidence_and_dedups(grace_db):
    db = grace_db
    cm = db.conn_manager
    cm.begin_scope()
    try:
        _ensure_anomaly_schema(db)
        grid = str(uuid4())
        now = datetime.now(timezone.utc)

        asset_ids = _seed_security_assets(db, 40)

        # Baseline: ~10 prior runs each losing SentinelOne on 2 assets (quiet).
        for r in range(10):
            prior = str(uuid4())
            for k in range(2):
                _seed_loss_event(db, asset_ids[k], prior, "SentinelOne",
                                 now - timedelta(days=80 - r * 5))

        # This run: 30 assets lose SentinelOne — the uninstall sweep.
        for aid in asset_ids[:30]:
            _seed_loss_event(db, aid, grid, "SentinelOne", now)

        svc = AnomalyDetectionService(db, AlertsRepository(db))
        # No asset_created / attribute_updated / batches -> A1/A3/A4/C1/C2 quiet.
        current_stats = {"stages": {"chronicle": {"event_type_counts": {}}}}
        summary = svc.evaluate_run(grid, now, current_stats, current_batches=[])

        assert summary["alerts_created"] >= 1
        assert not summary["errors"], summary["errors"]

        d1 = _anomaly_alerts(db, "security_source_loss")
        assert len(d1) == 1, "exactly one D1 alert"
        assert d1[0]["evidence"]["observed"] == 30
        assert d1[0]["evidence"]["source"] == "SentinelOne"
        assert d1[0]["severity"] == "high"

        # A1 (mass new assets) must stay quiet — no created events this run.
        assert _anomaly_alerts(db, "mass_new_assets") == []

        # Re-running the identical evaluation must NOT duplicate the D1 event
        # alert (fingerprint/silence dedup).
        svc.evaluate_run(grid, now, current_stats, current_batches=[])
        assert len(_anomaly_alerts(db, "security_source_loss")) == 1
    finally:
        cm.end_scope(error=RuntimeError("intentional test rollback"))
