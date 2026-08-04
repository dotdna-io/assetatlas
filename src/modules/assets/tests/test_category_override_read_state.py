# SPDX-License-Identifier: AGPL-3.0-or-later
"""The edit modal's read of "is this asset reclassified, and to what?" (#1155).

Reopening the Reclassify tab on an asset someone overrode has to say so, name
who did it, and show what the pipeline predicted — otherwise a manual value is
indistinguishable from a pipeline one and there is nothing to restore *to*.

The whole feature turns on asking the right table. `assets.category_is_overridden`
reads `false` on every override older than one pipeline run — the rebuild is
`TRUNCATE assets CASCADE` + plain INSERT and the column is absent from
`bulk_upsert_assets`' explicit list, so it takes its DB DEFAULT — and it was
never set at all by the single-asset path before #1270. A read keyed on it would
report "not overridden" for almost every overridden asset, and the tab would look
exactly as broken as it does today. `category_overrides.active` is the durable
record: `asset_id` PK, no FK to `assets`, so it survives the rebuild.

The DB is a MagicMock here, so — per `.claude/rules/backend.md` — these assert
the generated SQL as well as the returned dict. That is not ceremony: the
`active = true` filter and the absence of the live flag both live entirely
inside SQL strings, and a mock returns whatever row it was handed regardless of
what the WHERE clause says. The repository is REAL over the mock adapter for the
same reason — stubbing it would assert the test's own SQL, not production's.

Lives under src/modules/assets/tests/ deliberately: that path is in the
"pipeline + delivery domain (hard gate)" required check, whereas the blueprint
tests under tests/unit/ run with continue-on-error and cannot fail the build.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.modules.asset_generation.repositories.category_override_repository import (
    CategoryOverrideRepository,
)
from src.modules.assets.services.reclassify_service import ReclassifyService


ASSET_ID = '11111111-2222-3333-4444-555555555555'
OVERRIDDEN_AT = datetime(2026, 7, 14, 9, 30, tzinfo=timezone.utc)

# What `category_overrides` holds for an asset an analyst reclassified. Every
# field the modal displays as attribution is already here — no new schema.
OVERRIDE_ROW = {
    'asset_id': ASSET_ID,
    'category': 'network',
    'subcategory': 'switch',
    'reason': 'Mislabelled by the OUI fallback',
    'overridden_by': 'jsmith',
    'overridden_at': OVERRIDDEN_AT,
}

# The live row: showing network/switch because the override was applied, with
# the pipeline's own answer frozen in the *_predicted pair (#1154). No
# `category_is_overridden` — `_fetch_current_categories` does not select it, and
# a fixture carrying it would imply this path has an opinion about it (#1270).
ASSET_ROW = {
    'id': ASSET_ID,
    'category': 'network',
    'subcategory': 'switch',
    'category_predicted': 'server',
    'subcategory_predicted': 'linux',
}


def _svc(override_row=OVERRIDE_ROW, asset_row=ASSET_ROW):
    """A ReclassifyService whose DB is a mock (__new__ skips the catalog load).

    The override repository is the real one over that same mock adapter, so both
    statements this path issues land in one call log and the SQL asserted below
    is the SQL production runs. `override_row=None` means `category_overrides`
    has nothing active for the asset; `asset_row=None` means no such asset.
    """
    svc = ReclassifyService.__new__(ReclassifyService)
    svc.db = MagicMock()
    svc.override_repo = CategoryOverrideRepository(db_adapter=svc.db)
    svc.custom_subcategory_repo = MagicMock()
    svc._catalog = [{"name": "network", "subcategories": ["switch"]}]

    def _execute(query, params=None, fetch=False):
        if 'category_overrides' in query:
            return [dict(override_row)] if override_row else []
        return [dict(asset_row)] if asset_row else []

    svc.db.execute_query.side_effect = _execute
    return svc


def _statements(db):
    """Every statement issued, whitespace-collapsed so reformatting is free."""
    return [" ".join(c.args[0].split()) for c in db.execute_query.call_args_list]


def _the(db, needle):
    """The one statement containing `needle` — asserts it is unambiguous."""
    matches = [s for s in _statements(db) if needle in s]
    assert len(matches) == 1, f"expected exactly one {needle!r} statement, got {matches}"
    return matches[0]


class TestTheDurableRecordIsWhatAnswers:
    """The issue's regression, at the layer that decides it."""

    def test_an_override_whose_flag_a_pipeline_run_reset_still_reads_as_overridden(self):
        """The asset row here is the steady state of every override older than
        one pipeline run: the live values still carry the correction, and the
        live flag has been reset to false by the rebuild. Only the override row
        can say the asset was reclassified, so only it is asked (the SQL
        assertions below are what pin that — this one pins the answer)."""
        svc = _svc()

        state = svc.get_override_state(ASSET_ID)

        assert state['is_overridden'] is True

    def test_a_deactivated_override_reads_as_not_overridden(self):
        """Clearing deactivates the row rather than deleting it, so "a row
        exists" is not the question — "an ACTIVE row exists" is."""
        svc = _svc(override_row=None)

        state = svc.get_override_state(ASSET_ID)

        assert state['is_overridden'] is False
        assert state['overridden_by'] is None
        assert state['reason'] is None

    def test_the_lookup_asks_only_for_active_rows(self):
        """The filter lives in the WHERE clause, and a MagicMock hands back the
        row either way — so assert the clause, not the return value. Without it
        every asset that was ever overridden reads as overridden forever."""
        svc = _svc()

        svc.get_override_state(ASSET_ID)

        sql = _the(svc.db, 'FROM category_overrides')
        assert 'active = true' in sql

    def test_the_lookup_is_scoped_to_the_asset(self):
        svc = _svc()

        svc.get_override_state(ASSET_ID)

        sql = _the(svc.db, 'FROM category_overrides')
        assert 'asset_id = %(asset_id)s::uuid' in sql
        params = [
            c.args[1] for c in svc.db.execute_query.call_args_list
            if 'category_overrides' in c.args[0]
        ]
        assert params == [{'asset_id': ASSET_ID}]

    def test_no_statement_on_this_path_reads_the_live_flag(self):
        """The one line that makes this feature correct, stated over the whole
        path rather than one statement: `category_is_overridden` is a
        best-effort cache the pipeline resets, and a read that consults it is
        wrong even when it happens to agree."""
        svc = _svc()

        svc.get_override_state(ASSET_ID)

        assert not [s for s in _statements(svc.db) if 'category_is_overridden' in s]


