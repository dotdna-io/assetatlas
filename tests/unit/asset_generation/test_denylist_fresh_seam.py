# SPDX-License-Identifier: AGPL-3.0-or-later
import pandas as pd
from src.modules.asset_generation.domain.asset_validator import AssetValidator
from src.modules.merge_denylist.encoding import (
    to_canonical_component,
    to_canonical_range,
)
from src.modules.merge_denylist.matcher import DenylistMatcher


def _fields(df):
    return df["asset"].tolist()


def _matcher(*entries):
    """A DenylistMatcher over (identifier_type, raw_value, match_type) triples,
    built through the real encoder — the exact shape Stage 2 loads from the DB."""
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


def _range_matcher(start, end):
    value, start_int, end_int = to_canonical_range("mac", start, end)
    return DenylistMatcher([{
        "identifier_type": "mac",
        "match_type": "range",
        "identifier_value": value,
        "range_start": start_int,
        "range_end": end_int,
    }])


def test_denylisted_hostname_dropped_from_asset_field():
    v = AssetValidator()
    # MAC (not SN) is the per-row distinguishing identifier here. Two distinct
    # *valid* SNs sharing a hostname already trip the pre-existing "hostname
    # with multiple valid SNs" exclusion in _build_exclusion_sets (rule 5),
    # independent of the denylist under test here; two *suspicious* SNs (e.g.
    # low-entropy placeholders) dodge that rule but are themselves dropped by
    # the per-identifier SN check before assembly. Either way a shared-hostname
    # SN pair can't isolate the denylist behavior. MAC has no such rule.
    df = pd.DataFrame([
        {"hostname": "web01", "sn": None, "mac": "AA:BB:CC:DD:EE:01", "ip": None, "fqdn": None, "data_source": "s1"},
        {"hostname": "web01", "sn": None, "mac": "AA:BB:CC:DD:EE:02", "ip": None, "fqdn": None, "data_source": "s2"},
    ])
    # Baseline: 'web01' present in both asset fields -> they would merge.
    base = v.create_asset_field(df.copy())
    assert all("web01" in f for f in _fields(base))

    # With denylist: 'web01' stripped, each row keeps only its own MAC.
    v.denylist_components = frozenset({"web01"})
    out = v.create_asset_field(df.copy())
    fields = _fields(out)
    assert all("web01" not in f for f in fields)
    assert fields[0] == "aa:bb:cc:dd:ee:01"
    assert fields[1] == "aa:bb:cc:dd:ee:02"


def test_denylisted_only_identifier_yields_empty_field():
    v = AssetValidator()
    v.denylist_components = frozenset({"web01"})
    df = pd.DataFrame([{"hostname": "web01", "sn": None, "mac": None, "ip": None, "fqdn": None, "data_source": "s1"}])
    out = v.create_asset_field(df)
    assert out["asset"].tolist() == [""]  # documented caveat: record will be excluded downstream


# ---------------------------------------------------------------------------
# Pattern entries (#991) — same seam, driven by a real DenylistMatcher
# ---------------------------------------------------------------------------

def _proxmox_df():
    """The motivating case: a Proxmox host and an LXC guest whose veth MACs both
    come out of the bc:24:11 vendor range, so the MACs merge them into one
    Frankenstein asset."""
    return pd.DataFrame([
        {"hostname": "pve-node1", "sn": None, "mac": "bc:24:11:00:00:01",
         "ip": None, "fqdn": None, "data_source": "proxmox"},
        {"hostname": "lxc-web", "sn": None, "mac": "bc:24:11:00:00:02",
         "ip": None, "fqdn": None, "data_source": "unifi"},
    ])


def test_exact_entries_still_work_through_the_matcher():
    # The pre-#991 behavior, but reaching the seam as a DenylistMatcher rather
    # than a frozenset: an exact entry must still drop exactly its own component.
    v = AssetValidator()
    v.denylist_components = _matcher(("mac", "bc:24:11:00:00:01", "exact"))
    out = v.create_asset_field(_proxmox_df())
    fields = _fields(out)
    assert fields[0] == "pve-node1"  # only its own MAC dropped
    assert fields[1] == "lxc-web|bc:24:11:00:00:02"  # the other MAC untouched


def test_mac_prefix_drops_a_whole_vendor_range():
    v = AssetValidator()
    df = _proxmox_df()
    # Baseline: both rows carry a bc:24:11 MAC, which is what over-merges them.
    base = _fields(v.create_asset_field(df.copy()))
    assert all("bc:24:11" in f for f in base)

    # One prefix entry knocks out the range that neither host owns.
    v.denylist_components = _matcher(("mac", "bc:24:11:*", "prefix"))
    fields = _fields(v.create_asset_field(df.copy()))
    assert fields == ["pve-node1", "lxc-web"]  # only their own hostnames remain


def test_mac_prefix_spares_other_vendors():
    v = AssetValidator()
    v.denylist_components = _matcher(("mac", "bc:24:11:*", "prefix"))
    df = pd.DataFrame([
        {"hostname": "srv01", "sn": None, "mac": "aa:bb:cc:dd:ee:ff",
         "ip": None, "fqdn": None, "data_source": "s1"},
    ])
    assert _fields(v.create_asset_field(df)) == ["srv01|aa:bb:cc:dd:ee:ff"]


def test_mac_range_drops_only_the_macs_inside_it():
    v = AssetValidator()
    v.denylist_components = _range_matcher(
        "bc:24:11:00:00:00", "bc:24:11:00:00:01"
    )
    df = pd.DataFrame([
        {"hostname": "in-range", "sn": None, "mac": "bc:24:11:00:00:01",
         "ip": None, "fqdn": None, "data_source": "s1"},
        {"hostname": "past-end", "sn": None, "mac": "bc:24:11:00:00:02",
         "ip": None, "fqdn": None, "data_source": "s2"},
    ])
    fields = _fields(v.create_asset_field(df))
    assert fields[0] == "in-range"  # bound is inclusive, MAC dropped
    assert fields[1] == "past-end|bc:24:11:00:00:02"  # one past the end, kept


def test_hostname_prefix_drops_matching_hostnames():
    v = AssetValidator()
    v.denylist_components = _matcher(("hostname", "localhost*", "prefix"))
    df = pd.DataFrame([
        {"hostname": "localhost-01", "sn": None, "mac": "aa:bb:cc:dd:ee:01",
         "ip": None, "fqdn": None, "data_source": "s1"},
        {"hostname": "web01", "sn": None, "mac": "aa:bb:cc:dd:ee:02",
         "ip": None, "fqdn": None, "data_source": "s2"},
    ])
    fields = _fields(v.create_asset_field(df))
    assert fields[0] == "aa:bb:cc:dd:ee:01"  # hostname dropped
    assert fields[1] == "web01|aa:bb:cc:dd:ee:02"  # untouched


def test_fqdn_suffix_drops_the_fqdn_and_leaves_the_short_hostname():
    # The seam adds an fqdn AND a bridge component for its short name; a suffix
    # entry targets the fqdn only, so the short hostname must survive.
    v = AssetValidator()
    v.denylist_components = _matcher(("fqdn", "*.corp.local", "suffix"))
    df = pd.DataFrame([
        {"hostname": None, "sn": None, "mac": None, "ip": None,
         "fqdn": "web01.corp.local", "data_source": "s1"},
    ])
    assert _fields(v.create_asset_field(df)) == ["web01"]
