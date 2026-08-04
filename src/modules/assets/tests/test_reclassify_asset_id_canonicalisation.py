# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asset ids are canonicalised before they are matched against psycopg2 (#1275).

psycopg2 renders a `uuid` column as canonical lowercase 8-4-4-4-12, always. Every
dict and set `ReclassifyService` builds off a query result is therefore keyed
that way, and looking one up with the caller's own spelling is a plain string
comparison that silently misses. Postgres accepts — and canonicalises on cast —
several spellings Python's `uuid.UUID()` also parses, so a caller can construct
one legitimately:

* `A0EEBC99-…` uppercase
* `{a0eebc99-…}` braces
* `a0eebc999c0b…` unhyphenated

Three lookups had that shape, and none of them failed loudly:

* `clear_overrides_bulk` narrowed against `get_active_override_ids`' canonical
  set, matched nothing, never called `deactivate_overrides_bulk`, and answered
  `{"cleared": 0}` with a 200 — the reported symptom;
* `reclassify_bulk` read its chronicle old-values out of a canonically-keyed
  snapshot, so every event recorded `"old": None` for both fields while the
  writes themselves landed correctly (they go through `::uuid` casts Postgres
  canonicalises). Nothing failed and the wrong history is durable — worse than
  the no-op;
* `get_override_state` returned a null predicted pair, so the edit modal reported
  "no stored prediction" for an asset that has one.

`urn:uuid:…` is deliberately absent from the list: Postgres *rejects* it, which
is a loud failure and a different bug (the service now canonicalises it too, so
it works rather than 500ing, but that is a side effect and not what is asserted).

The DB is a mock here, which is exactly where this bug hides: a mock hands back
its canned row whatever id it was asked about, so a fixture that echoes the
caller's spelling makes every one of these tests unfailable. `_select` and
`_active_ids` below model the real asymmetry instead — they match the way
`ANY(%(ids)s::uuid[])` matches (canonicalising on cast) and return ids the way
psycopg2 renders them (canonical), regardless of what was passed in. `_canonical`
stands in for the database and deliberately does not call the service's own
helper: normalising with the code under test would prove nothing.

Every behaviour class is parametrised over the canonical spelling too. That case
is the guard rail — it passed before this fix and passes after — and the fact
that it is the *only* one that passed against the old service is what shows the
fixture models the asymmetry rather than simply being broken.

Lives under src/modules/assets/tests/ deliberately: that path is in the
"pipeline + delivery domain (hard gate)" required check, whereas the sibling
reclassify tests under tests/unit/ run with continue-on-error and cannot fail
the build.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from flask import Flask

from src.api.middleware.error_handler import handle_errors
from src.modules.assets.services.reclassify_service import ReclassifyService


# Canonical lowercase 8-4-4-4-12 — the only spelling psycopg2 ever renders a
# `uuid` column as, and so the only spelling the service's keyed lookups hold.
ASSET_ID = 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'

# The canonical spelling first: it is the guard rail, and naming it in the same
# parametrisation keeps "still behaves as before" beside "now behaves at all".
ALL_SPELLINGS = [
    ASSET_ID,
    ASSET_ID.upper(),
    '{' + ASSET_ID + '}',
    ASSET_ID.replace('-', ''),
]
SPELLING_IDS = ['canonical', 'uppercase', 'braces', 'no-hyphens']

OVERRIDDEN_AT = datetime(2026, 7, 14, 9, 30, tzinfo=timezone.utc)

# An asset an analyst overrode to network/switch, with the pipeline's own answer
# frozen in the *_predicted pair (#1154). No `category_is_overridden`:
# `_fetch_current_categories` does not select it and a fixture carrying it would
# imply this path has an opinion about it (#1270).
OVERRIDDEN_ROW = {
    'id': ASSET_ID,
    'category': 'network',
    'subcategory': 'switch',
    'category_predicted': 'server',
    'subcategory_predicted': 'linux',
}

