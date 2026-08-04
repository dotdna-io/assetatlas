# SPDX-License-Identifier: AGPL-3.0-or-later
"""IP geolocation enrichment — Stage 14c (post IP classification, pre-scoring)."""

import logging

from src.database.postgres_adapter import _clean_nan
from src.modules.asset_generation.pipeline.base import PipelineStage
from src.modules.asset_generation.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


def lookup_ip_geo(ip, reader):
    """
    Look up a single IP address in an MMDB reader.

    Returns a dict with 7 fields (country, country_code, region, city,
    latitude, longitude, timezone) or None if the lookup fails / reader
    is absent / IP is invalid or unknown.
    """
    if reader is None:
        return None
    try:
        record = reader.get(ip)
    except Exception:
        return None

    if record is None:
        return None

    country_block = record.get("country") or {}
    country = (country_block.get("names") or {}).get("en")
    country_code = country_block.get("iso_code")

    subdivisions = record.get("subdivisions") or []
    region = None
    if subdivisions:
        region = (subdivisions[0].get("names") or {}).get("en")

    city_block = record.get("city") or {}
    city = (city_block.get("names") or {}).get("en")

    location = record.get("location") or {}

    return _clean_nan({
        "country": country,
        "country_code": country_code,
        "region": region,
        "city": city,
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "timezone": location.get("time_zone"),
    })


class IpGeolocationStage(PipelineStage):

    def __init__(self, service):
        self.service = service

    @property
    def name(self) -> str:
        return "IP Geolocation Enrichment"

    @property
    def key(self) -> str:
        return "ip_geolocation"

    @property
    def stage_label(self) -> str:
        return "IP Geolocation"

    def _execute(self, ctx: PipelineContext) -> None:
        reader = ctx.geo_reader

        if reader is None:
            logger.info("  No geo_reader — skipping IP geolocation")
            ctx.stats["stages"][self.key] = {"skipped": True, "reason": "no_reader"}
            return

        enriched = 0
        skipped = 0

        def _enrich_row(row):
            nonlocal enriched, skipped

            ef = row.get("extended_fields")
            if not isinstance(ef, dict):
                skipped += 1
                return ef

            aa = ef.get("AssetAtlas")
            if not isinstance(aa, dict) or not aa.get("ip_public"):
                skipped += 1
                return ef

            ef = ef.copy()
            aa = aa.copy()
            ip_public = aa["ip_public"]

            ip_public_geo = {}
            geo_countries = []
            geo_cities = []
            geo_regions = []
            seen_countries = set()
            seen_cities = set()
            seen_regions = set()

            for ip in ip_public:
                geo = lookup_ip_geo(ip, reader)
                if geo is None:
                    continue
                ip_public_geo[ip] = geo

                country = geo.get("country")
                if country and country not in seen_countries:
                    geo_countries.append(country)
                    seen_countries.add(country)

                city = geo.get("city")
                if city and city not in seen_cities:
                    geo_cities.append(city)
                    seen_cities.add(city)

                region = geo.get("region")
                if region and region not in seen_regions:
                    geo_regions.append(region)
                    seen_regions.add(region)

            aa["ip_public_geo"] = ip_public_geo
            aa["geo_countries"] = geo_countries
            aa["geo_cities"] = geo_cities
            aa["geo_regions"] = geo_regions

            ef["AssetAtlas"] = aa
            enriched += 1
            return ef

        ctx.aggregated_df["extended_fields"] = ctx.aggregated_df.apply(_enrich_row, axis=1)

        ctx.stats["stages"][self.key] = {
            "enriched": enriched,
            "skipped": skipped,
        }