class TestAFailedLookupIsNotReportedAsCleanState:
    """`get_all_overrides` degrades to `{}` on error; this read must not.

    Swallowing here renders as "this asset was never reclassified" — the tab
    then hides the indicator and the restore control on an asset that has both,
    and the user is told a falsehood with a 200. A 500 is the honest answer.
    """

    def test_the_failure_propagates(self):
        svc = _svc()
        svc.db.execute_query.side_effect = RuntimeError("timeout")

        with pytest.raises(RuntimeError):
            svc.get_override_state(ASSET_ID)


class TestWhatTheModalDisplays:
    """Attribution and the restore target, straight off the two rows."""

    def test_attribution_comes_from_the_override_row(self):
        svc = _svc()

        state = svc.get_override_state(ASSET_ID)

        assert state['overridden_by'] == 'jsmith'
        assert state['reason'] == 'Mislabelled by the OUI fallback'

    def test_the_timestamp_is_handed_over_unformatted(self):
        """The blueprint isoformats it, the way get_label_override does — the
        service stays in datetimes so a non-HTTP caller isn't handed a string to
        re-parse."""
        svc = _svc()

        state = svc.get_override_state(ASSET_ID)

        assert state['overridden_at'] == OVERRIDDEN_AT

    def test_both_predicted_values_come_from_the_assets_row(self):
        """What "Restore predicted" restores to, and what the tab prints beside
        the offer. Blind reverts are what #1154 was about."""
        svc = _svc()

        state = svc.get_override_state(ASSET_ID)

        assert state['category_predicted'] == 'server'
        assert state['subcategory_predicted'] == 'linux'

    def test_the_snapshot_query_reads_both_predicted_columns(self):
        svc = _svc()

        svc.get_override_state(ASSET_ID)

        # 'SELECT id' rather than 'SELECT': the override lookup carries a
        # SELECT of its own.
        sql = _the(svc.db, 'SELECT id')
        assert 'category_predicted' in sql
        assert 'subcategory_predicted' in sql

    def test_a_legacy_row_reports_no_prediction_rather_than_inventing_one(self):
        """Rows written before migration 143 have no snapshot. The tab has to be
        able to tell the user that restoring will not change the values now —
        which it can only do if this read says so instead of guessing."""
        svc = _svc(asset_row={**ASSET_ROW, 'category_predicted': None,
                              'subcategory_predicted': None})

        state = svc.get_override_state(ASSET_ID)

        assert state['is_overridden'] is True
        assert state['category_predicted'] is None
        assert state['subcategory_predicted'] is None

    def test_an_unknown_asset_still_returns_a_well_formed_body(self):
        """Same contract as get_label_override: a shape the client can render,
        not a 404 it has to special-case."""
        svc = _svc(override_row=None, asset_row=None)

        state = svc.get_override_state(ASSET_ID)

        assert state == {
            'asset_id': ASSET_ID,
            'is_overridden': False,
            'overridden_by': None,
            'overridden_at': None,
            'reason': None,
            'category_predicted': None,
            'subcategory_predicted': None,
        }