# A second overridden asset, for the batch-ordering assertion.
SECOND_ID = 'b1ffc99a-8d1c-4fa9-cc7e-7cc0ce491b22'
SECOND_ROW = {
    'id': SECOND_ID,
    'category': 'network',
    'subcategory': 'switch',
    'category_predicted': 'printer',
    'subcategory_predicted': 'laser',
}

# What `category_overrides` holds for that asset.
OVERRIDE_ROW = {
    'asset_id': ASSET_ID,
    'category': 'network',
    'subcategory': 'switch',
    'reason': 'Mislabelled by the OUI fallback',
    'overridden_by': 'jsmith',
    'overridden_at': OVERRIDDEN_AT,
}


class DatabaseRejectedTheSpelling(Exception):
    """What Postgres does with a spelling its `::uuid` cast cannot parse:
    `invalid input syntax for type uuid`, surfacing as a psycopg2 error that
    `@handle_errors` has no case for and answers with a 500.

    Raised by the fixtures below so "the service rejected the id" stays
    distinguishable from "the id reached the database" — otherwise a malformed-id
    test passes for the wrong reason.
    """


def _canonical(value):
    """What the `::uuid` cast does to a spelling Postgres accepts, and what
    psycopg2 renders the column back as. Stands in for the database, so it does
    NOT call the service's helper — normalising with the code under test would
    make these assertions circular."""
    try:
        return str(UUID(str(value)))
    except ValueError:
        raise DatabaseRejectedTheSpelling(
            f'invalid input syntax for type uuid: "{value}"'
        )


def _svc(rows=(OVERRIDDEN_ROW,), active=(ASSET_ID,)):
    """A ReclassifyService whose DB is a mock (__new__ skips the catalog load).

    The mock models the one asymmetry this issue turns on: ids go in spelled
    however the caller spelled them, and come back canonical. `active` is what
    `category_overrides` says, keyed canonically like the real table.
    """
    svc = ReclassifyService.__new__(ReclassifyService)
    svc.db = MagicMock()
    svc.override_repo = MagicMock()
    svc.custom_subcategory_repo = MagicMock()
    svc.custom_subcategory_repo.get_for_category.return_value = []
    svc._catalog = [
        {"name": "network", "subcategories": ["switch"]},
        {"name": "printer", "subcategories": ["laser"]},
    ]

    def _select(query, params=None, fetch=False):
        """Rows for the ids the statement asked about, matched the way
        `ANY(%(ids)s::uuid[])` matches and returned with `id` rendered the way
        psycopg2 renders a uuid column."""
        if not fetch:
            return None
        wanted = {_canonical(i) for i in (params or {}).get('ids', [])}
        return [dict(r) for r in rows if r['id'] in wanted]

    def _active_ids(asset_ids):
        return {_canonical(i) for i in asset_ids if _canonical(i) in active}

    def _active_override(asset_id):
        return dict(OVERRIDE_ROW) if _canonical(asset_id) in active else None

    svc.db.execute_query.side_effect = _select
    svc.override_repo.get_active_override_ids.side_effect = _active_ids
    svc.override_repo.get_active_override.side_effect = _active_override
    return svc


def _events(db):
    return db.bulk_write_chronicle_events.call_args.args[0]


def _id_params(db):
    """The `ids` list of every statement that carried one."""
    return [
        c.args[1]['ids'] for c in db.execute_query.call_args_list
        if len(c.args) > 1 and isinstance(c.args[1], dict) and 'ids' in c.args[1]
    ]


