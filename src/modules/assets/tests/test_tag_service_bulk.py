# SPDX-License-Identifier: AGPL-3.0-or-later
"""TagService.add_tag_bulk + the list-scoped tags-column refresh (#1156).

The blueprint tests mock the service away, so everything that makes bulk
tagging correct rather than merely 200 lives here: the xmax insert/update
split, one chronicle round trip for the batch, the denormalized assets.tags
refresh scoped to the affected ids, and the all-or-nothing scope.
"""

from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from src.modules.assets.services.tag_service import TagService
from src.modules.tag_rules.services.tags_column_service import refresh_tags_for_assets

A1 = "11111111-1111-4111-8111-111111111111"
A2 = "22222222-2222-4222-8222-222222222222"
A3 = "33333333-3333-4333-8333-333333333333"


def _row(asset_id, inserted):
    # RealDictCursor hands back a real UUID for a uuid column, not a str.
    return {"asset_id": UUID(asset_id), "inserted": inserted}


def _service(rows=None):
    db = MagicMock()
    db.execute_query.return_value = rows if rows is not None else []
    return TagService(db), db


def _sql(db):
    """The upsert statement as executed."""
    return db.execute_query.call_args.args[0]


def _params(db):
    return db.execute_query.call_args.args[1]


# ---------------------------------------------------------------------------
# The duplicate case: some assets already carry the tag
# ---------------------------------------------------------------------------

def test_existing_rows_are_recoloured_by_the_same_statement():
    # add_tag upserts with DO UPDATE SET tag_color; bulk must match it, or the
    # same tag applied per-asset and in bulk gives two different results.
    _svc, db = _service([_row(A1, True), _row(A2, False)])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        _svc.add_tag_bulk([A1, A2], "prod", "#ef4444", "admin")

    sql = _sql(db)
    assert "ON CONFLICT (asset_id, tag_name)" in sql
    assert "DO UPDATE SET tag_color = EXCLUDED.tag_color" in sql
    assert _params(db)["color"] == "#ef4444"
    # One statement for the whole set — not a bare multi-row INSERT that the
    # first UNIQUE (asset_id, tag_name) collision would kill mid-batch.
    assert db.execute_query.call_count == 1


def test_chronicle_events_only_for_genuine_inserts():
    # xmax = 0 splits the INSERT branch from the DO UPDATE branch. Without it
    # every asset in the filter set gets fabricated 'tag_added' history, even
    # the ones that already carried the tag.
    svc, db = _service([_row(A1, True), _row(A2, False), _row(A3, True)])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        result = svc.add_tag_bulk([A1, A2, A3], "prod", "#ef4444", "admin")

    assert "(xmax = 0) AS inserted" in _sql(db)
    db.bulk_write_chronicle_events.assert_called_once()
    events = db.bulk_write_chronicle_events.call_args.args[0]
    assert [e["asset_id"] for e in events] == [A1, A3]
    assert {e["event_type"] for e in events} == {"tag_added"}
    assert events[0]["attribute_changes"] == {"manual_tags": {"added": "prod"}}
    # One timestamp for the batch, like ReclassifyService.reclassify_bulk.
    assert events[0]["time"] == events[1]["time"]
    assert result == {"tagged": 3, "created": 2}


def test_all_assets_already_tagged_writes_no_history():
    svc, db = _service([_row(A1, False), _row(A2, False)])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        result = svc.add_tag_bulk([A1, A2], "prod", "#ef4444", "admin")

    db.bulk_write_chronicle_events.assert_not_called()
    assert result == {"tagged": 2, "created": 0}


def test_one_chronicle_call_for_the_whole_batch():
    # The per-asset path calls bulk_write_chronicle_events with a single-element
    # list; looping it over a capped 1000-asset set is 1000 round trips.
    ids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(500)]
    svc, db = _service([_row(i, True) for i in ids])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        svc.add_tag_bulk(ids, "prod", "#ef4444", "admin")

    assert db.bulk_write_chronicle_events.call_count == 1
    assert len(db.bulk_write_chronicle_events.call_args.args[0]) == 500


# ---------------------------------------------------------------------------
# The denormalized tags column
# ---------------------------------------------------------------------------

def test_refreshes_the_tags_column_for_the_affected_ids_only():
    # assets.tags is what Explorer filters on. Skip this and the rows land in
    # asset_manual_tags but the tag never becomes filterable — "bulk tag doesn't
    # work". Scoped to the ids: the all-assets rebuild would touch the fleet.
    svc, db = _service([_row(A1, True), _row(A2, True)])
    with patch(
        "src.modules.assets.services.tag_service.refresh_tags_for_assets"
    ) as refresh:
        svc.add_tag_bulk([A1, A2], "prod", "#ef4444", "admin")

    refresh.assert_called_once_with(db, [A1, A2])


