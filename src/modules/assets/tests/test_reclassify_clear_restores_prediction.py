# SPDX-License-Identifier: AGPL-3.0-or-later
"""Clearing a category override restores the pipeline's prediction (#1154).

The bulk dialog's button says "Reset {n} to predicted", and it lied twice over.
`subcategory` was hardcoded to NULL because no `subcategory_predicted` column
existed, and `category` was set from `category_predicted` — a column nothing had
ever written, because `bulk_upsert_assets` omitted it from its INSERT list and
silently dropped the DataFrame column the pipeline built. On the shared dev
database all 73,748 rows had it NULL. So the reset blanked BOTH fields, for
every asset, until the next pipeline run recomputed them.

The DB is a MagicMock here, so — per `.claude/rules/backend.md` — these assert
the generated SQL as well as the returned/emitted dicts. The whole defect lived
in a SQL string: a mock happily "restores" a value from a statement that reads
`subcategory = NULL`, so a test that only inspected the result dict would have
passed against the broken code.

Lives under src/modules/assets/tests/ deliberately: that path is in the
"pipeline + delivery domain (hard gate)" required check, whereas the sibling
reclassify tests under tests/unit/ run with continue-on-error and cannot fail
the build.
"""

from unittest.mock import MagicMock

from src.modules.assets.services.reclassify_service import ReclassifyService


# Real UUIDs, spelled the way psycopg2 renders a `uuid` column. The service
# canonicalises every id it is handed and rejects anything that is not a UUID
# (#1275), and an `a1`-style stand-in could never have survived the `::uuid`
# casts these ids are bound into anyway.
A1 = '11111111-1111-4111-8111-111111111111'
A2 = '22222222-2222-4222-8222-222222222222'

# An asset the analyst overrode to network/switch, whose pipeline prediction
# (server/linux) was snapshotted into the *_predicted columns by the run that
# built the row. No `category_is_overridden` — the live flag is not part of the
# clear path's input any more, and a fixture carrying it would imply it is
# (#1270).
OVERRIDDEN_ROW = {
    'id': A1,
    'category': 'network',
    'subcategory': 'switch',
    'category_predicted': 'server',
    'subcategory_predicted': 'linux',
}

# A row written before migration 143 — overridden, but with no snapshot to
# restore from. Nothing can recover its prediction, so the reset must leave the
# values alone rather than blank them.
LEGACY_ROW = {
    'id': A2,
    'category': 'network',
    'subcategory': 'switch',
    'category_predicted': None,
    'subcategory_predicted': None,
}


def _svc(rows=(OVERRIDDEN_ROW,)):
    """A ReclassifyService whose DB is a mock (__new__ skips the catalog load)."""
    svc = ReclassifyService.__new__(ReclassifyService)
    svc.db = MagicMock()
    svc.override_repo = MagicMock()
    svc.custom_subcategory_repo = MagicMock()
    svc._catalog = [{"name": "network", "subcategories": ["switch"]}]
    svc.db.execute_query.return_value = [dict(r) for r in rows]
    # Every row here is overridden; `category_overrides` is what says so.
    svc.override_repo.get_active_override_ids.return_value = {
        str(r['id']) for r in rows
    }
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


class TestTheResetRestoresBothFields:
    """The issue's regression: clearing must put back what the pipeline predicted."""

    def test_the_update_restores_category_and_subcategory_from_the_snapshot(self):
        svc = _svc()

        svc.clear_overrides_bulk([A1], 'admin')

        sql = _the(svc.db, 'UPDATE assets')
        assert 'category = COALESCE(category_predicted, category)' in sql
        assert 'subcategory = COALESCE(subcategory_predicted, subcategory)' in sql

    def test_the_update_can_never_write_null_into_either_field(self):
        """The defect itself, stated as an invariant over the statement.

        `SET subcategory = NULL` blanked it outright; `SET category =
        category_predicted` blanked it too, because that column was never
        written. Both forms are now unreachable — the COALESCE degrades to
        "leave the value alone" instead.
        """
        svc = _svc()

        svc.clear_overrides_bulk([A1], 'admin')

        sql = _the(svc.db, 'UPDATE assets')
        assert 'subcategory = NULL' not in sql
        assert 'category = category_predicted' not in sql
        assert 'subcategory = subcategory_predicted' not in sql

    def test_the_snapshot_query_reads_both_predicted_columns(self):
        """The chronicle event needs the subcategory snapshot too — without it
        the event cannot state what the UPDATE wrote."""
        svc = _svc()

        svc.clear_overrides_bulk([A1], 'admin')

        # 'SELECT id' rather than 'SELECT': the reset's EXISTS guard carries a
        # SELECT of its own.
        sql = _the(svc.db, 'SELECT id')
        assert 'category_predicted' in sql
        assert 'subcategory_predicted' in sql

    def test_the_never_overridden_guard_survives(self):
        """A filter set can name assets that were never overridden. The WHERE
        clause is what stops the reset touching them, so it is asserted on the
        statement — the id-level filter in clear_overrides_bulk is a separate
        defence and neither one alone is enough.

        The guard was `category_is_overridden = true` until #1270; it now reads
        the durable override row, because the flag reads false on every override
        older than one pipeline run. See
        test_reclassify_override_clear_keys_on_overrides_table.py for why it
        must not filter on `co.active`."""
        svc = _svc()

        svc.clear_overrides_bulk([A1], 'admin')

        sql = _the(svc.db, 'UPDATE assets')
        assert 'EXISTS ( SELECT 1 FROM category_overrides co WHERE co.asset_id = assets.id )' in sql


class TestTheChronicleMatchesWhatWasWritten:
    """`category_override_cleared` must not contradict the assets table."""

    def test_new_values_are_the_predicted_pair(self):
        svc = _svc()

        svc.clear_overrides_bulk([A1], 'admin')

        changes = _events(svc.db)[0]['attribute_changes']
        assert changes['category'] == {'old': 'network', 'new': 'server'}
        assert changes['subcategory'] == {'old': 'switch', 'new': 'linux'}

    def test_a_legacy_row_records_the_value_it_kept_not_null(self):
        """Same COALESCE semantics as the UPDATE. Recording None here would
        write a fictional history entry that nothing would ever flag: the
        statement leaves both fields untouched on a row with no snapshot."""
        svc = _svc(rows=(LEGACY_ROW,))

        svc.clear_overrides_bulk([A2], 'admin')

        changes = _events(svc.db)[0]['attribute_changes']
        assert changes['category'] == {'old': 'network', 'new': 'network'}
        assert changes['subcategory'] == {'old': 'switch', 'new': 'switch'}

    def test_a_partial_snapshot_coalesces_each_field_independently(self):
        """category_predicted predates 143, subcategory_predicted does not, so
        a row backfilled by 143 can carry one without the other."""
        svc = _svc(rows=({**OVERRIDDEN_ROW, 'subcategory_predicted': None},))

        svc.clear_overrides_bulk([A1], 'admin')

        changes = _events(svc.db)[0]['attribute_changes']
        assert changes['category']['new'] == 'server'    # restored
        assert changes['subcategory']['new'] == 'switch'  # kept