class TestClearingAnOverrideIgnoresHowTheIdWasSpelled:
    """The reported symptom: `{"cleared": 0}` and a 200 for an override that is
    sitting right there."""

    @pytest.mark.parametrize('spelling', ALL_SPELLINGS, ids=SPELLING_IDS)
    def test_the_override_is_cleared(self, spelling):
        svc = _svc()

        out = svc.clear_overrides_bulk([spelling], 'admin')

        assert out == {"cleared": 1}
        svc.override_repo.deactivate_overrides_bulk.assert_called_once_with([ASSET_ID])

    @pytest.mark.parametrize('spelling', ALL_SPELLINGS, ids=SPELLING_IDS)
    def test_the_live_row_is_reset_to_the_predicted_pair(self, spelling):
        """Deactivating the override row is half the operation; the reset UPDATE
        is what the user sees. It has to be issued, and issued for this asset."""
        svc = _svc()

        svc.clear_overrides_bulk([spelling], 'admin')

        reset = [
            c for c in svc.db.execute_query.call_args_list
            if 'UPDATE assets' in c.args[0]
        ]
        assert [c.args[1]['ids'] for c in reset] == [[ASSET_ID]]

    @pytest.mark.parametrize('spelling', ALL_SPELLINGS, ids=SPELLING_IDS)
    def test_the_chronicle_event_states_what_the_reset_wrote(self, spelling):
        svc = _svc()

        svc.clear_overrides_bulk([spelling], 'admin')

        changes = _events(svc.db)[0]['attribute_changes']
        assert changes['category'] == {'old': 'network', 'new': 'server'}
        assert changes['subcategory'] == {'old': 'switch', 'new': 'linux'}


class TestBulkReclassifyRecordsWhatItActuallyOverwrote:
    """The severe one, and the one the issue title does not mention.

    The writes land whatever the spelling — they go through `::uuid` casts
    Postgres canonicalises — so nothing fails and nothing is retried. Only the
    chronicle is wrong, and it is wrong durably: every event claims the asset had
    no category and no subcategory before the change.
    """

    @pytest.mark.parametrize('spelling', ALL_SPELLINGS, ids=SPELLING_IDS)
    def test_the_event_carries_the_real_previous_values(self, spelling):
        svc = _svc()

        svc.reclassify_bulk([spelling], 'printer', 'laser', 'admin')

        changes = _events(svc.db)[0]['attribute_changes']
        assert changes['category'] == {'old': 'network', 'new': 'printer'}
        assert changes['subcategory'] == {'old': 'switch', 'new': 'laser'}

    @pytest.mark.parametrize('spelling', ALL_SPELLINGS, ids=SPELLING_IDS)
    def test_no_event_invents_an_asset_that_had_no_previous_category(self, spelling):
        """The corruption stated as its own assertion: a `None` old-value on an
        asset the snapshot knows about is a fictional history entry, and one no
        error, count or status code would ever flag."""
        svc = _svc()

        svc.reclassify_bulk([spelling], 'printer', 'laser', 'admin')

        for event in _events(svc.db):
            for field, change in event['attribute_changes'].items():
                assert change['old'] is not None, (
                    f"{field} recorded as having no previous value, but the "
                    f"snapshot holds one for {event['asset_id']}"
                )

    @pytest.mark.parametrize('spelling', ALL_SPELLINGS, ids=SPELLING_IDS)
    def test_the_writes_still_land_and_are_still_counted(self, spelling):
        """Unchanged by this fix — asserted so the fix cannot be mistaken for
        the reason the writes work."""
        svc = _svc()

        out = svc.reclassify_bulk([spelling], 'printer', 'laser', 'admin')

        assert out == {"updated": 1}
        assert svc.override_repo.save_overrides_bulk.call_args.kwargs['asset_ids'] \
            == [ASSET_ID]


