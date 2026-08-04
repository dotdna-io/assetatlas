# SPDX-License-Identifier: AGPL-3.0-or-later
"""The over_merged tell: concurrency, not a count (#973).

get_merge_keys flags an asset as possibly over-merged. Until #973 the tell was
`serial_count >= 2`, which missed the case the merge denylist mostly exists for:
a reused hostname (`localhost`, `web01`) gluing two boxes that report 0-1
serials between them, so the likeliest over-merges carried no badge at all.

Counting hostnames instead would have flagged every renamed host, because assets
legitimately accumulate names. Concurrency is the discriminator: a rename leaves
the names SEQUENTIAL (the old one stops being reported as the new one starts),
an over-merge leaves them CONCURRENT (both machines keep reporting their own
name, so both stay live).

These tests are the only place that distinction is held: the dev DB holds 41
days of history, far too short to contain renames, so no real dataset available
here can exercise it. If they go, the rule silently decays into a count.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.modules.assets.services.asset_service import (
    AssetService,
    _CONCURRENT_WITHIN,
    concurrent_identifiers,
)

# A real dev-DB pipeline-run stamp. A run stamps last_seen on every component in
# its snapshot, so everything still being reported shares this instant.
RUN = datetime(2026, 7, 8, 21, 24, 42, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def test_two_concurrently_live_serials_still_flag():
    # The one case the serial-only tell already caught; widening the rule must
    # not cost it. A serial should name exactly one device, so two reporting at
    # once means two devices got glued together.
    assert concurrent_identifiers([
        ("serial", "pf3k6v39", RUN),
        ("serial", "pw025phj", RUN),
    ]) == {"serial": ["pf3k6v39", "pw025phj"]}


def test_reused_hostname_flags_although_only_one_box_reports_a_serial():
    # The headline case the denylist exists for, and precisely what a serial
    # count misses: a reused name bridges two boxes and only one of them
    # reports a serial, so serial_count never reaches 2 and the old tell
    # stayed silent on the likeliest over-merge in the fleet.
    assert concurrent_identifiers([
        ("hostname", "localhost", RUN),   # the reused name that glued them
        ("hostname", "web01", RUN),       # box A, still reporting
        ("hostname", "web02", RUN),       # box B, still reporting
        ("serial", "only-one-box-has-one", RUN),
    ]) == {"hostname": ["localhost", "web01", "web02"]}


def test_a_rename_never_flags_however_many_names_it_leaves_behind():
    # Renames are why counting was rejected. This asset carries four names and
    # is one device: each name stopped being reported as the next started, so
    # only the current one is live. If this ever flags, the badge is back to
    # crying wolf on healthy hosts and operators learn to ignore it.
    assert concurrent_identifiers([
        ("hostname", "web01", RUN - 90 * DAY),
        ("hostname", "web01-old", RUN - 60 * DAY),
        ("hostname", "webserver01", RUN - 30 * DAY),
        ("hostname", "web-prod-01", RUN),
    ]) == {}


def test_a_name_that_went_quiet_does_not_pair_with_the_live_one():
    # last_seen is genuinely per-component, which is what makes the rule
    # possible: on dev-DB asset 09859628 one MAC stopped being reported on
    # 06-18 while every sibling stayed live to 07-08. A value that went quiet
    # is a device that left, not one sharing this asset right now.
    assert concurrent_identifiers([
        ("hostname", "still-reporting", RUN),
        ("hostname", "went-quiet-three-weeks-ago", RUN - 21 * DAY),
    ]) == {}


def test_tolerance_forgives_a_lagging_source_but_not_a_departed_device():
    # A component is stamped by the run that saw it, so a source that lags must
    # not read as a device that left; every integration syncs on a daily cron
    # and the tolerance leaves a week of slack. Asserted against the constant,
    # so retuning it stays legal but breaking the boundary does not.
    assert concurrent_identifiers([
        ("hostname", "web01", RUN),
        ("hostname", "web02", RUN - (_CONCURRENT_WITHIN - DAY)),
    ]) == {"hostname": ["web01", "web02"]}

    assert concurrent_identifiers([
        ("hostname", "web01", RUN),
        ("hostname", "web02", RUN - (_CONCURRENT_WITHIN + DAY)),
    ]) == {}


def test_a_dual_nic_laptop_is_not_an_over_merge():
    # Dev-DB asset CFRTE5091, a Lenovo ThinkPad: one hostname, one FQDN, one
    # serial and two live MACs -- Ethernet and Wi-Fi. Concurrent MACs are what
    # a healthy laptop looks like, so MAC does not vote. Letting it vote made
    # 5729 of 6435 dev-DB flags (89%) devices exactly like this one.
    assert concurrent_identifiers([
        ("hostname", "cfrte5091", RUN),
        ("fqdn", "cfrte5091.ce.hsi.local", RUN),
        ("serial", "pw025phj", RUN),
        ("mac", "20:c1:9b:e3:c0:a1", RUN),
        ("mac", "34:29:8f:79:37:93", RUN),
    ]) == {}


def test_a_live_mac_still_dates_the_asset_although_it_does_not_vote():
    # MAC does not vote, but it is still evidence the asset is being reported.
    # If the reference ignored MACs, these two hostnames -- both quiet for a
    # month -- would look like the newest thing here and flag.
    assert concurrent_identifiers([
        ("mac", "20:c1:9b:e3:c0:a1", RUN),
        ("hostname", "web01", RUN - 30 * DAY),
        ("hostname", "web02", RUN - 30 * DAY),
    ]) == {}


def test_a_short_name_and_its_fqdn_are_different_types_so_never_pair():
    # "short name vs FQDN" is one of the ways assets legitimately accumulate
    # names. decode_merge_component splits them into separate types and the
    # rule only compares within a type, so this healthy asset stays quiet.
    assert concurrent_identifiers([
        ("hostname", "web01", RUN),
        ("fqdn", "web01.corp.local", RUN),
    ]) == {}


def test_concurrency_is_measured_against_the_asset_not_the_wall_clock():
    # Anchoring to the asset's own newest sighting is what keeps the tell
    # working while ingestion is behind: the dev DB's newest stamp is 9 days
    # old and the pipeline once paused for 13.4 days, so a now() anchor with
    # any small tolerance would flag nothing at all. A decommissioned asset
    # whose names went quiet together is still two devices glued into one.
    retired = RUN - 365 * DAY
    assert concurrent_identifiers([
        ("hostname", "web01", retired),
        ("hostname", "web02", retired),
    ]) == {"hostname": ["web01", "web02"]}


def test_a_component_with_no_last_seen_is_not_evidence_of_concurrency():
    # asset_id_mappings.last_seen is NOT NULL, so this only guards hand-built
    # rows. The rule still needs positive evidence that two values were live at
    # once, and a missing window is not it: it must not flag on a guess.
    assert concurrent_identifiers([
        ("hostname", "web01", RUN),
        ("hostname", "web02", None),
    ]) == {}
    assert concurrent_identifiers([
        ("hostname", "web01", None),
        ("hostname", "web02", None),
    ]) == {}


def _rows(*components):
    return [
        {"component": c, "first_seen": RUN - 30 * DAY, "last_seen": ls}
        for c, ls in components
    ]


def test_get_merge_keys_names_the_identifiers_it_flagged_on():
    # The tell reports WHICH values fired it, so the operator knows what to
    # denylist instead of only that something is wrong. The badge is a prompt
    # for a human, not a verdict.
    repo = MagicMock()
    repo.get_merge_keys.return_value = _rows(
        ("localhost", RUN), ("web01", RUN), ("web02", RUN), ("sn:abc123", RUN),
    )

    tell = AssetService(repo).get_merge_keys("asset-id")["tell"]

    assert tell["over_merged"] is True
    assert tell["concurrent"] == {"hostname": ["localhost", "web01", "web02"]}
    assert tell["serial_count"] == 1


def test_get_merge_keys_stays_quiet_on_a_renamed_host():
    # End to end over the decoder: one device, renamed once, must carry no
    # badge -- while still listing both names so the operator sees the history.
    repo = MagicMock()
    repo.get_merge_keys.return_value = _rows(
        ("web01", RUN - 60 * DAY), ("web-prod-01", RUN),
    )

    out = AssetService(repo).get_merge_keys("asset-id")

    assert out["tell"] == {"serial_count": 0, "over_merged": False, "concurrent": {}}
    assert [k["value"] for k in out["keys"]] == ["web01", "web-prod-01"]
