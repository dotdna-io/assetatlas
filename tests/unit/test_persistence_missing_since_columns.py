# SPDX-License-Identifier: AGPL-3.0-or-later
"""missing_since must be in the persistence + archive column lists (#795)."""

import inspect

from src.database import postgres_adapter


def test_bulk_upsert_includes_missing_since():
    src = inspect.getsource(postgres_adapter.PostgreSQLAdapter.bulk_upsert_assets)
    assert "'missing_since'" in src


def test_archive_copies_missing_since():
    src = inspect.getsource(postgres_adapter.PostgreSQLAdapter.archive_all_to_history)
    # Present in both the INSERT column list and the SELECT list.
    assert src.count("missing_since") >= 2