class TestTheOverrideStateReadIgnoresHowTheIdWasSpelled:
    """What the edit modal offers to restore to (#1155). A null predicted pair
    reads as "no stored prediction for this asset", which is a statement about
    migration 143 — made here about an asset that has one."""

    @pytest.mark.parametrize('spelling', ALL_SPELLINGS, ids=SPELLING_IDS)
    def test_the_predicted_pair_comes_back(self, spelling):
        svc = _svc()

        state = svc.get_override_state(spelling)

        assert state['category_predicted'] == 'server'
        assert state['subcategory_predicted'] == 'linux'

    @pytest.mark.parametrize('spelling', ALL_SPELLINGS, ids=SPELLING_IDS)
    def test_the_asset_still_reads_as_overridden(self, spelling):
        svc = _svc()

        state = svc.get_override_state(spelling)

        assert state['is_overridden'] is True
        assert state['overridden_by'] == 'jsmith'

    @pytest.mark.parametrize('spelling', ALL_SPELLINGS, ids=SPELLING_IDS)
    def test_the_echoed_id_is_the_one_the_database_holds(self, spelling):
        """The body is keyed by asset elsewhere in the client; echoing the
        caller's spelling back would hand it a second name for the same row."""
        svc = _svc()

        state = svc.get_override_state(spelling)

        assert state['asset_id'] == ASSET_ID


class TestEverythingDownstreamIsHandedCanonicalIds:
    """Normalised once, at the entry point.

    Stated over what the private helpers and the repository actually receive
    rather than over the source: that is the property the rest of the service
    depends on, and it survives a refactor that moves the call.
    """

    @pytest.mark.parametrize('spelling', ALL_SPELLINGS, ids=SPELLING_IDS)
    def test_the_clear_path_passes_on_nothing_but_canonical_ids(self, spelling):
        svc = _svc()

        svc.clear_overrides_bulk([spelling], 'admin')

        assert svc.override_repo.get_active_override_ids.call_args.args[0] == [ASSET_ID]
        assert svc.override_repo.deactivate_overrides_bulk.call_args.args[0] == [ASSET_ID]
        assert _id_params(svc.db) == [[ASSET_ID], [ASSET_ID]]
        assert [e['asset_id'] for e in _events(svc.db)] == [ASSET_ID]

    @pytest.mark.parametrize('spelling', ALL_SPELLINGS, ids=SPELLING_IDS)
    def test_the_reclassify_path_passes_on_nothing_but_canonical_ids(self, spelling):
        svc = _svc()

        svc.reclassify_bulk([spelling], 'printer', 'laser', 'admin')

        assert svc.override_repo.save_overrides_bulk.call_args.kwargs['asset_ids'] \
            == [ASSET_ID]
        assert _id_params(svc.db) == [[ASSET_ID], [ASSET_ID]]
        assert [e['asset_id'] for e in _events(svc.db)] == [ASSET_ID]

    @pytest.mark.parametrize('spelling', ALL_SPELLINGS, ids=SPELLING_IDS)
    def test_the_state_read_passes_on_nothing_but_canonical_ids(self, spelling):
        svc = _svc()

        svc.get_override_state(spelling)

        assert svc.override_repo.get_active_override.call_args.args[0] == ASSET_ID
        assert _id_params(svc.db) == [[ASSET_ID]]


