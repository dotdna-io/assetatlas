# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pipeline's category prediction has to reach the database (#1154).

`assets.category_predicted` existed since migration 035 and was never written by
anything: `apply_overrides` created it as a pandas column, and
`bulk_upsert_assets` omitted it from its INSERT column list, so it was dropped at
persist time. 73,748 of 73,748 rows on the shared dev database had it NULL. That
is what made "Reset to predicted" blank the category — there was nothing to reset
to. `subcategory_predicted` did not exist at all until migration 143.

Two properties keep it populated, and both are tested here because each one alone
is insufficient:

1. The upsert must carry both columns — and must COALESCE them in its ON CONFLICT
   clause. Values are read with `asset.get(col)`, so a caller that doesn't supply
   the key sends NULL rather than raising, and `SET col = EXCLUDED.col` would then
   erase a stored prediction on any partial write.
2. Stage 13 must populate them on EVERY run, from the PRE-override values. It
   used to build them only inside `apply_overrides`, which s13 calls only when
   `enable_category_overrides` is true — and the service default for that flag is
   False, so most runs supplied nothing at all.

The upsert tests render the real statement the real method builds. psycopg2's
`as_string()` needs a live connection (`quote_ident` is a C function over one) and
this suite has no database, so `quote_ident` is stubbed with a pure-Python
equivalent — the query itself is still the production one, not a reconstruction.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import psycopg2.sql
import pytest

from src.database import postgres_adapter
from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.asset_generation.pipeline.stages.s13_category_selection import (
    CategorySelectionStage,
)
from src.modules.asset_generation.pipeline.stages.s14_scoring import ScoringStage
from src.modules.asset_generation.repositories.category_override_repository import (
    CategoryOverrideRepository,
)
from src.modules.asset_generation.services.chronicle_service import ChronicleService

PREDICTED_COLUMNS = ('category_predicted', 'subcategory_predicted')


# ── The upsert ────────────────────────────────────────────────────────────────

@pytest.fixture
def upsert(monkeypatch):
    """Run bulk_upsert_assets against mocks and return (sql_text, value_rows)."""
    monkeypatch.setattr(psycopg2.sql.ext, "quote_ident", lambda s, ctx: '"%s"' % s)

    captured = {}

    def _capture(cursor, query, values, **kwargs):
        captured['sql'] = query
        captured['values'] = values

    monkeypatch.setattr(postgres_adapter.extras, "execute_values", _capture)

    def _run(assets):
        adapter = PostgreSQLAdapter.__new__(PostgreSQLAdapter)
        adapter.conn_manager = MagicMock()
        adapter.conn_manager.get_cursor.return_value.__enter__.return_value = (
            MagicMock(), MagicMock(),
        )
        adapter.bulk_upsert_assets(assets)
        return captured['sql'], captured['values']

    return _run


def _insert_columns(sql_text):
    """The column list of `INSERT INTO assets (...)`, in order."""
    inner = sql_text.split('INSERT INTO assets (', 1)[1].split(')', 1)[0]
    return [c.strip().strip('"') for c in inner.split(',')]


class TestTheUpsertCarriesThePrediction:

    @pytest.mark.parametrize("column", PREDICTED_COLUMNS)
    def test_the_column_is_persisted(self, upsert, column):
        """The root cause: absent from this list, the pipeline's DataFrame column
        is silently discarded and the database keeps NULL forever."""
        sql_text, _ = upsert([{'id': 'a1', 'category_predicted': 'server'}])

        assert column in _insert_columns(sql_text)

    @pytest.mark.parametrize("column", PREDICTED_COLUMNS)
    def test_an_update_that_omits_the_key_cannot_erase_a_stored_value(self, upsert, column):
        """The wipe hazard, in two halves.

        `asset.get(col)` turns a missing key into None rather than a KeyError, so
        the proposed row genuinely carries NULL — asserted below on the values.
        `SET col = EXCLUDED.col` would write that NULL straight over a good
        prediction; COALESCE against the target row is what makes the write a
        no-op instead. A prediction is never legitimately NULL (`category_best`
        always carries a value, even 'unknown'), so nothing is lost.

        Scope, stated so this test is not over-read: it covers the UPDATE branch.
        The pipeline's own caller archives and TRUNCATEs `assets` first, so
        ON CONFLICT never fires there and every row is a plain INSERT — what
        keeps the snapshot alive across a rebuild is s13 populating it on every
        run (tested below), not this clause.
        """
        sql_text, values = upsert([{'id': 'a1'}])  # no predicted keys at all

        idx = _insert_columns(sql_text).index(column)
        assert values[0][idx] is None, "a missing key must reach the DB as NULL"
        assert (
            '"{c}" = COALESCE(EXCLUDED."{c}", assets."{c}")'.format(c=column)
            in sql_text
        )
        assert '"{c}" = EXCLUDED."{c}"'.format(c=column) not in sql_text

    def test_every_other_column_still_takes_the_proposed_value(self, upsert):
        """Guards the guard: COALESCE-everything would make the upsert unable to
        ever clear a field, which is a different and much larger bug."""
        sql_text, _ = upsert([{'id': 'a1'}])

        assert '"category" = EXCLUDED."category"' in sql_text
        assert '"subcategory" = EXCLUDED."subcategory"' in sql_text
        assert 'COALESCE(EXCLUDED."category",' not in sql_text


