# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavior tests for get_asset_history_impl: alert interleaving + range/sort/search.

The production asset-history timeline (HistoryTable.tsx / EventTimeline.tsx) shows
BOTH chronicle field-change events AND alert events on one merged timeline. The
chat tool previously queried ONLY ``asset_chronicle`` (monitoring.py), so every
alert that fired on the asset was silently dropped — a fidelity loss the UI does
not have. These tests pin the closed gaps:

- alerts are interleaved with chronicle changes (via the ``alerts.asset_ids``
  array linkage, NOT a join table);
- a custom ``start``/``end`` window OVERRIDES the ``time_range`` preset and bounds
  every query;
- unparseable custom input / unknown sort fail LOUDLY rather than silently
  answering a default window/order (a wrong window is worse than an error — §4);
- ``search`` actually narrows the merged stream (attribute fields AND alert title).

Each test mocks ``db.execute_query`` so no live DB is needed.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from src.mcp.tools.monitoring import get_asset_history_impl

_UTC = timezone.utc


def _asset_row(aid):
    return {"id": aid, "hostname": ["host01"], "category": "server",
            "ip": ["10.0.0.1"]}


def _chronicle_row(**over):
    row = {
        "id": 1,
        "time": datetime(2026, 4, 20, 10, 0, tzinfo=_UTC),
        "event_type": "attribute_updated",
        "changed_attributes": None,
        "attribute_changes": {
            "owner": {"old": ["alice"], "new": ["bob"], "source": "Jamf"},
        },
    }
    row.update(over)
    return row


def _alert_row(**over):
    row = {
        "id": "11111111-2222-3333-4444-555555555555",
        "type": "compliance",
        "severity": "high",
        "title": "EDR agent missing",
        "description": "no edr",
        "status": "active",
        "created_at": datetime(2026, 4, 21, 9, 0, tzinfo=_UTC),
        "resolved_at": None,
    }
    row.update(over)
    return row


def _db(asset, chronicle, alerts):
    """A db whose 3 sequential execute_query calls return asset / chronicle /
    alerts respectively (the order get_asset_history_impl issues them in)."""
    db = MagicMock()
    db.execute_query.side_effect = [[asset], chronicle, alerts]
    return db


def _executed_sql(db):
    return [c.args[0] for c in db.execute_query.call_args_list if c.args]


def _windowed_params(db, key):
    return [c.args[1].get(key) for c in db.execute_query.call_args_list
            if len(c.args) > 1 and isinstance(c.args[1], dict) and key in c.args[1]]


def test_interleaves_alert_events_with_chronicle():
    """The UI timeline shows alerts inline; the old tool never did. Both an
    owner change AND the alert must appear, and the alert must carry its type /
    severity / title so the model can act on it."""
    aid = str(uuid4())
    db = _db(_asset_row(aid), [_chronicle_row()], [_alert_row()])
    out = get_asset_history_impl(db, asset_id=aid, time_range="30d")

    # chronicle field change present
    assert "owner" in out
    assert "alice" in out and "bob" in out
    # alert present (the silently-dropped half)
    assert "EDR agent missing" in out
    assert "high" in out
    # fetched via the asset_ids array linkage, not a join table
    assert any("ANY(asset_ids)" in sql for sql in _executed_sql(db))


def test_custom_range_bounds_queries_and_overrides_preset():
    """start/end define an arbitrary window that overrides time_range. Both the
    chronicle and alert queries must be bounded by THAT window — otherwise
    'what changed between Jan and Feb' silently answers the last 30 days."""
    aid = str(uuid4())
    db = _db(_asset_row(aid), [_chronicle_row()], [_alert_row()])
    get_asset_history_impl(
        db, asset_id=aid, time_range="30d",
        start="2026-01-01", end="2026-02-01",
    )
    starts = _windowed_params(db, "start")
    ends = _windowed_params(db, "end")
    assert starts, "no windowed query was issued"
    assert len(starts) == 2  # chronicle + alerts both windowed
    for s in starts:
        assert (s.year, s.month, s.day) == (2026, 1, 1)
    for e in ends:
        assert (e.year, e.month, e.day) == (2026, 2, 1)


def test_custom_end_date_only_is_inclusive_end_of_day():
    """A date-only `end` ('2026-02-01') must cover the whole end day —
    `created_at <= midnight` would silently drop every event ON the end date."""
    aid = str(uuid4())
    db = _db(_asset_row(aid), [], [])
    get_asset_history_impl(db, asset_id=aid, start="2026-01-01",
                           end="2026-02-01")
    ends = _windowed_params(db, "end")
    assert ends
    for e in ends:
        assert (e.year, e.month, e.day) == (2026, 2, 1)
        assert (e.hour, e.minute, e.second) == (23, 59, 59)  # end-of-day


def test_custom_range_rejects_unparseable_date():
    """A bad date must fail loudly without querying — silently falling back to a
    default window answers the wrong question (§4)."""
    aid = str(uuid4())
    db = MagicMock()
    out = get_asset_history_impl(db, asset_id=aid, start="not-a-date")
    assert "not-a-date" in out
    db.execute_query.assert_not_called()


def test_rejects_unknown_sort():
    """An unknown sort silently using the default returns a different order than
    asked, with no signal (§4)."""
    aid = str(uuid4())
    db = MagicMock()
    out = get_asset_history_impl(db, asset_id=aid, time_range="7d", sort="bogus")
    assert "bogus" in out
    db.execute_query.assert_not_called()


