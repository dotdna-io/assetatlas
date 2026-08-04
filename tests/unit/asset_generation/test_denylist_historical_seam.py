# SPDX-License-Identifier: AGPL-3.0-or-later
# tests/unit/asset_generation/test_denylist_historical_seam.py
import logging
from types import SimpleNamespace

import pandas as pd
import pytest

from src.modules.asset_generation.pipeline.stages.s03_load_mappings import LoadMappingsStage
from src.modules.merge_denylist.encoding import (
    to_canonical_component,
    to_canonical_range,
)
from src.modules.merge_denylist.matcher import DenylistMatcher


class _Svc:
    class _V:
        denylist_components = frozenset({"web01"})
    validator = _V()


def _matcher(*entries):
    """A DenylistMatcher over (identifier_type, raw_value, match_type) triples,
    built through the real encoder — the exact shape Stage 2 loads from the DB
    and stashes on the validator for this stage to re-read."""
    rows = []
    for identifier_type, raw, match_type in entries:
        rows.append({
            "identifier_type": identifier_type,
            "match_type": match_type,
            "identifier_value": to_canonical_component(
                identifier_type, raw, match_type
            ),
            "range_start": None,
            "range_end": None,
        })
    return DenylistMatcher(rows)


def test_validate_mappings_drops_denylisted():
    stage = LoadMappingsStage(_Svc())
    raw = {"web01": "id-A", "sn:aaa111": "id-A", "bc:24:11:22:33:44": "id-B"}
    validated, skipped = stage._validate_mappings(raw, frozenset({"web01"}))
    assert "web01" not in validated
    assert "sn:aaa111" in validated and "bc:24:11:22:33:44" in validated
    assert skipped["denylisted"] == 1


# ---------------------------------------------------------------------------
# Pattern entries (#991)
#
# This seam gets no type context — just a bare component string out of
# asset_id_mappings — so the matcher has to dispatch on canonical shape alone.
# ---------------------------------------------------------------------------

def test_exact_entry_still_drops_only_itself_through_the_matcher():
    stage = LoadMappingsStage(_Svc())
    raw = {"web01": "id-A", "web02": "id-B"}
    validated, skipped = stage._validate_mappings(
        raw, _matcher(("hostname", "web01", "exact"))
    )
    assert "web01" not in validated
    assert "web02" in validated
    assert skipped["denylisted"] == 1


def test_mac_prefix_drops_historical_mappings_in_the_range():
    # The durable half of the fix: a mapping recorded before the range was
    # denylisted must not keep re-seeding the over-merge it caused.
    stage = LoadMappingsStage(_Svc())
    raw = {
        "bc:24:11:00:00:01": "id-A",
        "bc:24:11:99:99:99": "id-A",
        "aa:bb:cc:dd:ee:ff": "id-B",
        "pve-node1": "id-C",
    }
    validated, skipped = stage._validate_mappings(
        raw, _matcher(("mac", "bc:24:11:*", "prefix"))
    )
    assert "bc:24:11:00:00:01" not in validated
    assert "bc:24:11:99:99:99" not in validated
    assert "aa:bb:cc:dd:ee:ff" in validated  # another vendor, kept
    assert "pve-node1" in validated  # a hostname, never probed against MAC keys
    assert skipped["denylisted"] == 2


def test_mac_range_drops_historical_mappings_within_the_bounds():
    stage = LoadMappingsStage(_Svc())
    value, start, end = to_canonical_range(
        "mac", "bc:24:11:00:00:00", "bc:24:11:00:00:10"
    )
    matcher = DenylistMatcher([{
        "identifier_type": "mac",
        "match_type": "range",
        "identifier_value": value,
        "range_start": start,
        "range_end": end,
    }])
    raw = {
        "bc:24:11:00:00:10": "id-A",  # the inclusive end
        "bc:24:11:00:00:11": "id-B",  # one past it
    }
    validated, skipped = stage._validate_mappings(raw, matcher)
    assert "bc:24:11:00:00:10" not in validated
    assert "bc:24:11:00:00:11" in validated
    assert skipped["denylisted"] == 1


def test_hostname_prefix_and_fqdn_suffix_drop_historical_mappings():
    stage = LoadMappingsStage(_Svc())
    raw = {
        "vdi-pool-01": "id-A",
        "host.corp.local": "id-B",
        "web01": "id-C",
        "host.corp.example": "id-D",
    }
    validated, skipped = stage._validate_mappings(
        raw,
        _matcher(
            ("hostname", "vdi*", "prefix"),
            ("fqdn", "*.corp.local", "suffix"),
        ),
    )
    assert "vdi-pool-01" not in validated
    assert "host.corp.local" not in validated
    assert "web01" in validated
    assert "host.corp.example" in validated
    assert skipped["denylisted"] == 2