def test_refresh_happens_before_the_scope_closes():
    # A refresh committed outside the tag write's transaction could leave the
    # column stale if the scope later rolls back.
    seen = {}

    def _spy(_db, ids):
        seen["end_scope_calls"] = _db.conn_manager.end_scope.call_count
        seen["ids"] = list(ids)

    svc, db = _service([_row(A1, True)])
    with patch(
        "src.modules.assets.services.tag_service.refresh_tags_for_assets",
        side_effect=_spy,
    ):
        svc.add_tag_bulk([A1], "prod", "#ef4444", "admin")

    assert seen == {"end_scope_calls": 0, "ids": [A1]}


# ---------------------------------------------------------------------------
# Transaction scope
# ---------------------------------------------------------------------------

def test_all_writes_share_one_scope():
    svc, db = _service([_row(A1, True), _row(A2, True)])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        svc.add_tag_bulk([A1, A2], "prod", "#ef4444", "admin")

    names = [c[0] for c in db.mock_calls]
    assert names[0] == "conn_manager.begin_scope"
    assert names[-1] == "conn_manager.end_scope"
    assert names.count("conn_manager.begin_scope") == 1
    assert names.count("conn_manager.end_scope") == 1
    # Committed, not rolled back.
    db.conn_manager.end_scope.assert_called_once_with()


@pytest.mark.parametrize("failing", ["upsert", "refresh"])
def test_a_mid_batch_failure_rolls_the_whole_thing_back(failing):
    boom = RuntimeError("boom")
    svc, db = _service([_row(A1, True)])
    if failing == "upsert":
        db.execute_query.side_effect = boom
        refresh_side_effect = None
    else:
        refresh_side_effect = boom

    with patch(
        "src.modules.assets.services.tag_service.refresh_tags_for_assets",
        side_effect=refresh_side_effect,
    ):
        with pytest.raises(RuntimeError, match="boom"):
            svc.add_tag_bulk([A1], "prod", "#ef4444", "admin")

    db.conn_manager.end_scope.assert_called_once_with(error=boom)
    if failing == "upsert":
        db.bulk_write_chronicle_events.assert_not_called()


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

def test_empty_id_list_opens_no_scope():
    svc, db = _service()
    assert svc.add_tag_bulk([], "prod", "#ef4444", "admin") == {"tagged": 0, "created": 0}
    db.conn_manager.begin_scope.assert_not_called()
    db.execute_query.assert_not_called()


@pytest.mark.parametrize("name", ["", "   ", None])
def test_blank_tag_name_raises_before_any_write(name):
    svc, db = _service()
    with pytest.raises(ValueError, match="Tag name cannot be empty"):
        svc.add_tag_bulk([A1], name, "#ef4444", "admin")
    db.conn_manager.begin_scope.assert_not_called()
    db.execute_query.assert_not_called()


def test_tag_name_is_trimmed_like_the_per_asset_path():
    svc, db = _service([_row(A1, True)])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        svc.add_tag_bulk([A1], "  prod  ", "#ef4444", "admin")
    assert _params(db)["name"] == "prod"


def test_duplicate_ids_are_deduped():
    # ON CONFLICT DO UPDATE raises a cardinality violation if one statement
    # touches the same row twice, which would fail the entire batch.
    svc, db = _service([_row(A1, True), _row(A2, True)])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        svc.add_tag_bulk([A1, A2, A1], "prod", "#ef4444", "admin")
    assert _params(db)["ids"] == [A1, A2]


# ---------------------------------------------------------------------------
# refresh_tags_for_assets itself
# ---------------------------------------------------------------------------

def test_refresh_tags_for_assets_is_one_id_scoped_statement():
    db = MagicMock()
    refresh_tags_for_assets(db, [A1, A2])

    db.execute_query.assert_called_once()
    sql, params = db.execute_query.call_args.args[0], db.execute_query.call_args.args[1]
    # Every leg of the rebuild is bounded by the id list — the all-assets
    # version's unbounded scan is what this helper exists to avoid.
    assert sql.count("ANY(%(ids)s::uuid[])") == 3
    assert "asset_tags_v2" in sql and "asset_manual_tags" in sql
    assert params == {"ids": [A1, A2]}


def test_refresh_tags_for_assets_empty_is_a_noop():
    db = MagicMock()
    refresh_tags_for_assets(db, [])
    db.execute_query.assert_not_called()


def test_refresh_tags_for_assets_raises_rather_than_swallowing():
    # Swallowing would commit the tag rows with a stale filterable column.
    db = MagicMock()
    db.execute_query.side_effect = RuntimeError("nope")
    with pytest.raises(RuntimeError, match="nope"):
        refresh_tags_for_assets(db, [A1])
