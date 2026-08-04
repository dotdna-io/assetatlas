# SPDX-License-Identifier: AGPL-3.0-or-later
"""TagService.remove_tag_bulk + the manual-tag candidate picker (#1156 undo).

Mirror of test_tag_service_bulk.py for the removal half. The blueprint tests
mock the service away, so everything that makes bulk untagging correct rather
than merely 200 lives here: one set-based DELETE keyed on tag_name, chronicle
events for actual deletions only, the assets.tags refresh over the whole
resolved set, and the all-or-nothing scope.

The candidates picker is tested here too because its one load-bearing property
is which table it reads: asset_manual_tags, never the assets.tags / catalog
union, since only manual rows can be deleted.
"""

from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from src.modules.assets.services.tag_service import TagService

A1 = "11111111-1111-4111-8111-111111111111"
A2 = "22222222-2222-4222-8222-222222222222"
A3 = "33333333-3333-4333-8333-333333333333"


def _row(asset_id):
    # RealDictCursor hands back a real UUID for a uuid column, not a str.
    return {"asset_id": UUID(asset_id)}


def _service(rows=None):
    db = MagicMock()
    db.execute_query.return_value = rows if rows is not None else []
    return TagService(db), db


def _sql(db):
    """The statement as executed."""
    return db.execute_query.call_args.args[0]


def _params(db):
    return db.execute_query.call_args.args[1]


# ---------------------------------------------------------------------------
# The DELETE itself
# ---------------------------------------------------------------------------

def test_removal_is_one_set_based_delete_keyed_on_tag_name():
    # remove_tag keys on the row's own id and fetches it first, so reusing it
    # for a capped 1000-asset set would be 2000 round trips. One statement,
    # keyed on (id set, tag_name).
    svc, db = _service([_row(A1), _row(A2)])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        svc.remove_tag_bulk([A1, A2], "prod", "admin")

    sql = _sql(db)
    assert "DELETE FROM asset_manual_tags" in sql
    assert "asset_id = ANY(%(ids)s::uuid[])" in sql
    assert "tag_name = %(name)s" in sql
    assert "RETURNING asset_id" in sql
    assert db.execute_query.call_count == 1
    assert _params(db) == {"ids": [A1, A2], "name": "prod"}


def test_no_xmax_split_on_the_delete_path():
    # add_tag_bulk needs (xmax = 0) because its RETURNING fires for both the
    # INSERT and the DO UPDATE branch. A DELETE has no second branch — every
    # returned row is a genuine deletion — so copying the split across would be
    # dead code implying a distinction that does not exist here.
    svc, db = _service([_row(A1)])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        svc.remove_tag_bulk([A1], "prod", "admin")

    assert "xmax" not in _sql(db)


# ---------------------------------------------------------------------------
# Chronicle history
# ---------------------------------------------------------------------------

def test_chronicle_events_only_for_rows_actually_deleted():
    # Two of the three matched assets carried the tag. History must record two
    # removals, not three — the third asset never had it.
    svc, db = _service([_row(A1), _row(A3)])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        result = svc.remove_tag_bulk([A1, A2, A3], "prod", "admin")

    db.bulk_write_chronicle_events.assert_called_once()
    events = db.bulk_write_chronicle_events.call_args.args[0]
    assert [e["asset_id"] for e in events] == [A1, A3]
    assert {e["event_type"] for e in events} == {"tag_removed"}
    assert events[0]["changed_attributes"] == ["manual_tags"]
    assert events[0]["attribute_changes"] == {"manual_tags": {"removed": "prod"}}
    # One timestamp for the batch, like add_tag_bulk / reclassify_bulk.
    assert events[0]["time"] == events[1]["time"]
    assert result == {"removed": 2}


def test_assets_without_the_tag_are_silently_skipped():
    # The expected case, not an error: a filter-scoped untag is aimed at a set
    # where only some assets carry the tag. Nothing matched the DELETE, so
    # there is no failure, no history, and nothing removed.
    svc, db = _service([])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        result = svc.remove_tag_bulk([A1, A2], "prod", "admin")

    db.bulk_write_chronicle_events.assert_not_called()
    assert result == {"removed": 0}


def test_one_chronicle_call_for_the_whole_batch():
    ids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(500)]
    svc, db = _service([_row(i) for i in ids])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        svc.remove_tag_bulk(ids, "prod", "admin")

    assert db.bulk_write_chronicle_events.call_count == 1
    assert len(db.bulk_write_chronicle_events.call_args.args[0]) == 500


# ---------------------------------------------------------------------------
# The denormalized tags column
# ---------------------------------------------------------------------------

def test_refreshes_the_tags_column_for_the_whole_resolved_set():
    # assets.tags is what Explorer filters on. Skip this and the rows vanish
    # from asset_manual_tags while the map still advertises the tag on every
    # asset — "untag did nothing". Scoped to the resolved ids, not the fleet.
    svc, db = _service([_row(A1)])
    with patch(
        "src.modules.assets.services.tag_service.refresh_tags_for_assets"
    ) as refresh:
        svc.remove_tag_bulk([A1, A2], "prod", "admin")

    refresh.assert_called_once_with(db, [A1, A2])