@pytest.mark.parametrize(
    "component,denylisted",
    [
        # A serial is exact-only, so a name pattern must never reach it — the
        # 'sn:' shape is the only thing telling them apart at this seam.
        ("sn:vdi-01", False),
        ("vdi-01", True),
    ],
)
def test_shape_dispatch_keeps_serials_out_of_name_patterns(component, denylisted):
    stage = LoadMappingsStage(_Svc())
    validated, _ = stage._validate_mappings(
        {component: "id-A"}, _matcher(("hostname", "vdi*", "prefix"))
    )
    assert (component not in validated) is denylisted


# ---------------------------------------------------------------------------
# _execute wiring (#973)
#
# Everything above drives _validate_mappings directly. These cover what _execute
# does around it: read the denylist off the validator, hand the survivors on,
# and account for what it dropped.
# ---------------------------------------------------------------------------

class _Identifier:
    def __init__(self):
        self.loaded = None

    def load_existing_mappings(self, mappings):
        self.loaded = mappings


class _ExecSvc:
    """The three collaborators _execute reaches through."""

    def __init__(self, raw_mappings, validator):
        self.mapping_repo = SimpleNamespace(load_all_mappings=lambda: raw_mappings)
        self.validator = validator
        self.identifier = _Identifier()


class _ExecCtx:
    def __init__(self, asset_field_values):
        self.asset_field_df = pd.DataFrame({"asset": asset_field_values})
        self.stats = {"stages": {}}


# 'my server' has internal whitespace, so is_valid_component_value rejects it —
# a mapping recorded before that rule existed, which is what 'filtered' counts.
_RAW = {
    "web01": "id-A",
    "sn:aaa111": "id-A",
    "bc:24:11:22:33:44": "id-B",
    "my server": "id-C",
}


def test_execute_filters_with_the_denylist_stage_2_left_on_the_validator():
    # The stages are coupled only through an attribute on a shared validator, so
    # nothing in the type system says this reads what Stage 2 wrote. Because the
    # read is a getattr with a default, losing that coupling — a rename, a
    # validator swap — would not raise here; it would silently filter nothing and
    # let every denylisted component go back to seeding merges.
    svc = _ExecSvc(_RAW, SimpleNamespace(denylist_components=frozenset({"web01"})))
    ctx = _ExecCtx(["web01|sn:aaa111", "BC:24:11:22:33:44"])
    LoadMappingsStage(svc)._execute(ctx)

    assert "web01" not in ctx.existing_mappings
    assert set(ctx.existing_mappings) == {"sn:aaa111", "bc:24:11:22:33:44"}
    # The survivors are what UnionFind actually groups on, so the stage has to
    # hand the filtered dict on, not the raw one it read.
    assert svc.identifier.loaded == ctx.existing_mappings


def test_execute_defaults_to_no_denylist_when_the_validator_carries_none():
    # getattr's default is a guard, not a live path: AssetValidator.__init__
    # seeds the attribute and Stage 2 overwrites it (with frozenset() when the
    # read fails). It is pinned because it is what fails open — a validator that
    # never went through Stage 2 still loads mappings instead of raising
    # AttributeError mid-pipeline.
    svc = _ExecSvc(_RAW, SimpleNamespace())
    ctx = _ExecCtx(["web01"])
    LoadMappingsStage(svc)._execute(ctx)

    assert "web01" in ctx.existing_mappings
    assert ctx.stats["stages"]["load_mappings"]["mappings_denylisted"] == 0


def test_execute_records_what_it_dropped_and_why():
    # The stats block is the only account of a denylist entry doing its job: the
    # component is gone from the mappings, so nothing downstream can report it.
    # 'filtered' (stale) and 'denylisted' are counted apart because they mean
    # different things to an operator — one is data ageing out, the other is a
    # rule they wrote.
    svc = _ExecSvc(_RAW, SimpleNamespace(denylist_components=frozenset({"web01"})))
    ctx = _ExecCtx(["web01|sn:aaa111", "BC:24:11:22:33:44", None, ""])
    LoadMappingsStage(svc)._execute(ctx)

    assert ctx.stats["stages"]["load_mappings"] == {
        "mapping_count": 2,
        "mapping_count_raw": 4,
        "mappings_filtered": 1,      # 'my server'
        "mappings_denylisted": 1,    # 'web01'
        "components_in_dataset": 3,  # split on '|', lowercased; blanks dropped
    }
    assert ctx.all_components == {"web01", "sn:aaa111", "bc:24:11:22:33:44"}


def test_execute_logs_the_denylisted_count_only_when_something_was_dropped(caplog):
    # An operator's first question after adding an entry is whether it did
    # anything. A run that dropped nothing must stay quiet, or the line stops
    # meaning anything.
    clean = {"sn:aaa111": "id-A"}
    with caplog.at_level(logging.INFO):
        LoadMappingsStage(
            _ExecSvc(clean, SimpleNamespace(denylist_components=frozenset()))
        )._execute(_ExecCtx(["sn:aaa111"]))
    assert "denylisted" not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO):
        LoadMappingsStage(
            _ExecSvc(_RAW, SimpleNamespace(denylist_components=frozenset({"web01"})))
        )._execute(_ExecCtx(["web01"]))
    assert "Filtered 1 stale + 1 denylisted mappings" in caplog.text
