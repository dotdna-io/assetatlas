# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from datetime import datetime, timezone
from src.modules.merge_denylist.service import MergeDenylistService
from src.modules.merge_denylist.models import DenylistRow


class FakeRepo:
    def __init__(self):
        self.rows = {}
        self.bounds = {}  # row id -> (range_start, range_end), as the DB columns
        self._n = 0
        self.create_calls = 0
        self.create_idempotent_calls = 0

    def _build_row(
        self, *, identifier_type, identifier_value, raw_value, reason, created_by,
        match_type="exact", range_start=None, range_end=None,
    ):
        self._n += 1
        row = DenylistRow(
            id=f"id-{self._n}",
            identifier_type=identifier_type,
            match_type=match_type,
            identifier_value=identifier_value,
            raw_value=raw_value,
            reason=reason,
            created_by=created_by,
            enabled=True,
            created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        self.rows[row.id] = row
        self.bounds[row.id] = (range_start, range_end)
        return row

    def create(
        self, *, identifier_type, identifier_value, raw_value, reason, created_by,
        match_type="exact", range_start=None, range_end=None,
    ):
        self.create_calls += 1
        return self._build_row(
            identifier_type=identifier_type,
            match_type=match_type,
            identifier_value=identifier_value,
            raw_value=raw_value,
            reason=reason,
            created_by=created_by,
            range_start=range_start,
            range_end=range_end,
        )

    def create_idempotent(
        self, *, identifier_type, identifier_value, raw_value, reason, created_by,
        match_type="exact", range_start=None, range_end=None,
    ):
        # Mirrors ON CONFLICT (identifier_type, identifier_value, match_type)
        # DO NOTHING: None on conflict, never raises.
        self.create_idempotent_calls += 1
        for row in self.rows.values():
            if (
                row.identifier_type == identifier_type
                and row.identifier_value == identifier_value
                and row.match_type == match_type
            ):
                return None
        return self._build_row(
            identifier_type=identifier_type,
            match_type=match_type,
            identifier_value=identifier_value,
            raw_value=raw_value,
            reason=reason,
            created_by=created_by,
            range_start=range_start,
            range_end=range_end,
        )

    def list_all(self):
        return list(self.rows.values())

    def delete(self, entry_id):
        return self.rows.pop(entry_id, None) is not None


def test_add_entry_encodes_value():
    svc = MergeDenylistService(FakeRepo())
    out = svc.add_entry(
        identifier_type="serial", raw_value="ABC123", reason="dup", created_by="admin"
    )
    assert out["identifier_value"] == "sn:abc123"
    assert out["raw_value"] == "ABC123"
    assert isinstance(out["created_at"], str)  # JSON-ready


def test_add_entry_bad_mac_raises_valueerror():
    svc = MergeDenylistService(FakeRepo())
    with pytest.raises(ValueError):
        svc.add_entry(
            identifier_type="mac", raw_value="nope", reason=None, created_by="admin"
        )


def test_list_and_remove():
    repo = FakeRepo()
    svc = MergeDenylistService(repo)
    row = svc.add_entry(
        identifier_type="hostname", raw_value="web01", reason=None, created_by="admin"
    )
    assert any(e["id"] == row["id"] for e in svc.list_entries())
    assert svc.remove_entry(row["id"]) is True
    assert svc.remove_entry(row["id"]) is False


def test_add_entry_idempotent_routes_to_create_idempotent():
    # asset-split calls add_entry(..., idempotent=True) so a re-denylisted
    # bridge component can never raise into its request transaction scope.
    repo = FakeRepo()
    svc = MergeDenylistService(repo)
    out = svc.add_entry(
        identifier_type="serial",
        raw_value="ABC123",
        reason="dup",
        created_by="admin",
        idempotent=True,
    )
    assert repo.create_idempotent_calls == 1
    assert repo.create_calls == 0
    assert out["identifier_value"] == "sn:abc123"

    # Same (identifier_type, identifier_value) again: the fake reports a
    # conflict (None), mirroring ON CONFLICT DO NOTHING -- must not raise.
    again = svc.add_entry(
        identifier_type="serial",
        raw_value="ABC123",
        reason="dup",
        created_by="admin",
        idempotent=True,
    )
    assert again is None
    assert repo.create_idempotent_calls == 2
    assert repo.create_calls == 0


def test_add_entry_default_routes_to_create():
    # Unchanged path: idempotent defaults to False, so add_entry must still
    # go through create() (which raises ConflictError on a real duplicate).
    repo = FakeRepo()
    svc = MergeDenylistService(repo)
    out = svc.add_entry(
        identifier_type="serial", raw_value="ABC123", reason="dup", created_by="admin"
    )
    assert repo.create_calls == 1
    assert repo.create_idempotent_calls == 0
    assert out["identifier_value"] == "sn:abc123"


# ---------------------------------------------------------------------------
# Pattern entries (#991)
# ---------------------------------------------------------------------------

def test_add_entry_defaults_to_exact():
    # Callers that predate #991 (asset split) pass no match_type and must keep
    # producing exact entries.
    svc = MergeDenylistService(FakeRepo())
    out = svc.add_entry(
        identifier_type="hostname", raw_value="web01", reason=None, created_by="admin"
    )
    assert out["match_type"] == "exact"
    assert out["identifier_value"] == "web01"


def test_add_entry_encodes_a_pattern():
    svc = MergeDenylistService(FakeRepo())
    out = svc.add_entry(
        identifier_type="mac",
        raw_value="BC-24-11-*",
        match_type="prefix",
        reason="proxmox guest veth",
        created_by="admin",
    )
    assert out["match_type"] == "prefix"
    assert out["identifier_value"] == "bc:24:11:*"
    assert out["raw_value"] == "BC-24-11-*"  # what the admin typed, for display


def test_add_entry_range_stores_bounds_and_a_display_value():
    repo = FakeRepo()
    svc = MergeDenylistService(repo)
    out = svc.add_entry(
        identifier_type="mac",
        raw_value=None,
        match_type="range",
        range_start="BC:24:11:00:00:00",
        range_end="BC:24:11:FF:FF:FF",
        reason=None,
        created_by="admin",
    )
    assert out["match_type"] == "range"
    # identifier_value is the canonical '<start>-<end>' spelling, so a range is
    # uniqueness-keyed like every other entry.
    assert out["identifier_value"] == "bc:24:11:00:00:00-bc:24:11:ff:ff:ff"
    # raw_value shows both of the things the admin typed.
    assert out["raw_value"] == "BC:24:11:00:00:00 - BC:24:11:FF:FF:FF"
    # The 48-bit bounds go to the DB columns the matcher bisects.
    assert repo.bounds[out["id"]] == (0xBC24_1100_0000, 0xBC24_11FF_FFFF)


def test_add_entry_rejects_an_illegal_pattern():
    svc = MergeDenylistService(FakeRepo())
    with pytest.raises(ValueError):
        svc.add_entry(
            identifier_type="serial",
            raw_value="abc*",
            match_type="prefix",
            reason=None,
            created_by="admin",
        )


def test_same_value_with_different_match_types_are_distinct_entries():
    # The DB keys uniqueness on (identifier_type, identifier_value, match_type),
    # so an exact 'web01' and a prefix 'web01*' must not collide.
    repo = FakeRepo()
    svc = MergeDenylistService(repo)
    exact = svc.add_entry(
        identifier_type="hostname", raw_value="web01", reason=None,
        created_by="admin", idempotent=True,
    )
    pattern = svc.add_entry(
        identifier_type="hostname", raw_value="web01*", match_type="prefix",
        reason=None, created_by="admin", idempotent=True,
    )
    assert exact is not None and pattern is not None
    assert exact["id"] != pattern["id"]
