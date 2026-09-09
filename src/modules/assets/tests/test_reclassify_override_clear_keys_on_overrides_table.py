# SPDX-License-Identifier: AGPL-3.0-or-later
"""Clearing a category override keys on `category_overrides`, not the flag (#1270).

`assets.category_is_overridden` cannot decide whether an asset carries an
override, in either direction it is used:

* every pipeline run with work resets it to `false` — the rebuild is
  `TRUNCATE assets CASCADE` + plain INSERT, and the column is absent from
  `bulk_upsert_assets`' explicit list, so it takes its DB DEFAULT;
* `_update_asset` (the single-asset path) never set it at all, so an override
  made there read as `false` from the moment it was created.

Both made `clear_overrides_bulk` a silent no-op: `overridden_ids` came out
empty, `deactivate_overrides_bulk` was never called, the override row stayed
active, and the UI reported success. `category_overrides` is the durable
record — `asset_id` PK, an `active` boolean, no FK to `assets`, so it survives
the TRUNCATE — and it is already what stage s13 re-applies from each run.

The DB is a MagicMock here, so — per `.claude/rules/backend.md` — these assert
the generated SQL as well as the result. That matters more than usual: the
deactivate runs before the reset in the same transaction, so a reset guarded on
`EXISTS (… AND co.active)` would match zero rows while every mock-based
assertion stayed green. `TestTheResetGuardIsOrderIndependent` is what stops a
future edit reintroducing exactly that.

Lives under src/modules/assets/tests/ deliberately: that path is in the
"pipeline + delivery domain (hard gate)" required check, whereas the sibling
reclassify tests under tests/unit/ run with continue-on-error and cannot fail
the build.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import UUID

from src.modules.assets.services.reclassify_service import ReclassifyService


# Real UUIDs, spelled the way psycopg2 renders a `uuid` column. The service
# canonicalises every id it is handed and rejects anything that is not a UUID
# (#1275), and an `a1`-style stand-in could never have survived the `::uuid`
# casts these ids are bound into anyway.
A1 = '11111111-1111-4111-8111-111111111111'
A2 = '22222222-2222-4222-8222-222222222222'
A3 = '33333333-3333-4333-8333-333333333333'

# An asset overridden to network/switch that has since been through a pipeline
# run: the durable override row is still active, but the live flag has been
# reset to false by the rebuild. This is the steady state of every override
# older than one pipeline run.
FLAG_RESET_BY_PIPELINE = {
    'id': A1,
    'category': 'network',
    'subcategory': 'switch',
    'category_predicted': 'server',
    'subcategory_predicted': 'linux',
    'category_is_overridden': False,
}

# An override created through reclassify_asset. Before this fix `_update_asset`
# wrote only category/subcategory/updated_at, so the flag was never true even
# for an instant — the override was unclearable from creation.
FROM_SINGLE_ASSET_PATH = {
    'id': A2,
    'category': 'network',
    'subcategory': 'switch',
    'category_predicted': 'workstation',
    'subcategory_predicted': 'desktop',
    'category_is_overridden': False,
}

# Swept up by a filter that matched it; it has no override row at all.
NEVER_OVERRIDDEN = {
    'id': A3,
    'category': 'server',
    'subcategory': 'linux',
    'category_predicted': 'server',
    'subcategory_predicted': 'linux',
    'category_is_overridden': False,
}


def _svc(rows=(FLAG_RESET_BY_PIPELINE,), active_ids=(A1,)):
    """A ReclassifyService whose DB is a mock (__new__ skips the catalog load).

    `active_ids` is what `category_overrides` says — the repository call the
    clear path now keys on. The rows carry `category_is_overridden: False`
    throughout, which is the whole point: the flag disagrees with the durable
    record and the durable record is the one that counts.
    """
    svc = ReclassifyService.__new__(ReclassifyService)
    # One parent so the repository calls and the SQL land in a single ordered
    # log — the reset/deactivate ordering is a correctness property here.
    manager = MagicMock()
    svc.db = MagicMock()
    svc.override_repo = MagicMock()
    manager.attach_mock(svc.db, 'db')
    manager.attach_mock(svc.override_repo, 'override_repo')
    svc._manager = manager
    svc.custom_subcategory_repo = MagicMock()
    svc._catalog = [{"name": "network", "subcategories": ["switch"]}]
    svc.db.execute_query.return_value = [dict(r) for r in rows]
    svc.override_repo.get_active_override_ids.return_value = set(active_ids)
    return svc


def _statements(db):
    """Every statement issued, whitespace-collapsed so reformatting is free."""
    return [" ".join(c.args[0].split()) for c in db.execute_query.call_args_list]


def _the(db, needle):
    """The one statement containing `needle` — asserts it is unambiguous."""
    matches = [s for s in _statements(db) if needle in s]
    assert len(matches) == 1, f"expected exactly one {needle!r} statement, got {matches}"
    return matches[0]


def _events(db):
    return db.bulk_write_chronicle_events.call_args.args[0]


def _write_order(svc):
    """The deactivate and the reset UPDATE, in the order they were issued."""
    order = []
    for name, args, _ in svc._manager.mock_calls:
        if name == 'override_repo.deactivate_overrides_bulk':
            order.append('deactivate')
        elif name == 'db.execute_query' and args and 'UPDATE assets' in args[0]:
            order.append('reset')
    return order


class TestClearingKeysOnTheDurableRecord:
    """The issue itself: an override the flag has forgotten still clears."""

    def test_an_override_whose_flag_a_pipeline_run_reset_still_clears(self):
        svc = _svc()

        out = svc.clear_overrides_bulk([A1], 'admin')

        assert out == {"cleared": 1}
        svc.override_repo.deactivate_overrides_bulk.assert_called_once_with([A1])
        # The reset ran, and ran for this asset — `_the` alone only proves a
        # single 'UPDATE assets' was issued, not that it targeted A1.
        reset = [
            c for c in svc.db.execute_query.call_args_list
            if 'UPDATE assets' in c.args[0]
        ]
        assert [c.args[1]['ids'] for c in reset] == [[A1]]

    def test_an_override_from_the_single_asset_path_still_clears(self):
        """`_update_asset` never set the flag, so this override read as
        never-overridden from creation. The override row is what says
        otherwise."""
        svc = _svc(rows=(FROM_SINGLE_ASSET_PATH,), active_ids=(A2,))

        out = svc.clear_overrides_bulk([A2], 'admin')

        assert out == {"cleared": 1}
        svc.override_repo.deactivate_overrides_bulk.assert_called_once_with([A2])

    def test_the_active_override_lookup_is_asked_about_every_matched_id(self):
        """Narrowing happens on the answer, not before the question — an id
        skipped here can never be cleared."""
        svc = _svc(
            rows=(FLAG_RESET_BY_PIPELINE, NEVER_OVERRIDDEN),
            active_ids=(A1,),
        )

        svc.clear_overrides_bulk([A1, A3], 'admin')

        svc.override_repo.get_active_override_ids.assert_called_once_with([A1, A3])

    def test_a_uuid_typed_id_matches_the_string_keyed_answer(self):
        """`get_matching_asset_ids` returns strings today, so the whole method
        is string-keyed — `previous` too. A caller handing over UUID objects
        would otherwise match nothing and clear nothing, silently, which is the
        failure mode this issue is about."""
        asset_id = UUID('11111111-2222-3333-4444-555555555555')
        svc = _svc(
            rows=({**FLAG_RESET_BY_PIPELINE, 'id': str(asset_id)},),
            active_ids=(str(asset_id),),
        )

        out = svc.clear_overrides_bulk([asset_id], 'admin')

        assert out == {"cleared": 1}
        changes = _events(svc.db)[0]['attribute_changes']
        assert changes['category'] == {'old': 'network', 'new': 'server'}

    def test_the_flag_is_never_what_decides(self):
        """The inverse of the bug: a stale `true` flag on an asset whose
        override has already been deactivated must not resurrect a clear."""
        svc = _svc(
            rows=({**NEVER_OVERRIDDEN, 'category_is_overridden': True},),
            active_ids=(),
        )

        out = svc.clear_overrides_bulk([A3], 'admin')

        assert out == {"cleared": 0}
        svc.override_repo.deactivate_overrides_bulk.assert_not_called()


class TestALookupFailureIsNotReportedAsSuccess:
    """The lookup runs INSIDE `_bulk_scope()`'s write transaction, which changes
    what "degrade to empty on error" means.

    A swallowed DB error leaves the transaction INERROR without poisoning the
    scope, so `errored` stays False; an empty result then makes `overridden_ids`
    empty, nothing else runs, and `end_scope()` — called without
    `raise_on_aborted`, unlike the pipeline orchestrator's — rolls back with only
    a `logger.warning` (`postgres_connection.py:262-287`, #813). The caller gets
    HTTP 200 and `{"cleared": 0}` for an operation that failed outright: the same
    "reported success, did nothing" shape as #1270 itself.
    """

    def test_the_failure_propagates_instead_of_clearing_nothing(self):
        svc = _svc()
        svc.override_repo.get_active_override_ids.side_effect = RuntimeError("timeout")

        try:
            svc.clear_overrides_bulk([A1], 'admin')
        except RuntimeError:
            pass
        else:
            raise AssertionError("a failed lookup was reported as {'cleared': 0}")

    def test_the_scope_rolls_back(self):
        svc = _svc()
        svc.override_repo.get_active_override_ids.side_effect = RuntimeError("timeout")

        try:
            svc.clear_overrides_bulk([A1], 'admin')
        except RuntimeError:
            pass

        err = svc.db.conn_manager.end_scope.call_args.kwargs.get('error')
        assert isinstance(err, RuntimeError)


class TestANeverOverriddenAssetIsLeftAlone:
    """A filter set names assets that were never overridden. Clearing one would
    rewrite its real values and write an audit event for something that did not
    happen."""

    def test_it_is_not_deactivated_not_reset_and_not_counted(self):
        svc = _svc(
            rows=(FLAG_RESET_BY_PIPELINE, NEVER_OVERRIDDEN),
            active_ids=(A1,),
        )

        out = svc.clear_overrides_bulk([A1, A3], 'admin')

        assert out == {"cleared": 1}
        svc.override_repo.deactivate_overrides_bulk.assert_called_once_with([A1])
        assert _the(svc.db, 'UPDATE assets')  # issued once, for A1
        reset_ids = [
            c.args[1]['ids'] for c in svc.db.execute_query.call_args_list
            if 'UPDATE assets' in c.args[0]
        ]
        assert reset_ids == [[A1]]

    def test_it_emits_no_chronicle_event(self):
        svc = _svc(
            rows=(FLAG_RESET_BY_PIPELINE, NEVER_OVERRIDDEN),
            active_ids=(A1,),
        )

        svc.clear_overrides_bulk([A1, A3], 'admin')

        assert [e['asset_id'] for e in _events(svc.db)] == [A1]

    def test_nothing_at_all_happens_when_none_are_overridden(self):
        svc = _svc(rows=(NEVER_OVERRIDDEN,), active_ids=())

        out = svc.clear_overrides_bulk([A3], 'admin')

        assert out == {"cleared": 0}
        svc.override_repo.deactivate_overrides_bulk.assert_not_called()
        svc.db.bulk_write_chronicle_events.assert_not_called()
        assert not [s for s in _statements(svc.db) if 'UPDATE assets' in s]

    def test_the_statement_guard_holds_independently_of_the_caller(self):
        """The id-level filter and the WHERE clause are two separate defences;
        the docstring on `_reset_assets_bulk` promises the second one, and a
        MagicMock DB will happily "reset" a row the real WHERE clause excludes.
        Assert the clause."""
        svc = _svc()

        svc.clear_overrides_bulk([A1], 'admin')

        sql = _the(svc.db, 'UPDATE assets')
        assert 'EXISTS ( SELECT 1 FROM category_overrides co WHERE co.asset_id = assets.id )' in sql


class TestTheResetGuardIsOrderIndependent:
    """The trap this fix has to walk past.

    `deactivate_overrides_bulk` runs before `_reset_assets_bulk`, both inside
    one `_bulk_scope()` transaction, so the UPDATE reads its own transaction's
    writes: every override row it cares about is already `active = false`. A
    guard of `EXISTS (… AND co.active)` therefore matches nothing — the exact
    bug, restored, with every returns-a-row test still green.
    """

    def test_the_guard_does_not_read_activity(self):
        svc = _svc()

        svc.clear_overrides_bulk([A1], 'admin')

        sql = _the(svc.db, 'UPDATE assets')
        guard = sql.split('WHERE', 1)[1]
        assert 'active' not in guard, (
            "the reset guard must not depend on the override still being active: "
            "the deactivate has already run in this transaction"
        )

    def test_the_guard_is_not_the_flag_the_pipeline_resets(self):
        svc = _svc()

        svc.clear_overrides_bulk([A1], 'admin')

        sql = _the(svc.db, 'UPDATE assets')
        assert 'AND category_is_overridden = true' not in sql

    def test_an_activity_reading_guard_would_have_to_run_before_the_deactivate(self):
        """Stated as an implication so it survives a change to either half.

        Two configurations are correct — an order-independent guard in any
        order, or an activity-reading guard with the reset first. The one
        combination that silently clears nothing is rejected here.
        """
        svc = _svc()

        svc.clear_overrides_bulk([A1], 'admin')

        guard = _the(svc.db, 'UPDATE assets').split('WHERE', 1)[1]
        order = _write_order(svc)
        assert order == ['deactivate', 'reset'] or order == ['reset', 'deactivate'], order
        assert 'active' not in guard or order.index('reset') < order.index('deactivate'), (
            f"reset guard reads override activity but runs after the deactivate "
            f"(order: {order}) — it will match zero rows"
        )

    def test_both_writes_land_in_one_scope(self):
        """Whatever their order, they are all-or-nothing — the guard's
        reasoning about what the UPDATE can see depends on it. The refresh
        hook afterwards opens a SECOND begin/end pair (outside_request_scope,
        #1428); only the first pair brackets the writes."""
        svc = _svc()

        svc.clear_overrides_bulk([A1], 'admin')

        calls = svc._manager.mock_calls
        names = [name for name, _, _ in calls]
        first_begin = names.index('db.conn_manager.begin_scope')
        first_end = names.index('db.conn_manager.end_scope')
        deactivate = names.index('override_repo.deactivate_overrides_bulk')
        reset = next(
            i for i, (name, args, _) in enumerate(calls)
            if name == 'db.execute_query' and args and 'UPDATE assets' in args[0]
        )
        assert first_begin < deactivate < first_end
        assert first_begin < reset < first_end


class TestTheTwoOverridePathsAgree:
    """`_update_asset` not setting the flag is half of #1270. Clearing no longer
    depends on the flag, but a column one path writes and its twin does not is
    what produced the bug in the first place."""

    def test_the_single_asset_update_sets_the_flag(self):
        svc = _svc()

        svc._update_asset(A1, 'network', 'switch', datetime.now(timezone.utc))

        assert 'category_is_overridden = true' in _the(svc.db, 'UPDATE assets')

    def test_both_update_paths_set_the_same_columns(self):
        single = _svc()
        bulk = _svc()
        now = datetime.now(timezone.utc)

        single._update_asset(A1, 'network', 'switch', now)
        bulk._update_assets_bulk([A1], 'network', 'switch', now)

        def _assignments(sql):
            body = sql.split('SET', 1)[1].split('WHERE', 1)[0]
            return sorted(a.split('=')[0].strip() for a in body.split(','))

        assert _assignments(_the(single.db, 'UPDATE assets')) == \
            _assignments(_the(bulk.db, 'UPDATE assets'))
