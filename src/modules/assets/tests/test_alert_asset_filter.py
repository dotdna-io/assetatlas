# SPDX-License-Identifier: AGPL-3.0-or-later
"""Filtering the asset list by the alert that names the assets (#907).

An alert stores its assets as `alerts.asset_ids uuid[]`. The Explorer drilldown
resolves that array SERVER-side from one alert uuid, which is the whole design:
the ids never travel to the client and back, so the alert naming 35,136 assets on
the dev DB links exactly like one naming three. The superseded proposal shipped
the ids in the URL and therefore needed a length cap, a ~100-asset threshold and a
short-lived-filter-id fallback; none of that exists because none of it has to.

What these tests hold, and what breaks if they go:

- The scope NARROWS, never widens. It ANDs with the user's filters even when
  those filters OR among themselves — put the alert clause inside the OR group
  and an alert drilldown starts listing assets the alert never named, which is
  the one thing the feature promises not to do.
- The id reaches SQL as a bound `::uuid` param. Interpolate it and a client typo
  becomes a psycopg2 error `handle_errors` cannot read as a ValueError, so the
  400 the blueprint earns turns back into the 500 that #973/#999 fixed.
- No alert_id leaves the listing untouched, so the ordinary Explorer never pays
  for a join it did not ask for.
"""

from unittest.mock import MagicMock

from src.modules.assets.repositories.asset_repository import AssetRepository

ALERT = "948e3f69-818b-4649-8f77-362048686475"

# The canonical membership test, spelled as the MCP alerts tool spells it in the
# other direction (src/mcp/tools/alerts.py). `id = ANY((SELECT asset_ids ...))`
# is NOT an alternative: Postgres parses a parenthesised SELECT after ANY as a
# sublink however it is nested, so that spelling dies on
# `operator does not exist: uuid = uuid[]` — verified against the dev DB.
MEMBERSHIP = "assets.id = ANY(al.asset_ids)"


def _repo():
    """Repository over a mock adapter, capturing every (sql, params) it issues.

    The SELECT list and ORDER BY are stubbed: both are FieldResolver-driven and
    orthogonal to the WHERE clause under test here.
    """
    db = MagicMock()
    db.execute_query.return_value = [{"total": 0}]
    repo = AssetRepository(db, MagicMock())
    repo._build_select_columns = MagicMock(return_value="id")
    repo._build_order_by = MagicMock(return_value="")
    return repo, db


def _queries(db):
    return [(c.args[0], c.args[1]) for c in db.execute_query.call_args_list]


def test_alert_id_narrows_the_listing_to_that_alerts_assets():
    repo, db = _repo()
    repo.list_assets(alert_id=ALERT)

    # Both the count and the page must carry the scope: if only the page did,
    # the table would show the alert's assets above a total for the whole fleet.
    assert len(_queries(db)) == 2
    for sql, params in _queries(db):
        assert MEMBERSHIP in sql
        assert "FROM alerts al" in sql
        assert params["alert_id"] == ALERT


def test_the_alert_id_is_bound_and_cast_never_interpolated():
    # The ::uuid cast is what makes a typo Postgres's problem rather than
    # Python's, and psycopg2's error is not a ValueError — so the id has to
    # arrive as a bound param the driver escapes, with the cast in the SQL.
    repo, db = _repo()
    repo.list_assets(alert_id=ALERT)

    for sql, params in _queries(db):
        assert "%(alert_id)s::uuid" in sql
        assert ALERT not in sql          # value travels in params, not the text
        assert params["alert_id"] == ALERT


def test_only_the_alert_id_travels_however_many_assets_it_names():
    # The point of filtering by alert rather than by a list of asset ids: the
    # bound params carry ONE uuid, so cost is flat in the alert's size. If a
    # future change resolves asset_ids client-side and passes them down, this
    # fails — which is the intended alarm, not an inconvenience.
    repo, db = _repo()
    repo.list_assets(alert_id=ALERT)

    for _sql, params in _queries(db):
        alert_scoped = {k: v for k, v in params.items() if k not in ("limit", "offset")}
        assert alert_scoped == {"alert_id": ALERT}


def test_alert_scope_ands_with_filters_that_or_among_themselves():
    # The narrowing rule. `logic="or"` collapses the user's filters into one
    # parenthesised OR group; the alert clause must sit OUTSIDE it. Were it
    # inside, `alert OR category=server` would list every server in the fleet
    # under a chip claiming to show one alert's assets.
    repo, db = _repo()
    repo._build_filter_clauses = MagicMock(
        return_value=(["category = %(f0)s", "os_normalized = %(f1)s"], {"f0": "a", "f1": "b"})
    )
    repo.list_assets(
        alert_id=ALERT,
        filters=[{"field_id": {}, "op": "eq", "value": "a"}],
        logic="or",
    )

    sql, _params = _queries(db)[0]
    where = sql.split("WHERE", 1)[1]
    # The OR group is parenthesised and joined to the alert clause with AND.
    assert "(category = %(f0)s OR os_normalized = %(f1)s)" in where
    assert " AND " in where
    assert where.index(MEMBERSHIP) < where.index("OR")
    # The membership test must not have been swallowed into the OR group.
    or_group = where[where.index("(category"):]
    assert MEMBERSHIP not in or_group


