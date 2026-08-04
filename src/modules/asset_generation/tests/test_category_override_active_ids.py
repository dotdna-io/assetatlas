# SPDX-License-Identifier: AGPL-3.0-or-later
"""`get_active_override_ids` — the durable answer to "is this overridden?" (#1270).

`assets.category_is_overridden` is a best-effort cache that every pipeline run
with work resets to `false`. `category_overrides` is the record that survives:
`asset_id` PK, an `active` boolean, no FK to `assets`, so `TRUNCATE assets
CASCADE` leaves it intact. This is the lookup the clear path keys on.

The DB is a MagicMock, so — per `.claude/rules/backend.md` — the WHERE clause is
asserted directly. `active = true` is the entire behaviour of this method; a
mock returning rows proves nothing about whether it was there.
"""

import uuid
from unittest.mock import MagicMock

import pytest

from src.modules.asset_generation.repositories.category_override_repository import (
    CategoryOverrideRepository,
)


@pytest.fixture
def repo():
    return CategoryOverrideRepository(db_adapter=MagicMock())


def _sql(repo):
    return " ".join(repo.db.execute_query.call_args[0][0].split())


class TestGetActiveOverrideIds:

    def test_returns_the_matching_ids_as_a_set_of_strings(self, repo):
        """The caller composes this against `asset_ids: List[str]`, so a UUID
        object coming back from psycopg2 would silently match nothing."""
        found = uuid.uuid4()
        repo.db.execute_query.return_value = [{'asset_id': found}]

        result = repo.get_active_override_ids([str(found), str(uuid.uuid4())])

        assert result == {str(found)}
        assert all(isinstance(i, str) for i in result)

    def test_only_active_rows_count(self, repo):
        """A cleared override leaves its row behind with `active = false`.
        Without this predicate every asset ever overridden reads as overridden
        forever."""
        repo.get_active_override_ids(['a1'])

        assert 'active = true' in _sql(repo)

    def test_it_filters_to_the_requested_ids(self, repo):
        repo.get_active_override_ids(['a1', 'a2'])

        assert 'ANY(%(ids)s::uuid[])' in _sql(repo)
        assert repo.db.execute_query.call_args[0][1]['ids'] == ['a1', 'a2']

    def test_ids_are_stringified_for_the_uuid_cast(self, repo):
        asset_id = uuid.uuid4()

        repo.get_active_override_ids([asset_id])

        assert repo.db.execute_query.call_args[0][1]['ids'] == [str(asset_id)]

    def test_an_empty_request_never_reaches_the_database(self, repo):
        assert repo.get_active_override_ids([]) == set()
        repo.db.execute_query.assert_not_called()

    def test_no_matches_is_an_empty_set(self, repo):
        repo.db.execute_query.return_value = []

        assert repo.get_active_override_ids(['a1']) == set()

    def test_a_database_error_propagates(self, repo):
        """Deliberately NOT the degrade-to-empty contract of `get_all_overrides`
        / `get_override_count`.

        Those run on read paths, where an empty result is a survivable
        approximation. This one is called inside `clear_overrides_bulk`'s
        `_bulk_scope()` write transaction, where returning empty means "no asset
        carried an override" — indistinguishable from success. A swallowed error
        leaves the transaction INERROR without setting the scope's `errored`
        flag, so `end_scope()` rolls back with only a warning (it passes no
        `raise_on_aborted`, unlike the pipeline orchestrator) and the caller gets
        HTTP 200 with `{"cleared": 0}` for an operation that failed outright —
        #813's shape, and #1270's.

        Propagating turns that into the 500 it is.
        """
        repo.db.execute_query.side_effect = RuntimeError("connection lost")

        with pytest.raises(RuntimeError):
            repo.get_active_override_ids(['a1'])
