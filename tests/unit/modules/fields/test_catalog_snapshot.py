# SPDX-License-Identifier: AGPL-3.0-or-later
"""Snapshot atomicity and invalidation races in FieldCatalog (#1413).

The catalog is shared by every request thread in a gunicorn worker. Three
latent races lived in the old ``_cache``/``_index``/``_loaded_at``/``_disp_idx``
handling:

1. ``index()`` read ``self._index`` AFTER ``entries()`` returned; an
   ``invalidate()`` in that gap yielded None → AttributeError in the resolver.
2. ``invalidate()`` skipped the build lock, so one landing mid-build was
   overwritten by the build's install — the edit stayed invisible for a TTL.
3. The display indexes had their own unlocked TTL stamp, so they could be
   built twice and disagree with the entry list they index.

All three are fixed by holding everything in one immutable ``_Snapshot``,
replaced atomically, with ``invalidate()`` serialised on the build lock.
"""

import threading
import time
from unittest.mock import MagicMock

import pytest

from src.modules.fields.catalog import FieldCatalog


@pytest.fixture
def mock_db():
    """Mock db_adapter whose execute_query returns one preset per query pattern."""
    db = MagicMock()

    def execute_query(sql, _params=None):
        sql_lower = sql.lower()
        if "jsonb_each(extended_attributes)" in sql_lower:
            return [{"source_name": "Intune Full", "field_name": "deviceType"}]
        if "asset_tags_v2" in sql_lower and "asset_manual_tags" in sql_lower:
            return [{"tag_name": "ProductionAsset"}]
        if "posture_categories" in sql_lower:
            return [{"id": "secured"}]
        return []

    db.execute_query.side_effect = execute_query
    return db


class TestIndexNeverNone:
    def test_invalidate_between_entries_and_index_still_returns_dict(self, mock_db):
        """Race 1: index() must never return None while entries() returned a list.

        Old code: entries() populated _cache/_index, invalidate() cleared both,
        index() then returned self._index → None. Now index() reads the index
        from its own freshly-loaded snapshot, so the invalidate in the gap just
        means a rebuild — the result is always a dict.
        """
        cat = FieldCatalog(db_adapter=mock_db)
        first = cat.entries()
        assert first  # a non-empty list of entries was served
        cat.invalidate()
        idx = cat.index()
        assert isinstance(idx, dict)
        assert idx  # rebuilt, not None and not empty


class TestInvalidateDuringBuildIsNotLost:
    def test_next_entries_rebuilds(self, mock_db):
        """Race 2: an invalidate() landing mid-build must survive the build.

        The fake _build blocks on an Event so the test can call invalidate()
        while a build is provably in progress. invalidate() serialises on the
        build lock: it applies the moment the in-flight build installs, so the
        NEXT entries() call rebuilds instead of serving the pre-edit snapshot
        for a full TTL.
        """
        cat = FieldCatalog(db_adapter=mock_db, ttl_seconds=60)
        real_build = cat._build
        build_started = threading.Event()
        finish_build = threading.Event()
        build_calls = []

        def slow_build():
            build_calls.append(1)
            build_started.set()
            assert finish_build.wait(timeout=5), "test deadlock: build never released"
            return real_build()

        cat._build = slow_build

        first_result = []

        def builder():
            first_result.append(cat.entries())

        t_build = threading.Thread(target=builder)
        t_build.start()
        assert build_started.wait(timeout=5), "build never started"

        invalidator_done = threading.Event()

        def invalidator():
            cat.invalidate()
            invalidator_done.set()

        t_inv = threading.Thread(target=invalidator)
        t_inv.start()
        time.sleep(0.05)  # let invalidate() queue on the build lock
        finish_build.set()
        t_build.join()
        t_inv.join(timeout=5)
        assert invalidator_done.is_set()

        # The invalidation was not swallowed by the in-flight build's install:
        # the very next read rebuilds.
        cat.entries()
        assert len(build_calls) == 2, (
            f"{len(build_calls)} builds; the mid-build invalidate() was lost"
        )


class TestOneSnapshot:
    def test_accessors_share_one_snapshot(self, mock_db):
        """Race 3: entries/index/display indexes come from ONE build.

        The display indexes used to be built lazily under a separate stamp, so
        they could be computed twice and disagree with the entry list. Now all
        three accessors read the same immutable snapshot: one build feeds all
        of them (3 discovery queries total, no extra scan for the display
        indexes) until an explicit invalidate().
        """
        cat = FieldCatalog(db_adapter=mock_db)
        snap_before = cat._snapshot
        assert snap_before is None

        entries = cat.entries()
        idx = cat.index()
        fid = cat.field_id_for_display_name("Tags - ProductionAsset")

        assert cat._snapshot is not None
        assert entries == cat._snapshot.entries
        assert idx is cat._snapshot.index
        assert fid is not None and fid.kind == "tag"
        # One build = one round of the three discovery queries; the display
        # lookup and index() added no further scan.
        assert mock_db.execute_query.call_count == 3

    def test_invalidate_replaces_snapshot_atomically(self, mock_db):
        cat = FieldCatalog(db_adapter=mock_db)
        cat.entries()
        old_snap = cat._snapshot
        cat.invalidate()
        assert cat._snapshot is None
        cat.entries()
        assert cat._snapshot is not None
        assert cat._snapshot is not old_snap
        assert cat._snapshot.generation == old_snap.generation + 1