def test_without_an_alert_id_the_listing_is_untouched():
    # The ordinary Explorer must not pay for, or be narrowed by, a join it
    # never asked for.
    repo, db = _repo()
    repo.list_assets()

    for sql, params in _queries(db):
        assert "alerts" not in sql
        assert "alert_id" not in params


def test_both_service_list_paths_forward_the_alert_id():
    # GET /assets serves the Explorer via list_assets_dynamic (it sends
    # ?fields=) and everything else via list_assets. A path that dropped the
    # param would silently answer an alert-scoped request with the whole
    # fleet — a filter that quietly does nothing is worse than one that errors.
    from src.modules.assets.services.asset_service import AssetService

    repo = MagicMock()
    repo.list_assets.return_value = ([], 0)
    svc = AssetService(repo)
    svc._get_confidence_thresholds = MagicMock(return_value=(0.5, 0.8))

    svc.list_assets_dynamic(fields=["static:Hostname"], alert_id=ALERT)
    assert repo.list_assets.call_args.kwargs["alert_id"] == ALERT

    repo.list_assets.reset_mock()
    svc.list_assets(alert_id=ALERT)
    assert repo.list_assets.call_args.kwargs["alert_id"] == ALERT


# ---------------------------------------------------------------------------
# get_matching_asset_ids — the bulk target-set resolver (#1011)
#
# The Explorer count and a bulk action's target set are resolved by two DIFFERENT
# queries: the count comes from list_assets (which honours the alert scope), the
# targets from get_matching_asset_ids (which did NOT). Under an alert drilldown
# with no other filters that gap is silent and wrong — the count beside the button
# reads the alert's size while the write lands on a wider set. These tests hold
# that get_matching_asset_ids narrows on alert_id EXACTLY as list_assets does, so
# the set the write touches is the set the count promised.
# ---------------------------------------------------------------------------

def _ids_repo():
    """Like _repo(), but the mock returns id rows (get_matching_asset_ids maps
    r['id']); the [{'total': 0}] default has no 'id' key."""
    db = MagicMock()
    db.execute_query.return_value = []
    repo = AssetRepository(db, MagicMock())
    return repo, db


def test_get_matching_asset_ids_narrows_to_that_alerts_assets():
    repo, db = _ids_repo()
    repo.get_matching_asset_ids(alert_id=ALERT)

    sql, params = _queries(db)[0]
    assert MEMBERSHIP in sql
    assert "FROM alerts al" in sql
    assert params["alert_id"] == ALERT


def test_get_matching_asset_ids_alert_id_is_bound_and_cast_never_interpolated():
    # Same contract the listing carries: the ::uuid cast makes a typo Postgres's
    # problem, but only if the id arrives as a bound param — interpolate it and a
    # bad id becomes a psycopg2 error handle_errors answers 500, not the 400 the
    # blueprint earns via ensure_uuid (#973/#999).
    repo, db = _ids_repo()
    repo.get_matching_asset_ids(alert_id=ALERT)

    sql, params = _queries(db)[0]
    assert "%(alert_id)s::uuid" in sql
    assert ALERT not in sql
    assert params["alert_id"] == ALERT


def test_get_matching_asset_ids_alert_scope_ands_with_filters_that_or_among_themselves():
    # The narrowing rule, for the bulk path. logic="or" collapses the user's
    # filters into one parenthesised OR group; the alert clause must sit OUTSIDE
    # it. Were it inside, a bulk action under "alert OR category=server" would
    # resolve every server in the fleet as its target set — the exact silent
    # widening #1011 is about.
    repo, db = _ids_repo()
    repo._build_filter_clauses = MagicMock(
        return_value=(["category = %(f0)s", "os_normalized = %(f1)s"], {"f0": "a", "f1": "b"})
    )
    repo.get_matching_asset_ids(
        alert_id=ALERT,
        filters=[{"field_id": {}, "op": "eq", "value": "a"}],
        logic="or",
    )

    sql, _params = _queries(db)[0]
    where = sql.split("WHERE", 1)[1]
    assert "(category = %(f0)s OR os_normalized = %(f1)s)" in where
    assert where.index(MEMBERSHIP) < where.index("OR")
    or_group = where[where.index("(category"):]
    assert MEMBERSHIP not in or_group


