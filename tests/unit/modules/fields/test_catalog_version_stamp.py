# SPDX-License-Identifier: AGPL-3.0-or-later
"""FieldCatalog freshness via the shared version stamp (#1413).

With a version source available, the TTL drops to a safety net
(``ttl_seconds_with_stamp``) and cross-worker invalidation is driven by the
stamp: ``invalidate()`` on one catalog bumps, and every other catalog sharing
the source rebuilds on its next read past the throttled check interval.
Without a working source (None on every read — no Redis, TestConfig) the
catalog behaves exactly as it always has: the plain 60s TTL, no exceptions.

"Workers" here are two FieldCatalog instances sharing one fake source and one
fake clock — the same shape as two gunicorn workers sharing Redis.
"""

from unittest.mock import MagicMock

from src.modules.fields import catalog_version
from src.modules.fields.catalog import FieldCatalog


def make_db():
    """One mock db_adapter per catalog, so builds are countable per instance."""
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


# One build runs 3 discovery queries (extended_attributes, tags, posture).
QUERIES_PER_BUILD = 3


class FakeVersionSource:
    """In-memory stand-in for the Redis stamp, shared by the 'workers'."""

    def __init__(self, version=1):
        self.version = version
        self.get_calls = 0
        self.bump_calls = 0

    def get(self):
        self.get_calls += 1
        return self.version

    def bump(self):
        self.bump_calls += 1
        self.version += 1


class NoneVersionSource:
    """A source that is never available (no Redis / TestConfig shape)."""

    def __init__(self):
        self.bump_calls = 0

    def get(self):
        return None

    def bump(self):
        self.bump_calls += 1


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _catalog(source, clock, db=None, **kw):
    return FieldCatalog(
        db_adapter=db or make_db(),
        version_source=source,
        clock=clock,
        **kw,
    )


class TestCrossWorkerInvalidation:
    def test_invalidate_on_a_rebuilds_b_after_check_interval(self):
        source, clock = FakeVersionSource(), FakeClock()
        a_db, b_db = make_db(), make_db()
        a = _catalog(source, clock, db=a_db)
        b = _catalog(source, clock, db=b_db)
        a_entries = a.entries()
        b.entries()
        assert b_db.execute_query.call_count == QUERIES_PER_BUILD

        a.invalidate()
        assert source.bump_calls == 1

        # Within the check interval B has not re-polled the stamp yet...
        clock.advance(1.0)
        b.entries()
        assert b_db.execute_query.call_count == QUERIES_PER_BUILD

        # ...past it, the next read sees the bump and rebuilds — with content
        # identical to A's, only fresher.
        clock.advance(1.1)
        b_entries = b.entries()
        assert b_db.execute_query.call_count == 2 * QUERIES_PER_BUILD
        assert b_entries == a_entries

    def test_without_bump_b_serves_its_snapshot_for_the_full_ttl(self):
        source, clock = FakeVersionSource(), FakeClock()
        b_db = make_db()
        b = _catalog(source, clock, db=b_db)
        b.entries()
        assert b_db.execute_query.call_count == QUERIES_PER_BUILD

        # No bump anywhere: the stamped snapshot lives for the LONG safety-net
        # TTL (900s default) — in particular, nothing happens at the old 60s.
        clock.advance(61.0)
        b.entries()
        assert b_db.execute_query.call_count == QUERIES_PER_BUILD
        clock.advance(838.0)  # 899s total — still inside
        b.entries()
        assert b_db.execute_query.call_count == QUERIES_PER_BUILD
        clock.advance(2.0)  # 901s total — safety net expires
        b.entries()
        assert b_db.execute_query.call_count == 2 * QUERIES_PER_BUILD