def test_refresh_happens_before_the_scope_closes():
    # A refresh committed outside the delete's transaction could leave the
    # column stale if the scope later rolls back.
    seen = {}

    def _spy(_db, ids):
        seen["end_scope_calls"] = _db.conn_manager.end_scope.call_count
        seen["ids"] = list(ids)

    svc, db = _service([_row(A1)])
    with patch(
        "src.modules.assets.services.tag_service.refresh_tags_for_assets",
        side_effect=_spy,
    ):
        svc.remove_tag_bulk([A1], "prod", "admin")

    assert seen == {"end_scope_calls": 0, "ids": [A1]}


# ---------------------------------------------------------------------------
# Transaction scope
# ---------------------------------------------------------------------------

def test_all_writes_share_one_scope():
    svc, db = _service([_row(A1), _row(A2)])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        svc.remove_tag_bulk([A1, A2], "prod", "admin")

    names = [c[0] for c in db.mock_calls]
    assert names[0] == "conn_manager.begin_scope"
    assert names[-1] == "conn_manager.end_scope"
    assert names.count("conn_manager.begin_scope") == 1
    assert names.count("conn_manager.end_scope") == 1
    # Committed, not rolled back.
    db.conn_manager.end_scope.assert_called_once_with()


@pytest.mark.parametrize("failing", ["delete", "refresh"])
def test_a_mid_batch_failure_rolls_the_whole_thing_back(failing):
    boom = RuntimeError("boom")
    svc, db = _service([_row(A1)])
    if failing == "delete":
        db.execute_query.side_effect = boom
        refresh_side_effect = None
    else:
        refresh_side_effect = boom

    with patch(
        "src.modules.assets.services.tag_service.refresh_tags_for_assets",
        side_effect=refresh_side_effect,
    ):
        with pytest.raises(RuntimeError, match="boom"):
            svc.remove_tag_bulk([A1], "prod", "admin")

    db.conn_manager.end_scope.assert_called_once_with(error=boom)
    if failing == "delete":
        db.bulk_write_chronicle_events.assert_not_called()


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

def test_empty_id_list_opens_no_scope():
    svc, db = _service()
    assert svc.remove_tag_bulk([], "prod", "admin") == {"removed": 0}
    db.conn_manager.begin_scope.assert_not_called()
    db.execute_query.assert_not_called()


@pytest.mark.parametrize("name", ["", "   ", None])
def test_blank_tag_name_raises_before_any_write(name):
    svc, db = _service()
    with pytest.raises(ValueError, match="Tag name cannot be empty"):
        svc.remove_tag_bulk([A1], name, "admin")
    db.conn_manager.begin_scope.assert_not_called()
    db.execute_query.assert_not_called()


def test_tag_name_is_trimmed_like_the_per_asset_path():
    svc, db = _service([_row(A1)])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        svc.remove_tag_bulk([A1], "  prod  ", "admin")
    assert _params(db)["name"] == "prod"


def test_duplicate_ids_are_deduped():
    # Hygiene rather than the correctness fix it is in add_tag_bulk: a plain
    # DELETE has no cardinality-violation problem, it just re-tests a row.
    svc, db = _service([_row(A1)])
    with patch("src.modules.assets.services.tag_service.refresh_tags_for_assets"):
        svc.remove_tag_bulk([A1, A2, A1], "prod", "admin")
    assert _params(db)["ids"] == [A1, A2]


# ---------------------------------------------------------------------------
# The candidate picker
# ---------------------------------------------------------------------------

def test_candidates_come_from_asset_manual_tags_only():
    # assets.tags — and the field catalog built from it — is the UNION of
    # asset_tags_v2 (applied by tag rules) and asset_manual_tags. Sourcing the
    # picker from either union would offer a rule-based tag the DELETE can
    # never match: the action would report zero removals, the tag would stay on
    # every asset, and the next pipeline run would re-apply it anyway.
    svc, db = _service([{"tag_name": "prod", "count": 4}])
    svc.get_bulk_tag_candidates([A1, A2])

    sql = _sql(db)
    assert "FROM asset_manual_tags" in sql
    assert "asset_tags_v2" not in sql
    assert "assets.tags" not in sql
    assert "FROM assets" not in sql


def test_candidates_are_scoped_to_the_id_set_and_counted_per_tag():
    rows = [{"tag_name": "prod", "count": 4}, {"tag_name": "stale", "count": 1}]
    svc, db = _service(rows)

    assert svc.get_bulk_tag_candidates([A1, A2]) == rows
    sql = _sql(db)
    assert "asset_id = ANY(%(ids)s::uuid[])" in sql
    assert "count(*)::int AS count" in sql
    assert "GROUP BY tag_name" in sql
    assert "ORDER BY tag_name" in sql
    assert _params(db) == {"ids": [A1, A2]}


def test_candidates_empty_id_list_queries_nothing():
    svc, db = _service()
    assert svc.get_bulk_tag_candidates([]) == []
    db.execute_query.assert_not_called()


def test_candidates_no_manual_tags_is_an_empty_list_not_an_error():
    # A matched set where nobody carries a manual tag is a valid answer; the
    # dialog turns it into "nothing to remove", not a failure.
    svc, _db = _service([])
    assert svc.get_bulk_tag_candidates([A1]) == []


def test_candidates_are_read_outside_a_write_scope():
    # A read-only picker load must not open a transaction scope.
    svc, db = _service([{"tag_name": "prod", "count": 1}])
    svc.get_bulk_tag_candidates([A1])
    db.conn_manager.begin_scope.assert_not_called()
