# SPDX-License-Identifier: AGPL-3.0-or-later
"""Value suggestions for the `Alert` search token.

The dropdown must be answered from `alerts` (50 rows) and never from `assets`:
the catalog expression for this field is a correlated subquery, so the generic
DISTINCT-over-every-asset path does not return in any usable time.

Only OPEN alerts are offered, because only an open alert is what
`Alert = "<name>"` can match — suggesting a resolved one would hand the user a
token that answers zero assets.
"""

from unittest.mock import MagicMock

from src.modules.assets.repositories.asset_repository import AssetRepository


def _repo(rows):
    db = MagicMock()
    db.execute_query.return_value = rows
    return AssetRepository(db, MagicMock()), db


def _sql(db):
    return db.execute_query.call_args.args[0]


def _params(db):
    return db.execute_query.call_args.args[1]


def test_alert_values_come_from_alerts_never_from_assets():
    repo, db = _repo([{"value": "Data Source Stale", "count": 12}])
    values, total = repo.get_field_values("virtual:alert", limit=30)

    assert values == [{"value": "Data Source Stale", "count": 12}]
    assert total == 1
    assert "FROM alerts" in _sql(db)
    assert "FROM assets" not in _sql(db)


def test_only_open_alerts_are_suggested():
    repo, db = _repo([])
    repo.get_field_values("virtual:alert", limit=30)
    assert "status IN ('active','acknowledged')" in _sql(db)


def test_alerts_naming_no_assets_are_not_suggested():
    # integration / audit_event / pipeline_run alerts carry no asset_ids, so
    # their names are tokens that can only ever answer zero assets.
    repo, db = _repo([])
    repo.get_field_values("virtual:alert", limit=30)
    assert "asset_count > 0" in _sql(db)


def test_the_search_term_is_bound_never_interpolated():
    repo, db = _repo([])
    repo.get_field_values("virtual:alert", limit=30, search="stale")
    assert "stale" not in _sql(db)
    assert _params(db)["val_search"] == "%stale%"


def test_the_limit_is_bound():
    repo, db = _repo([])
    repo.get_field_values("virtual:alert", limit=7)
    assert _params(db)["limit"] == 7


def test_other_fields_still_use_the_generic_path():
    # The branch must be narrow: a regression here silently breaks every other
    # column-filter dropdown in the Explorer.
    repo, db = _repo([])
    repo.get_field_values("static:Hostname", limit=30)
    assert "FROM assets" in _sql(db)