class TestTheDisplayedAttributionPairStaysConsistent:
    """`overridden_by` and `overridden_at` are rendered as one line — "<date> by
    <user>" — so they have to move together.

    `asset_id` is the PK, so re-reclassifying an asset REPLACES its override via
    `ON CONFLICT DO UPDATE`. That clause moved `overridden_by` to the new author
    but left `overridden_at` at the value the original INSERT's column default
    wrote, so an asset overridden by alice in January and re-overridden by bob in
    August read "2026-01-05 by bob": one person's correction stamped with another
    person's date. Inert until something displayed the pair; #1155 is that
    something.

    Asserted on the generated SQL because that is where the whole behaviour lives
    — a MagicMock DB never applies an ON CONFLICT clause, so a returns-True test
    passes against either version. Rows written before this fix keep their stale
    stamp; correcting those would need a backfill migration and is not attempted.
    """

    def _upsert_sql(self, call):
        return " ".join(call.args[0].split())

    def test_the_single_asset_upsert_restamps_the_time_with_the_author(self):
        db = MagicMock()
        repo = CategoryOverrideRepository(db_adapter=db)

        repo.save_override(asset_id=ASSET_ID, category='network', subcategory='switch',
                           overridden_by='bob')

        sql = self._upsert_sql(db.execute_query.call_args)
        conflict = sql.split('DO UPDATE SET', 1)[1]
        assert 'overridden_by = EXCLUDED.overridden_by' in conflict
        assert 'overridden_at = CURRENT_TIMESTAMP' in conflict

    def test_the_bulk_upsert_restamps_the_time_with_the_author(self):
        db = MagicMock()
        # The bulk path batches through psycopg2's execute_values rather than
        # execute_query, so the statement has to be caught there. Unpacking
        # `as (cursor, conn)` needs a real 2-tuple — a bare MagicMock yields
        # nothing and the `with` raises before any SQL is built.
        db.conn_manager.get_cursor.return_value.__enter__.return_value = (
            MagicMock(), MagicMock(),
        )
        repo = CategoryOverrideRepository(db_adapter=db)

        with patch(
            'src.modules.asset_generation.repositories'
            '.category_override_repository.extras.execute_values'
        ) as execute_values:
            repo.save_overrides_bulk(asset_ids=[ASSET_ID], category='network',
                                     subcategory='switch', overridden_by='bob')

        sql = " ".join(execute_values.call_args.args[1].split())
        conflict = sql.split('DO UPDATE SET', 1)[1]
        assert 'overridden_by = EXCLUDED.overridden_by' in conflict
        assert 'overridden_at = CURRENT_TIMESTAMP' in conflict

    def test_neither_upsert_can_move_the_author_without_the_time(self):
        """Stated as an implication so it survives a rewrite of either statement:
        whichever clause updates the author must update the timestamp too."""
        import inspect

        for method in (CategoryOverrideRepository.save_override,
                       CategoryOverrideRepository.save_overrides_bulk):
            body = inspect.getsource(method)
            clause = body.split('DO UPDATE SET', 1)[1]
            moves_author = 'overridden_by = EXCLUDED.overridden_by' in clause
            moves_time = 'overridden_at = CURRENT_TIMESTAMP' in clause
            assert not moves_author or moves_time, (
                f"{method.__name__} reassigns overridden_by without restamping "
                f"overridden_at — the modal would show the new author against "
                f"the old date"
            )


class TestTheRepositoryReadInIsolation:
    """`get_active_override` is the durable-record lookup on its own."""

    def test_it_returns_the_row_when_one_is_active(self):
        db = MagicMock()
        db.execute_query.return_value = [dict(OVERRIDE_ROW)]

        row = CategoryOverrideRepository(db_adapter=db).get_active_override(ASSET_ID)

        assert row['overridden_by'] == 'jsmith'

    def test_it_returns_none_when_nothing_is_active(self):
        db = MagicMock()
        db.execute_query.return_value = []

        row = CategoryOverrideRepository(db_adapter=db).get_active_override(ASSET_ID)

        assert row is None

    def test_it_selects_every_field_the_modal_attributes_with(self):
        db = MagicMock()
        db.execute_query.return_value = []

        CategoryOverrideRepository(db_adapter=db).get_active_override(ASSET_ID)

        sql = " ".join(db.execute_query.call_args.args[0].split())
        for column in ('overridden_by', 'overridden_at', 'reason'):
            assert column in sql, f"{column} is displayed by the tab but not selected"

    def test_a_uuid_typed_id_is_accepted(self):
        """The blueprint canonicalises to a string, but the pipeline side of
        this repository passes UUID objects around."""
        from uuid import UUID

        db = MagicMock()
        db.execute_query.return_value = []

        CategoryOverrideRepository(db_adapter=db).get_active_override(UUID(ASSET_ID))

        assert db.execute_query.call_args.args[1] == {'asset_id': ASSET_ID}