class TestBumpDuringBuildIsNotLost:
    def test_bump_landing_mid_build_forces_a_rebuild_that_sees_the_edit(self):
        """Round-2 review repro (#1426): worker B is mid-build when worker A
        appends a tag row and bumps the shared stamp. The stamp is read
        BEFORE _build(), so B's snapshot carries the pre-edit version and the
        next poll past the check interval mismatches — B rebuilds and serves
        the new tag. (Read-after-build stamped the new version onto pre-edit
        content and lost the update until the 900s safety net.)"""
        source, clock = FakeVersionSource(), FakeClock()
        tags = ["ProductionAsset"]
        db = MagicMock()

        def execute_query(sql, _params=None):
            sql_lower = sql.lower()
            if "jsonb_each(extended_attributes)" in sql_lower:
                return [{"source_name": "Intune Full", "field_name": "deviceType"}]
            if "asset_tags_v2" in sql_lower and "asset_manual_tags" in sql_lower:
                return [{"tag_name": t} for t in tags]
            if "posture_categories" in sql_lower:
                return [{"id": "secured"}]
            return []

        db.execute_query.side_effect = execute_query
        b = _catalog(source, clock, db=db)

        real_build = b._build
        build_calls = []

        def build_while_a_edits():
            built = real_build()  # B's provider queries run on pre-edit data
            build_calls.append(1)
            if len(build_calls) == 1:
                # Worker A's commit + invalidate() land while B is mid-build.
                tags.append("EmergencyTag")
                source.bump()
            return built

        b._build = build_while_a_edits

        first = b.entries()
        assert len(build_calls) == 1
        # B built from pre-edit data, and the snapshot carries the PRE-build
        # stamp (1), not the value A bumped to mid-build (2).
        assert "Tags - EmergencyTag" not in {e.display_name for e in first}
        assert source.version == 2
        assert b._snapshot.version == 1

        # Within the check interval the throttled poll has not re-read yet.
        clock.advance(1.0)
        b.entries()
        assert len(build_calls) == 1

        # Past it, the poll mismatches the pre-build stamp: B rebuilds and
        # picks up A's tag.
        clock.advance(1.1)
        rebuilt = b.entries()
        assert len(build_calls) == 2
        assert "Tags - EmergencyTag" in {e.display_name for e in rebuilt}
        assert b._snapshot.version == 2


class TestStampReadThrottle:
    def test_entries_polls_the_source_at_most_once_per_interval(self):
        source, clock = FakeVersionSource(), FakeClock()
        cat = _catalog(source, clock)
        cat.entries()
        assert source.get_calls == 1  # the unthrottled read at build

        for _ in range(1000):  # well within one 2s interval
            clock.advance(0.0009)
            cat.entries()
        assert source.get_calls == 1

        clock.advance(2.0)  # interval elapsed: exactly one more poll
        cat.entries()
        assert source.get_calls == 2


class TestUnavailableSource:
    def test_none_source_behaves_like_the_legacy_ttl(self):
        """Source present but never answering (no Redis): 60s TTL, no errors."""
        source, clock = NoneVersionSource(), FakeClock()
        db = make_db()
        cat = _catalog(source, clock, db=db)
        cat.entries()
        assert db.execute_query.call_count == QUERIES_PER_BUILD

        clock.advance(59.0)
        cat.entries()
        assert db.execute_query.call_count == QUERIES_PER_BUILD
        clock.advance(2.0)  # 61s: legacy TTL expires
        cat.entries()
        assert db.execute_query.call_count == 2 * QUERIES_PER_BUILD

    def test_no_source_at_all_behaves_like_the_legacy_ttl(self):
        """version_source=None (hand-built catalog): pure TTL, zero stamp logic."""
        clock = FakeClock()
        db = make_db()
        cat = FieldCatalog(db_adapter=db, clock=clock)
        cat.entries()
        clock.advance(59.0)
        cat.entries()
        assert db.execute_query.call_count == QUERIES_PER_BUILD
        clock.advance(2.0)
        cat.entries()
        assert db.execute_query.call_count == 2 * QUERIES_PER_BUILD

    def test_source_recovering_mid_life_triggers_rebuild(self):
        """Built while Redis was down (version None): when the source comes
        back the value differs from None, so the catalog rebuilds and moves
        onto the long TTL — it does not stay degraded for a full TTL."""

        class RecoveringSource:
            def __init__(self):
                self.value = None

            def get(self):
                return self.value

            def bump(self):
                pass

        source, clock = RecoveringSource(), FakeClock()
        db = make_db()
        cat = _catalog(source, clock, db=db)
        cat.entries()
        source.value = 3  # Redis back
        clock.advance(2.1)
        cat.entries()
        assert db.execute_query.call_count == 2 * QUERIES_PER_BUILD
        # ...and the fresh snapshot is now stamped, on the long TTL.
        clock.advance(61.0)
        cat.entries()
        assert db.execute_query.call_count == 2 * QUERIES_PER_BUILD


class TestInvalidateBumps:
    def test_invalidate_bumps_the_injected_source(self):
        source = FakeVersionSource()
        cat = _catalog(source, FakeClock())
        cat.invalidate()
        assert source.bump_calls == 1

    def test_invalidate_without_source_bumps_the_module_default(self, monkeypatch):
        """Hand-built catalogs (no injected source) still propagate via the
        module default, so no caller of invalidate() needs to change."""
        bumped = []
        monkeypatch.setattr(catalog_version, "bump", lambda: bumped.append(1))
        FieldCatalog(db_adapter=make_db()).invalidate()
        assert bumped == [1]
