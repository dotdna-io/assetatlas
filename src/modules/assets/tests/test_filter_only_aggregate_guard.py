# SPDX-License-Identifier: AGPL-3.0-or-later
"""A filter-only field must never reach GROUP BY.

Grouping by such a field evaluates a correlated subquery once per asset row —
measured as >180s (aborted) against the dev DB's 73,740 assets, versus 71ms for
the same question asked from the alerts side. The UI cannot request it, because
filter-only entries are absent from the payload the pivot pickers read; this
guard closes the hand-crafted-request path, exactly as the existing
unresolvable-field branch beside it does.
"""

import pytest
from unittest.mock import MagicMock

from src.modules.assets.repositories.asset_repository import AssetRepository
from src.modules.fields import InvalidFieldError

EMPTY = {'rows': [], 'columns': [], 'data': {}, 'grand_total': 0}


def _repo():
    db = MagicMock()
    db.execute_query.return_value = []
    return AssetRepository(db, MagicMock()), db


def test_a_filter_only_row_field_returns_empty_and_issues_no_query():
    repo, db = _repo()
    assert repo.aggregate_assets(row_field="virtual:alert") == EMPTY
    assert db.execute_query.call_count == 0


def test_a_filter_only_col_field_returns_empty_and_issues_no_query():
    repo, db = _repo()
    result = repo.aggregate_assets(
        row_field="static:Category", col_field="virtual:alert"
    )
    assert result == EMPTY
    assert db.execute_query.call_count == 0


def test_ordinary_fields_still_aggregate():
    # The guard must be narrow — a regression here breaks every pivot.
    repo, db = _repo()
    repo.aggregate_assets(row_field="static:Category")
    assert db.execute_query.call_count > 0


def test_row_field_resolve_value_error_returns_empty_and_issues_no_query():
    # resolve() can transitively raise a bare ValueError (unified/merge_sql.py
    # build_merge_sql, for a stored field whose unified_function isn't one it
    # handles), not just InvalidFieldError. Pin the pre-existing graceful-empty
    # contract so a narrowed except can't let it escape uncaught again.
    db = MagicMock()
    db.execute_query.return_value = []
    resolver = MagicMock()
    resolver.resolve.side_effect = ValueError("unknown function: bogus")
    repo = AssetRepository(db, resolver)

    assert repo.aggregate_assets(row_field="static:Category") == EMPTY
    assert db.execute_query.call_count == 0


def test_get_field_values_resolve_value_error_returns_empty_and_issues_no_query():
    # Same resolve()-can-raise-bare-ValueError contract as the row-field case
    # above, pinned for get_field_values' independent try/except around
    # resolve().
    db = MagicMock()
    db.execute_query.return_value = []
    resolver = MagicMock()
    resolver.resolve.side_effect = ValueError("unknown function: bogus")
    repo = AssetRepository(db, resolver)

    assert repo.get_field_values(field="static:Category") == ([], 0)
    assert db.execute_query.call_count == 0


def test_a_filter_only_field_cannot_be_sorted():
    # ORDER BY is the third whole-table path (with pivot and /values) that
    # evaluates the correlated subquery once per matching row before LIMIT —
    # measured >180s. sort_by=virtual:alert passes Pydantic (decode succeeds)
    # and resolve() succeeds (the field is live, only hidden from the picker),
    # so without this guard a hand-crafted sort hangs a worker. InvalidFieldError
    # is caught by the dynamic-list handler as a 400. The raise must happen
    # BEFORE resolve(), so a MagicMock resolver never gets consulted.
    repo = AssetRepository(MagicMock(), MagicMock())
    with pytest.raises(InvalidFieldError):
        repo._build_order_by("virtual:alert", "asc")


def test_the_default_and_ordinary_sorts_are_unaffected():
    # The guard must be narrow — a regression here breaks every sorted page.
    repo = AssetRepository(MagicMock(), MagicMock())
    assert repo._build_order_by(None, None) == "ORDER BY last_time DESC NULLS LAST, id ASC"