def test_search_narrows_to_matching_attribute_events():
    """search filters the merged stream; only the matching attribute change
    survives — the non-matching change AND the alert are excluded."""
    aid = str(uuid4())
    chron = [_chronicle_row(attribute_changes={
        "owner": {"old": ["alice"], "new": ["bob"], "source": "Jamf"},
        "location": {"old": ["nyc"], "new": ["sf"], "source": "Intune"},
    })]
    db = _db(_asset_row(aid), chron, [_alert_row()])
    out = get_asset_history_impl(db, asset_id=aid, time_range="30d",
                                search="owner")
    assert "owner" in out
    assert "location" not in out
    assert "EDR agent missing" not in out


def test_search_matches_alert_title():
    """search must reach alert titles too (HistoryTable searches `title`),
    so 'show me the EDR alert in the history' works."""
    aid = str(uuid4())
    db = _db(_asset_row(aid), [_chronicle_row()], [_alert_row()])
    out = get_asset_history_impl(db, asset_id=aid, time_range="30d",
                                search="edr")
    assert "EDR agent missing" in out
    assert "owner" not in out


def test_sort_by_type_keeps_all_events():
    """A non-date sort must reorder, never drop, events."""
    aid = str(uuid4())
    db = _db(_asset_row(aid), [_chronicle_row()], [_alert_row()])
    out = get_asset_history_impl(db, asset_id=aid, time_range="30d", sort="type")
    assert "EDR agent missing" in out
    assert "owner" in out


def _alert_sql(db):
    """The alert sub-query is the one whose SQL references the asset_ids array
    linkage; isolate it from the asset-exists and chronicle queries."""
    for sql in _executed_sql(db):
        if "ANY(asset_ids)" in sql:
            return sql
    raise AssertionError("no alert query was issued")


def test_active_alert_older_than_window_is_not_dropped():
    """A currently-OPEN alert (status active/acknowledged) is present-state:
    the UI's HistoryTable/EventTimeline shows it even when it was created
    before the requested window. Chronicle field-changes are historical and
    stay windowed, but gating the alert sub-query on `created_at` alone
    silently drops a still-unresolved alert (the DECVST17 / "Stale Assets —
    Retirement Candidates" defect; same class as #735 P2). The alert query
    must therefore OR-in open alerts regardless of created_at — only RESOLVED
    alerts are windowed, because once resolved an alert is a historical
    event."""
    aid = str(uuid4())
    db = _db(_asset_row(aid), [_chronicle_row()], [_alert_row()])
    get_asset_history_impl(db, asset_id=aid, time_range="30d")

    sql = _alert_sql(db)
    # The window predicate must NOT be the sole gate on alerts: an open-alert
    # escape clause keeps unresolved alerts that predate the window. We accept
    # either canonical spelling of "currently open" the product uses elsewhere
    # (alerts.repository: status IN ('active','acknowledged'); equivalently
    # status <> 'resolved').
    norm = " ".join(sql.lower().split())
    assert ("<> 'resolved'" in norm or "!= 'resolved'" in norm
            or "status in ('active', 'acknowledged')" in norm), (
        "alert sub-query gates open alerts on created_at alone, so an active "
        "alert created before the window is silently dropped:\n" + sql)


def test_out_of_window_open_alert_is_labelled_in_output():
    """When the DB returns an open alert whose created_at predates the window
    (because the query OR-includes unresolved alerts), the rendered line must
    flag it as still-open / created-before-window — otherwise a user sees an
    alert dated outside the range they asked for with no explanation."""
    aid = str(uuid4())
    # created_at is ~2 months before a 30d window from "now": squarely outside.
    old_open = _alert_row(
        created_at=datetime(2026, 1, 5, 9, 0, tzinfo=_UTC),
        status="active", title="Stale Assets — Retirement Candidates")
    db = _db(_asset_row(aid), [], [old_open])
    out = get_asset_history_impl(db, asset_id=aid, time_range="30d")

    assert "Stale Assets — Retirement Candidates" in out
    assert "still open" in out.lower()


def test_resolved_alerts_stay_windowed_by_created_at():
    """The fix must NOT widen the window for RESOLVED alerts — those are
    historical events and belong inside the requested window exactly like
    chronicle changes. So the alert query must still bind start/end (the open
    clause is an OR on top of, not a replacement for, the window)."""
    aid = str(uuid4())
    db = _db(_asset_row(aid), [_chronicle_row()], [_alert_row()])
    get_asset_history_impl(db, asset_id=aid, time_range="30d")

    # start/end are bound for BOTH chronicle and alert queries (the window is
    # still applied; it just no longer drops open alerts).
    assert len(_windowed_params(db, "start")) == 2
    assert len(_windowed_params(db, "end")) == 2


def test_asset_not_found_returns_message():
    aid = str(uuid4())
    db = MagicMock()
    db.execute_query.side_effect = [[]]  # asset-exists query returns nothing
    out = get_asset_history_impl(db, asset_id=aid, time_range="7d")
    assert "not found" in out.lower()


def test_no_events_reports_empty_window():
    aid = str(uuid4())
    db = _db(_asset_row(aid), [], [])
    out = get_asset_history_impl(db, asset_id=aid, time_range="7d")
    assert "No changes or alerts" in out
