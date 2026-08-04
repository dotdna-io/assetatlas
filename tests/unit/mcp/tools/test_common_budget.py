# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the centralized context-budget helpers in ``_common`` (§3.5/§8-#2).

``overflow_note`` is the single source of truth for the "N more not shown" signal
that every list-shaped chat tool emits when it caps its text. Centralising it keeps
the wording from drifting (the same reason ``unknown_enum`` exists) and the contract
below is what the tool-level tests rely on: a capped result is a *different, narrower*
answer than the full set, so the omission must be loud — but a non-capped result must
NOT carry a spurious signal (which would push the model to re-query needlessly).
"""

from __future__ import annotations

from src.mcp.tools._common import CHAT_TEXT_ROW_CAP, overflow_note


def test_cap_matches_query_assets_reference():
    # The default text-row ceiling is single-sourced here and must equal the
    # reference implementation's, or the budgets silently diverge per surface.
    from src.mcp.tools.query_assets import _CHAT_LIST_TEXT_ROWS
    assert CHAT_TEXT_ROW_CAP == _CHAT_LIST_TEXT_ROWS == 25


def test_none_when_nothing_hidden():
    # shown == total → no hidden rows → no signal. A spurious "more not shown"
    # would make the model needlessly widen/re-query a complete answer.
    assert overflow_note(10, 10, noun="rows") is None
    assert overflow_note(3, 25, noun="rows") is None      # shown can exceed total
    assert overflow_note(0, 0, noun="rows") is None


def test_signal_reports_the_true_hidden_count():
    # The number must be total - shown (the real omission), not the page size.
    note = overflow_note(100, 25, noun="assets")
    assert note == "[75 more assets not shown — narrow with a filter.]"


def test_noun_is_verbatim_and_hint_is_honored():
    # The noun is used as-is (caller passes the plural) so the helper never
    # mangles irregular plurals ("category"→"categorys", "batch"→"batchs").
    assert overflow_note(5, 1, noun="categories", hint="see the artifact") == (
        "[4 more categories not shown — see the artifact.]")