class TestOneAssetSpelledTwoWaysIsStillOneAsset:
    """Canonicalising creates a way for a batch to hold the same asset twice
    that did not exist before it.

    `['A0EEBC99-…', 'a0eebc99-…']` was two distinct strings, and only one of them
    ever matched. Both now normalise to the same id, so without a dedupe the
    asset is deactivated twice, reset twice, counted twice, and — the part that
    lasts — gets two identical `category_override_cleared` events at the same
    timestamp. That is durable wrong history arriving through the fix for
    durable wrong history, so it is closed here rather than left for the caller.

    No current caller can produce it: the bulk routes source ids from
    `get_matching_asset_ids` (distinct, straight out of psycopg2) and the
    per-asset routes pass one. That is the same "holds only because every caller
    remembers" this whole change exists to stop relying on.
    """

    def test_the_clear_counts_and_audits_it_once(self):
        svc = _svc()

        out = svc.clear_overrides_bulk([ASSET_ID.upper(), ASSET_ID], 'admin')

        assert out == {"cleared": 1}
        svc.override_repo.deactivate_overrides_bulk.assert_called_once_with([ASSET_ID])
        assert [e['asset_id'] for e in _events(svc.db)] == [ASSET_ID]

    def test_the_reclassify_writes_one_history_entry(self):
        svc = _svc()

        out = svc.reclassify_bulk([ASSET_ID.upper(), ASSET_ID], 'printer', 'laser', 'admin')

        assert out == {"updated": 1}
        assert [e['asset_id'] for e in _events(svc.db)] == [ASSET_ID]

    def test_the_batch_keeps_the_order_it_was_given(self):
        """A `set()` would dedupe too, and make every batch's order
        nondeterministic — chronicle events, log lines and the ids bound into
        each statement would shuffle between runs for no reason."""
        svc = _svc(rows=(OVERRIDDEN_ROW, SECOND_ROW), active=(ASSET_ID, SECOND_ID))

        svc.clear_overrides_bulk(
            [SECOND_ID, ASSET_ID.upper(), SECOND_ID.upper()], 'admin',
        )

        assert svc.override_repo.deactivate_overrides_bulk.call_args.args[0] == \
            [SECOND_ID, ASSET_ID]
        assert [e['asset_id'] for e in _events(svc.db)] == [SECOND_ID, ASSET_ID]


class TestAMalformedIdIsRejectedRatherThanSkipped:
    """Dropping an unparseable id from the batch would be the same
    silent-success failure in a new costume: fewer assets changed than the user
    asked for, reported as a clean 200."""

    def test_the_clear_path_raises_before_opening_a_transaction(self):
        svc = _svc()

        with pytest.raises(ValueError):
            svc.clear_overrides_bulk(['not-a-uuid'], 'admin')

        svc.db.conn_manager.begin_scope.assert_not_called()

    def test_the_reclassify_path_raises_before_opening_a_transaction(self):
        svc = _svc()

        with pytest.raises(ValueError):
            svc.reclassify_bulk(['not-a-uuid'], 'printer', 'laser', 'admin')

        svc.db.conn_manager.begin_scope.assert_not_called()

    def test_the_state_read_raises(self):
        svc = _svc()

        with pytest.raises(ValueError):
            svc.get_override_state('not-a-uuid')

    def test_one_bad_id_fails_the_whole_batch_rather_than_part_of_it(self):
        """All-or-nothing is what `_bulk_scope` already promises for the writes;
        the batch it was handed has to mean the same thing."""
        svc = _svc()

        with pytest.raises(ValueError):
            svc.clear_overrides_bulk([ASSET_ID, 'not-a-uuid'], 'admin')

        svc.override_repo.deactivate_overrides_bulk.assert_not_called()
        svc.db.bulk_write_chronicle_events.assert_not_called()

    def test_the_message_names_the_id_that_was_rejected(self):
        """A batch can be thousands of ids sourced from a filter; "badly formed
        hexadecimal UUID string" alone does not say which one."""
        svc = _svc()

        with pytest.raises(ValueError) as exc:
            svc.clear_overrides_bulk([ASSET_ID, 'not-a-uuid'], 'admin')

        assert 'not-a-uuid' in str(exc.value)


class TestAMalformedIdSurfacesAsA400:
    """`@handle_errors` maps ValueError to 400 and anything else to 500, so the
    id must not reach the `::uuid` cast: Postgres' rejection is a psycopg2 error
    the decorator has no case for, and a client typo would answer 500 (#973 —
    the reason the blueprint runs `ensure_uuid`, which this does not replace)."""

    def test_the_status_is_400_and_the_id_never_reaches_the_database(self):
        svc = _svc()
        app = Flask(__name__)

        @handle_errors
        def route():
            return svc.clear_overrides_bulk(['not-a-uuid'], 'admin')

        with app.test_request_context('/'):
            _body, status = route()

        assert status == 400
        svc.db.execute_query.assert_not_called()