def test_get_matching_asset_ids_service_forwards_the_alert_id():
    # The bulk resolver's service seam. _resolve_bulk_ids calls this; a path that
    # dropped alert_id would resolve the whole fleet under an alert drilldown.
    from src.modules.assets.services.asset_service import AssetService

    repo = MagicMock()
    repo.get_matching_asset_ids.return_value = []
    svc = AssetService(repo)
    svc._get_confidence_thresholds = MagicMock(return_value=(0.5, 0.8))

    svc.get_matching_asset_ids(filters=[], alert_id=ALERT, limit=1001)
    assert repo.get_matching_asset_ids.call_args.kwargs["alert_id"] == ALERT


# ---------------------------------------------------------------------------
# aggregate_assets — the pivot-table matrix (#1013)
#
# The Explorer pivot export batch-fetches GET /assets/aggregate and turns the
# matrix into CSV client-side. Under an alert drilldown the table beside it shows
# only the alert's assets, so the pivot must aggregate that same set — without an
# alert scope on aggregate_assets it summed the WHOLE fleet under a scoped chip,
# the silently-wider export the row buttons were disabled to prevent (#1011).
# These tests hold that aggregate_assets narrows on alert_id EXACTLY as
# list_assets does, so the matrix counts the set the drilldown promised.
# ---------------------------------------------------------------------------

def _agg_repo():
    """Repository shaped for aggregate_assets: an empty result set (so it returns
    before the row post-processing that reads 'row_val' keys) and a resolver that
    resolves the row field to a scalar column — only the WHERE clause is under
    test here, as in the list/ids cases above."""
    db = MagicMock()
    db.execute_query.return_value = []
    resolver = MagicMock()
    resolved = MagicMock()
    resolved.where_expr.return_value = "hostname"
    resolved.is_native_array.return_value = False
    resolver.resolve.return_value = resolved
    repo = AssetRepository(db, resolver)
    return repo, db


def test_aggregate_assets_narrows_to_that_alerts_assets():
    repo, db = _agg_repo()
    repo.aggregate_assets(row_field="static:Hostname", alert_id=ALERT)

    sql, params = _queries(db)[0]
    assert MEMBERSHIP in sql
    assert "FROM alerts al" in sql
    assert params["alert_id"] == ALERT


def test_aggregate_assets_alert_id_is_bound_and_cast_never_interpolated():
    # Same contract the listing carries: the ::uuid cast makes a typo Postgres's
    # problem, but only if the id arrives as a bound param — interpolate it and a
    # bad id becomes a psycopg2 error handle_errors answers 500, not the 400 the
    # blueprint earns via ensure_uuid (#973/#999).
    repo, db = _agg_repo()
    repo.aggregate_assets(row_field="static:Hostname", alert_id=ALERT)

    sql, params = _queries(db)[0]
    assert "%(alert_id)s::uuid" in sql
    assert ALERT not in sql
    assert params["alert_id"] == ALERT


def test_aggregate_assets_alert_scope_ands_with_filters_that_or_among_themselves():
    # The narrowing rule, for the pivot path. logic="or" collapses the user's
    # filters into one parenthesised OR group; the alert clause must sit OUTSIDE
    # it. Were it inside, a pivot under "alert OR category=server" would tally
    # every server in the fleet into the matrix — the exact silent widening the
    # feature promises not to do.
    repo, db = _agg_repo()
    repo._build_filter_clauses = MagicMock(
        return_value=(["category = %(f0)s", "os_normalized = %(f1)s"], {"f0": "a", "f1": "b"})
    )
    repo.aggregate_assets(
        row_field="static:Hostname",
        filters=[{"field_id": {}, "op": "eq", "value": "a"}],
        logic="or",
        alert_id=ALERT,
    )

    sql, _params = _queries(db)[0]
    where = sql.split("WHERE", 1)[1]
    assert "(category = %(f0)s OR os_normalized = %(f1)s)" in where
    assert where.index(MEMBERSHIP) < where.index("OR")
    or_group = where[where.index("(category"):]
    assert MEMBERSHIP not in or_group


def test_aggregate_assets_service_forwards_the_alert_id():
    # The pivot path's service seam. The blueprint hands aggregate_assets an
    # alert_id; the repo takes it keyword-only, so a service that forwarded
    # row/col/filters positionally but dropped alert_id would aggregate the whole
    # fleet under a scoped chip — a filter that quietly does nothing, worse than
    # one that errors.
    from src.modules.assets.services.asset_service import AssetService

    repo = MagicMock()
    repo.aggregate_assets.return_value = {}
    svc = AssetService(repo)

    svc.aggregate_assets(row_field="static:Hostname", alert_id=ALERT)
    assert repo.aggregate_assets.call_args.kwargs["alert_id"] == ALERT
