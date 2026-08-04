# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavior tests for the enriched get_asset detail path.

The chat get_asset must reach UI fidelity (AssetDetailsPage.tsx / InfoCards.tsx /
DataSources.tsx):

- resolve by a HUMAN identifier (hostname/serial/ip/...), not only UUID — and do
  it LOUDLY (no silent "pick the first" on ambiguity);
- show extended-attribute VALUES, not "N fields" counts (the old formatter);
- split private vs public IPs + geo country/region/city (from
  extended_attributes.AssetAtlas.*, exactly where the UI reads them);
- a posture matrix with the contributing provider badges (the UI derives this
  from each provider's source_category/type/name);
- providers grouped general/network/extended with last_sync;
- per-source conflicts (folding the old get_asset_source_breakdown);
- source / field_search bound the per-source ext-attr output.

format_asset_detail / compute_asset_sections are pure (no DB), so these run
without a database; resolve_asset_identifier patches AssetRepository.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.mcp.tools.assets import (
    compute_asset_sections,
    format_asset_detail,
    resolve_asset_identifier,
)

_UTC = timezone.utc


def _make_resolver():
    r = MagicMock()
    r.catalog.return_value.field_id_for_display_name.return_value = None
    return r


def _detail_asset(aid):
    return {
        "id": aid,
        "hostname": ["host01"], "fqdn": ["host01.corp"],
        "ip": ["10.0.0.5", "8.8.8.8"], "mac": ["aa:bb:cc:dd:ee:ff"],
        "sn": ["SN123"],
        "category": "workstation", "subcategory": "laptop",
        "os_normalized": "Windows 11", "mac_vendor_enriched": "Dell Inc.",
        "manufacturer": ["Dell"], "model": ["Latitude"],
        "owner": ["alice"], "location": ["NYC"],
        "confidence": Decimal("0.91"),
        "extended_attributes": {
            "AssetAtlas": {
                "ip_private": ["10.0.0.5"], "ip_public": ["8.8.8.8"],
                "geo_countries": ["United States"],
                "geo_regions": ["California"],
                "geo_cities": ["Mountain View"],
            },
        },
    }


def _detail_providers():
    return [
        {"data_source": "CrowdStrike", "source_name": "CrowdStrike Falcon",
         "source_type": "edr", "source_category": "Endpoint",
         "last_sync_at": datetime(2026, 4, 18, 8, 0, tzinfo=_UTC),
         "hostname": "host01", "fqdn": "host01.corp", "ip": "10.0.0.5",
         "mac": "aa:bb:cc:dd:ee:ff", "sn": "SN123", "os": "Windows 11",
         "manufacturer": "Dell", "model": "Latitude",
         "data_source_fields": {"cpu_model": "Xeon Gold", "ram_gb": 64}},
        {"data_source": "Intune", "source_name": "Microsoft Intune",
         "source_type": "mdm", "source_category": "Device Management",
         "last_sync_at": datetime(2026, 4, 19, 8, 0, tzinfo=_UTC),
         "hostname": "HOST01-alt",
         "data_source_fields": {"compliance_state": "compliant"}},
    ]


def _detail(aid, **kw):
    return format_asset_detail(
        _detail_asset(aid), asset_id=aid, tags=[],
        providers=_detail_providers(), **kw,
    )


# --- resolve_asset_identifier ------------------------------------------------

def test_resolve_identifier_uuid_passthrough_no_db():
    db = MagicMock()
    aid = str(uuid4())
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        got, err = resolve_asset_identifier(db, _make_resolver(), aid)
    assert got == aid and err is None
    MockRepo.return_value.query_assets.assert_not_called()


def test_resolve_identifier_hostname_single_match():
    db = MagicMock()
    aid = str(uuid4())
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.query_assets.return_value = (
            [{"id": aid, "hostname": ["host01"]}], 1)
        got, err = resolve_asset_identifier(db, _make_resolver(), "host01")
    assert got == aid and err is None
    assert MockRepo.return_value.query_assets.call_args.kwargs.get("q") == "host01"


def test_resolve_identifier_serial_single_match():
    db = MagicMock()
    aid = str(uuid4())
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.query_assets.return_value = (
            [{"id": aid, "hostname": ["host01"], "sn": ["SN123"]}], 1)
        got, err = resolve_asset_identifier(db, _make_resolver(), "SN123")
    assert got == aid and err is None


def test_resolve_identifier_no_match_is_loud():
    db = MagicMock()
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.query_assets.return_value = ([], 0)
        got, err = resolve_asset_identifier(db, _make_resolver(), "nope-host")
    assert got is None
    assert err and "nope-host" in err


def test_resolve_identifier_multiple_matches_disambiguates_not_silently_picks():
    """Ambiguity must surface the candidates (with UUIDs), never silently pick
    the first — picking would answer about a different asset than intended."""
    db = MagicMock()
    a1, a2 = str(uuid4()), str(uuid4())
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        MockRepo.return_value.query_assets.return_value = (
            [{"id": a1, "hostname": ["host01"]},
             {"id": a2, "hostname": ["host01-b"]}], 2)
        got, err = resolve_asset_identifier(db, _make_resolver(), "host01")
    assert got is None
    assert err and a1 in err and a2 in err


# --- format_asset_detail enrichment ------------------------------------------

def test_detail_renders_ext_attr_values_not_counts():
    aid = str(uuid4())
    out = _detail(aid)
    # The actual VALUES, not "2 fields".
    assert "Xeon Gold" in out
    assert "compliant" in out
    assert "2 fields" not in out


def test_detail_splits_private_and_public_ip():
    aid = str(uuid4())
    out = _detail(aid)
    assert "Private IP" in out and "10.0.0.5" in out
    assert "Public IP" in out and "8.8.8.8" in out


def test_detail_shows_geo_country_region_city():
    aid = str(uuid4())
    out = _detail(aid)
    assert "United States" in out
    assert "California" in out
    assert "Mountain View" in out


def test_detail_posture_matrix_has_pass_with_provider_badge_and_a_fail():
    """Endpoint Security passes via the EDR provider (badge shown); Domain Joined
    has no contributing provider, so it must show as a fail — a posture view that
    only listed passes would hide the gap the user is asking about."""
    aid = str(uuid4())
    out = _detail(aid)
    assert "Endpoint Security" in out
    assert "CrowdStrike Falcon" in out          # contributing provider badge
    assert "Device Management" in out
    assert "Microsoft Intune" in out
    assert "Domain Joined" in out               # present as a (failing) check


def test_detail_groups_providers_with_last_sync():
    aid = str(uuid4())
    out = _detail(aid)
    assert "CrowdStrike Falcon" in out
    assert "Microsoft Intune" in out
    assert "2026-04-18" in out                  # CrowdStrike last_sync date
    assert "Cpu Model" in out                   # grouped extended field label


def test_detail_caps_unbounded_ext_attr_values_with_signal():
    """A provider can expose hundreds of custom fields; dumping every VALUE into
    the detail text is the §8-#2 blow-up. The Extended line is capped and the
    omission is announced (with a pointer to field_search), instead of silently
    flooding the context. Counts (general/network) stay full — only the
    open-ended ext-attr bag is bounded."""
    from src.mcp.tools.assets import _MAX_PROVIDER_EXT_FIELDS
    aid = str(uuid4())
    big = {f"attr_{i:03d}": f"v{i}" for i in range(_MAX_PROVIDER_EXT_FIELDS + 12)}
    providers = [
        {"data_source": "Custom", "source_name": "Custom Source",
         "source_type": "custom", "source_category": "Other",
         "last_sync_at": datetime(2026, 4, 18, 8, 0, tzinfo=_UTC),
         "hostname": "host01", "data_source_fields": big},
    ]
    out = format_asset_detail(_detail_asset(aid), asset_id=aid, tags=[],
                              providers=providers)
    assert "Attr 000" in out                                   # first values shown
    assert "Attr 051" not in out                               # tail capped
    assert "12 more field(s) not shown" in out                 # overflow announced
    assert "field_search" in out                               # how to widen


def test_detail_source_filter_bounds_ext_attr_output():
    """source= bounds the per-source ext-attr output to that provider only."""
    aid = str(uuid4())
    out = _detail(aid, source="CrowdStrike")
    assert "Xeon Gold" in out          # CrowdStrike's ext-attr shown
    assert "compliant" not in out      # Intune's ext-attr bounded out


def test_detail_field_search_bounds_ext_attr_output():
    """field_search= bounds ext-attrs to keys/values matching the term."""
    aid = str(uuid4())
    out = _detail(aid, field_search="cpu")
    assert "Xeon Gold" in out          # cpu_model matches
    assert "Ram Gb" not in out         # ram_gb does not
    assert "compliant" not in out      # nor does Intune's compliance_state


def test_detail_flags_cross_source_conflict():
    """Two sources disagree on hostname — the folded breakdown must flag it
    (this is the get_asset_source_breakdown capability, now in get_asset)."""
    aid = str(uuid4())
    out = _detail(aid)
    lowered = out.lower()
    assert "conflict" in lowered
    assert "host01-alt" in lowered      # both disagreeing values surfaced


def test_compute_sections_handles_missing_extended_attributes():
    """A bare asset (no extended_attributes, no providers) must not crash and
    must still classify posture by category."""
    aid = str(uuid4())
    sections = compute_asset_sections(
        {"id": aid, "hostname": ["h"], "category": "server", "ip": ["1.2.3.4"]},
        [],
    )
    assert sections["ip_private"] == ["1.2.3.4"]   # falls back to asset.ip
    assert sections["ip_public"] == []
    assert sections["posture"]["category"] == "server"
    # server has posture checks defined; with no providers all fail
    assert sections["posture"]["checks"]
    assert all(not c["passed"] for c in sections["posture"]["checks"])


def test_compute_sections_empty_private_ip_does_not_fall_back_to_merged_ip():
    """An explicit AssetAtlas.ip_private == [] means 'no private IPs' (all
    public). It must NOT fall back to the merged asset.ip and relabel public IPs
    as private — Python's empty list is falsy (unlike JS), which a plain `or`
    would mishandle, diverging from the UI (`aa?.ip_private || asset.ip`)."""
    aid = str(uuid4())
    sections = compute_asset_sections(
        {"id": aid, "category": "server", "ip": ["8.8.8.8"],
         "extended_attributes": {"AssetAtlas": {
             "ip_private": [], "ip_public": ["8.8.8.8"]}}},
        [],
    )
    assert sections["ip_private"] == []          # NOT ["8.8.8.8"]
    assert sections["ip_public"] == ["8.8.8.8"]