# ── The pipeline stage ────────────────────────────────────────────────────────

def _service(overrides_enabled=False):
    """A pipeline service with every optional sub-stage switched off.

    MagicMock attributes are truthy, so each flag the stage branches on has to be
    set explicitly or the stage would try to run the real predictors.
    """
    svc = MagicMock()
    svc.enable_rule_based_prediction = False
    svc.category_predictor = None
    svc.knowledge_base = None
    svc.enable_ml_prediction = False
    svc.catboost_predictor = None
    svc.enable_category_overrides = overrides_enabled
    svc.category_override_repo = MagicMock() if overrides_enabled else None
    return svc


def _ctx(**columns):
    df = pd.DataFrame({'id': ['a1'], **columns})
    return SimpleNamespace(
        aggregated_df=df,
        enriched_df=df,
        aggregated_predictions=None,
        stats={'stages': {}},
    )


class TestTheStagePopulatesThePredictionEveryRun:

    def test_a_run_with_overrides_disabled_still_snapshots_both_fields(self):
        """The flag's default is False (asset_generation_service.py) — only the
        scheduled trigger passes True. Leaving the population inside
        apply_overrides meant most runs wrote no prediction at all, and once the
        upsert carries the columns that would push NULL at the database on every
        such run."""
        stage = CategorySelectionStage(_service(overrides_enabled=False))
        ctx = _ctx(category_best=['server'], subcategory_best=['linux'])

        stage._execute(ctx)

        assert list(ctx.aggregated_df['category_predicted']) == ['server']
        assert list(ctx.aggregated_df['subcategory_predicted']) == ['linux']

    def test_the_snapshot_is_taken_before_overrides_are_applied(self):
        """Ordering is the whole point: snapshot the override and clearing it
        becomes a no-op that looks like a bug."""
        svc = _service(overrides_enabled=True)
        seen = {}

        def _capture(df):
            seen['category'] = list(df['category_predicted'])
            seen['subcategory'] = list(df['subcategory_predicted'])
            return df

        svc._apply_category_overrides.side_effect = _capture
        stage = CategorySelectionStage(svc)
        ctx = _ctx(category_best=['server'], subcategory_best=['linux'])

        stage._execute(ctx)

        assert seen == {'category': ['server'], 'subcategory': ['linux']}

    def test_it_falls_back_to_the_post_rename_column_names(self):
        """`category_best` becomes `category` in s14. The stage runs before that,
        but the resolution is shared with apply_overrides, which can be handed
        either shape."""
        stage = CategorySelectionStage(_service(overrides_enabled=False))
        ctx = _ctx(category=['server'], subcategory=['linux'])

        stage._execute(ctx)

        assert list(ctx.aggregated_df['category_predicted']) == ['server']
        assert list(ctx.aggregated_df['subcategory_predicted']) == ['linux']

    def test_a_frame_with_no_category_at_all_is_left_alone(self):
        """Rule-based prediction can be off with no fallback configured; the
        stage must not invent columns or raise."""
        stage = CategorySelectionStage(_service(overrides_enabled=False))
        ctx = _ctx(hostname=['h1'])

        stage._execute(ctx)

        assert 'category_predicted' not in ctx.aggregated_df.columns
        assert 'subcategory_predicted' not in ctx.aggregated_df.columns


# ── The repository, where the pre-override property lives ─────────────────────

