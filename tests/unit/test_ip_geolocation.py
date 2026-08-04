# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for IpGeolocationStage (Stage 14c) and lookup_ip_geo().

Covers: known/unknown/invalid IPs, missing fields, NaN cleanup,
stage behaviour with reader=None, deduplication, multi-row DataFrames,
and preservation of Stage 14b (ip_public/ip_private/ip_type) fields.
"""

import pandas as pd
import pytest

from src.modules.asset_generation.pipeline.stages.s14c_ip_geolocation import (
    IpGeolocationStage,
    lookup_ip_geo,
)
from src.modules.asset_generation.pipeline.context import PipelineContext


# ---------------------------------------------------------------------------
# Mock MMDB reader
# ---------------------------------------------------------------------------

class MockGeoReader:
    """Simulates maxminddb.Reader.get() with a pre-populated lookup table."""

    _DATA = {
        "8.8.8.8": {
            "country": {"names": {"en": "United States"}, "iso_code": "US"},
            "subdivisions": [{"names": {"en": "California"}}],
            "city": {"names": {"en": "Mountain View"}},
            "location": {
                "latitude": 37.386,
                "longitude": -122.0838,
                "time_zone": "America/Los_Angeles",
            },
        },
        "1.1.1.1": {
            "country": {"names": {"en": "Australia"}, "iso_code": "AU"},
            "subdivisions": [{"names": {"en": "New South Wales"}}],
            "city": {"names": {"en": "Sydney"}},
            "location": {
                "latitude": -33.8688,
                "longitude": 151.2093,
                "time_zone": "Australia/Sydney",
            },
        },
        "2.2.2.2": {
            # country only, no city, no subdivisions
            "country": {"names": {"en": "France"}, "iso_code": "FR"},
        },
        "3.3.3.3": {
            # has country + city but no location block
            "country": {"names": {"en": "Germany"}, "iso_code": "DE"},
            "city": {"names": {"en": "Berlin"}},
        },
        "4.4.4.4": {
            # NaN latitude
            "country": {"names": {"en": "Japan"}, "iso_code": "JP"},
            "city": {"names": {"en": "Tokyo"}},
            "location": {
                "latitude": float("nan"),
                "longitude": float("nan"),
                "time_zone": "Asia/Tokyo",
            },
        },
    }

    def get(self, ip):
        return self._DATA.get(ip)


# ---------------------------------------------------------------------------
# Tests for lookup_ip_geo()
# ---------------------------------------------------------------------------

class TestLookupIpGeo:

    def test_known_ip(self):
        result = lookup_ip_geo("8.8.8.8", MockGeoReader())
        assert result is not None
        assert result["country"] == "United States"
        assert result["country_code"] == "US"
        assert result["region"] == "California"
        assert result["city"] == "Mountain View"
        assert result["latitude"] == pytest.approx(37.386)
        assert result["longitude"] == pytest.approx(-122.0838)
        assert result["timezone"] == "America/Los_Angeles"

    def test_unknown_ip(self):
        result = lookup_ip_geo("9.9.9.9", MockGeoReader())
        assert result is None

    def test_none_reader(self):
        result = lookup_ip_geo("8.8.8.8", None)
        assert result is None

    def test_invalid_ip(self):
        result = lookup_ip_geo("not-an-ip", MockGeoReader())
        assert result is None

    def test_missing_city(self):
        """IP with no city or subdivisions still returns country info."""
        result = lookup_ip_geo("2.2.2.2", MockGeoReader())
        assert result is not None
        assert result["country"] == "France"
        assert result["country_code"] == "FR"
        assert result["city"] is None
        assert result["region"] is None

    def test_missing_location(self):
        """IP with no location block returns None for lat/lon/timezone."""
        result = lookup_ip_geo("3.3.3.3", MockGeoReader())
        assert result is not None
        assert result["country"] == "Germany"
        assert result["latitude"] is None
        assert result["longitude"] is None
        assert result["timezone"] is None

    def test_nan_latitude_cleaned(self):
        """NaN floats from MMDB must be converted to None (JSONB safety)."""
        result = lookup_ip_geo("4.4.4.4", MockGeoReader())
        assert result is not None
        assert result["latitude"] is None
        assert result["longitude"] is None


# ---------------------------------------------------------------------------
# Helper: build a minimal PipelineContext for stage tests
# ---------------------------------------------------------------------------

def _make_ctx(ip_data, reader):
    """
    Build a PipelineContext with aggregated_df containing extended_fields
    pre-populated with Stage 14b output (ip_public, ip_private, ip_type).

    ip_data: list of dicts with keys ip_public, ip_private, ip_type
    """
    rows = []
    for d in ip_data:
        rows.append({
            "extended_fields": {
                "AssetAtlas": {
                    "ip_public": d.get("ip_public", []),
                    "ip_private": d.get("ip_private", []),
                    "ip_type": d.get("ip_type", "none"),
                }
            }
        })
    df = pd.DataFrame(rows)
    ctx = PipelineContext(raw_data_df=pd.DataFrame(), aggregated_df=df, geo_reader=reader)
    return ctx


# ---------------------------------------------------------------------------
# Tests for IpGeolocationStage._execute()
# ---------------------------------------------------------------------------

class TestIpGeolocationStage:

    def _stage(self):
        return IpGeolocationStage(service=None)

    def test_single_public_ip(self):
        ctx = _make_ctx(
            [{"ip_public": ["8.8.8.8"], "ip_private": [], "ip_type": "public_only"}],
            MockGeoReader(),
        )
        self._stage()._execute(ctx)
        aa = ctx.aggregated_df.iloc[0]["extended_fields"]["AssetAtlas"]
        assert aa["geo_countries"] == ["United States"]
        assert aa["geo_cities"] == ["Mountain View"]
        assert "8.8.8.8" in aa["ip_public_geo"]
        assert aa["ip_public_geo"]["8.8.8.8"]["country_code"] == "US"

    def test_multiple_public_ips(self):
        ctx = _make_ctx(
            [{"ip_public": ["8.8.8.8", "1.1.1.1"], "ip_private": [], "ip_type": "public_only"}],
            MockGeoReader(),
        )
        self._stage()._execute(ctx)
        aa = ctx.aggregated_df.iloc[0]["extended_fields"]["AssetAtlas"]
        assert set(aa["geo_countries"]) == {"United States", "Australia"}
        assert set(aa["geo_cities"]) == {"Mountain View", "Sydney"}
        assert "8.8.8.8" in aa["ip_public_geo"]
        assert "1.1.1.1" in aa["ip_public_geo"]

    def test_no_public_ips(self):
        ctx = _make_ctx(
            [{"ip_public": [], "ip_private": ["10.0.0.1"], "ip_type": "private_only"}],
            MockGeoReader(),
        )
        self._stage()._execute(ctx)
        aa = ctx.aggregated_df.iloc[0]["extended_fields"]["AssetAtlas"]
        assert "geo_countries" not in aa
        assert "ip_public_geo" not in aa

    def test_unknown_ip_skipped(self):
        ctx = _make_ctx(
            [{"ip_public": ["9.9.9.9"], "ip_private": [], "ip_type": "public_only"}],
            MockGeoReader(),
        )
        self._stage()._execute(ctx)
        aa = ctx.aggregated_df.iloc[0]["extended_fields"]["AssetAtlas"]
        # Unknown IP produces no geo data
        assert aa.get("ip_public_geo", {}) == {}
        assert aa.get("geo_countries", []) == []

    def test_preserves_existing_assetatlas_fields(self):
        """Stage 14b fields (ip_public, ip_private, ip_type) must not be clobbered."""
        ctx = _make_ctx(
            [{"ip_public": ["8.8.8.8"], "ip_private": ["10.0.0.1"], "ip_type": "mixed"}],
            MockGeoReader(),
        )
        self._stage()._execute(ctx)
        aa = ctx.aggregated_df.iloc[0]["extended_fields"]["AssetAtlas"]
        assert aa["ip_public"] == ["8.8.8.8"]
        assert aa["ip_private"] == ["10.0.0.1"]
        assert aa["ip_type"] == "mixed"

    def test_no_reader_skips(self):
        ctx = _make_ctx(
            [{"ip_public": ["8.8.8.8"], "ip_private": [], "ip_type": "public_only"}],
            reader=None,
        )
        self._stage()._execute(ctx)
        aa = ctx.aggregated_df.iloc[0]["extended_fields"]["AssetAtlas"]
        assert "geo_countries" not in aa
        assert "ip_public_geo" not in aa

    def test_deduplicates_countries(self):
        """Two IPs from the same country → one entry in geo_countries."""
        ctx = _make_ctx(
            [{"ip_public": ["8.8.8.8", "8.8.4.4"], "ip_private": [], "ip_type": "public_only"}],
            MockGeoReader(),
        )
        # 8.8.4.4 is not in MockGeoReader so only 8.8.8.8 resolves → still deduplicated
        # Add a second known US IP by patching the reader
        class ExtendedReader(MockGeoReader):
            _DATA = {
                **MockGeoReader._DATA,
                "8.8.4.4": {
                    "country": {"names": {"en": "United States"}, "iso_code": "US"},
                    "city": {"names": {"en": "Mountain View"}},
                    "location": {"latitude": 37.386, "longitude": -122.0838, "time_zone": "America/Los_Angeles"},
                },
            }

        ctx = _make_ctx(
            [{"ip_public": ["8.8.8.8", "8.8.4.4"], "ip_private": [], "ip_type": "public_only"}],
            ExtendedReader(),
        )
        self._stage()._execute(ctx)
        aa = ctx.aggregated_df.iloc[0]["extended_fields"]["AssetAtlas"]
        assert aa["geo_countries"].count("United States") == 1

    def test_multiple_rows(self):
        """Three rows: US public, AU public, no public IPs."""
        ctx = _make_ctx(
            [
                {"ip_public": ["8.8.8.8"], "ip_private": [], "ip_type": "public_only"},
                {"ip_public": ["1.1.1.1"], "ip_private": [], "ip_type": "public_only"},
                {"ip_public": [], "ip_private": ["10.0.0.1"], "ip_type": "private_only"},
            ],
            MockGeoReader(),
        )
        self._stage()._execute(ctx)

        aa0 = ctx.aggregated_df.iloc[0]["extended_fields"]["AssetAtlas"]
        aa1 = ctx.aggregated_df.iloc[1]["extended_fields"]["AssetAtlas"]
        aa2 = ctx.aggregated_df.iloc[2]["extended_fields"]["AssetAtlas"]

        assert aa0["geo_countries"] == ["United States"]
        assert aa1["geo_countries"] == ["Australia"]
        assert "geo_countries" not in aa2