class TestApplyOverridesKeepsThePredictionPreOverride:

    def _repo(self):
        return CategoryOverrideRepository(MagicMock())

    def test_the_prediction_survives_the_override_that_replaces_it(self):
        df = pd.DataFrame({
            'id': ['a1'], 'category_best': ['server'], 'subcategory_best': ['linux'],
        })

        out = self._repo().apply_overrides(
            df, {'a1': {'category': 'network', 'subcategory': 'switch'}},
        )

        assert out['category_best'][0] == 'network'      # override applied
        assert out['subcategory_best'][0] == 'switch'
        assert out['category_predicted'][0] == 'server'  # prediction preserved
        assert out['subcategory_predicted'][0] == 'linux'

    def test_the_no_overrides_branch_populates_both_columns_too(self):
        """The early return at the top of apply_overrides. It used to set only
        category_predicted, and only when the `_best` column was present."""
        df = pd.DataFrame({'id': ['a1'], 'category': ['server'], 'subcategory': ['linux']})

        out = self._repo().apply_overrides(df, {})

        assert out['category_predicted'][0] == 'server'
        assert out['subcategory_predicted'][0] == 'linux'
        assert not out['category_is_overridden'][0]


# ── The snapshot must survive s14's validation ────────────────────────────────

class TestTheSnapshotAgreesWithTheValidatedCategory:
    """s13 snapshots at 7.6c; s14 then remaps any category the CategorySelector
    let through but VALID_CATEGORIES rejects. Persisting the snapshot (#1154)
    means that remap has to reach it too — otherwise clearing an override writes
    a category the Explorer's own facet does not list, and it survives until the
    next run remaps the live value again.
    """

    def _stage_and_ctx(self, **columns):
        svc = MagicMock()
        # Let the real frame through the three service hops _execute makes; a
        # bare MagicMock would replace scored_df with a mock and the column
        # logic under test would never run.
        svc._populate_lifecycle_metadata.side_effect = lambda df, run_id: df
        svc._calculate_scores.side_effect = lambda df, expanded: df
        svc._disambiguate_ios_os.side_effect = lambda df: df
        df = pd.DataFrame({'id': ['a1'], 'confidence': [0.9], **columns})
        ctx = SimpleNamespace(
            aggregated_df=df, expanded_df=pd.DataFrame(), scored_df=None,
            generation_run_id='11111111-1111-1111-1111-111111111111',
            stats={'stages': {}}, track_changes=False,
        )
        return ScoringStage(svc), ctx

    def test_an_invalid_prediction_is_remapped_exactly_like_the_live_value(self):
        stage, ctx = self._stage_and_ctx(
            category_best=['bogus'], subcategory_best=['unknown'],
            category_predicted=['bogus'], subcategory_predicted=['unknown'],
        )

        stage._execute(ctx)

        # The live value's existing treatment, unchanged...
        assert ctx.scored_df['category'][0] == 'miscellaneous'
        assert ctx.scored_df['subcategory'][0] == 'bogus'
        # ...and the snapshot now matches it instead of keeping 'bogus'.
        assert ctx.scored_df['category_predicted'][0] == 'miscellaneous'
        assert ctx.scored_df['subcategory_predicted'][0] == 'bogus'

    def test_a_valid_prediction_is_left_alone(self):
        """Guards the guard: remapping unconditionally would destroy every
        legitimate prediction."""
        stage, ctx = self._stage_and_ctx(
            category_best=['server'], subcategory_best=['linux'],
            category_predicted=['server'], subcategory_predicted=['linux'],
        )

        stage._execute(ctx)

        assert ctx.scored_df['category_predicted'][0] == 'server'
        assert ctx.scored_df['subcategory_predicted'][0] == 'linux'


# ── Chronicle churn ───────────────────────────────────────────────────────────

class TestThePredictionIsNotAChangeEvent:

    @pytest.mark.parametrize("column", PREDICTED_COLUMNS)
    def test_the_snapshot_columns_stay_out_of_the_diff(self, column):
        """Persisting these made them eligible for the chronicle diff, which
        compares every key not in this set. The pipeline recomputes its
        prediction on every run, so tracking them would write a change event for
        every asset on every run — pure churn, on a table that already carries
        the user-visible category/subcategory changes that do mean something."""
        assert column in ChronicleService.DEFAULT_EXCLUDED_FIELDS
