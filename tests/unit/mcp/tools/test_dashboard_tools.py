# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the sectioned dashboard chat tools (plan §2.C).

``get_dashboard`` / ``get_dashboard_trend`` / ``get_relationship_graph`` are thin
wrappers over ``DashboardService`` — the SAME service the homepage UI calls — so
the numbers a chat user sees must match the UI exactly. These tests therefore
mock ``DashboardService`` with known dicts and assert the wrapper surfaces those
numbers verbatim (never re-derives them), builds a VALID artifact envelope
(donut/stacked_bar/table only — unknown types are dropped by the registry), and
emits FAITHFUL drill-downs (a count it can't substantiate is forbidden, §3.6).

WHY each behavior matters is encoded per test: a section that quietly drops or
re-computes a number breaks UI-parity (the whole point); a drill-down that
re-runs to a DIFFERENT population than the bucket it came from is a lie; an
unknown trend metric silently returning empty hides the error from the model
(§4 loud-error-over-silent).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.mcp.tools.dashboard_tools import (
    get_dashboard_impl,
    get_dashboard_trend_impl,
    get_relationship_graph_impl,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _summary():
    return {
        "totalAssets": 1234,
        "changeToday": {"added": 12, "removed": 0},
        "dataQuality": {
            "rating": "Good",
            "confidenceDistribution": {"high": 40.0, "medium": 35.0, "low": 25.0},
        },
        "visibility": {"level": "Developing", "score": 52.3},
        "completeness": 78.5,
        "lastUpdated": "2026-06-20T00:00:00",
        "categoryBreakdown": [
            {"category": "Server", "count": 500},
            {"category": "Workstation", "count": 734},
        ],
    }


def _inventory():
    return {
        "totalAssets": 1234,
        "categoryGauges": [
            {"category": "Server", "count": 500, "percentage": 40.5,
             "avgConfidence": 82.0, "confidenceLevel": "high",
             "highCount": 400, "mediumCount": 70, "lowCount": 30},
            {"category": "Network", "count": 200, "percentage": 16.2,
             "avgConfidence": 41.0, "confidenceLevel": "low",
             "highCount": 20, "mediumCount": 60, "lowCount": 120},
            {"category": "Workstation", "count": 534, "percentage": 43.3,
             "avgConfidence": 65.0, "confidenceLevel": "medium",
             "highCount": 200, "mediumCount": 250, "lowCount": 84},
        ],
    }


def _svc(**overrides):
    svc = MagicMock()
    svc.get_summary.return_value = _summary()
    svc.get_inventory_summary.return_value = _inventory()
    svc.get_posture_data.return_value = {
        "posture": {
            "server": {"secured": 300, "monitored": 150, "discovered": 50},
            "workstation": {"secured": 200, "monitored": 200, "discovered": 134},
        },
        "changes": {
            "secured": {"delta": 25, "period": "week"},
            "monitored": {"delta": -5, "period": "week"},
            "discovered": {"delta": None, "period": "week"},
        },
    }
    svc.get_confidence_breakdown.return_value = {
        "breakdown": {
            "server": {"confirmed": 400, "under_covered": 70, "shadows": 30},
            "network": {"confirmed": 20, "under_covered": 60, "shadows": 120},
        }
    }
    svc.get_data_sources.return_value = {
        "count": 2,
        "sources": [
            {"id": "i1", "name": "Jamf", "assetCount": 800,
             "status": "healthy", "lastSyncDisplay": "2 hours ago",
             "confidence": 87,
             "confidenceDistribution": {"high": 600, "medium": 150, "low": 50}},
            {"id": "i2", "name": "vCenter", "assetCount": 434,
             "status": "warning", "lastSyncDisplay": "30 hours ago",
             "confidence": 55,
             "confidenceDistribution": {"high": 100, "medium": 200, "low": 134}},
        ],
    }
    svc.get_reliability.return_value = {
        "mostReliableSource": {"name": "Jamf", "score": 0.87},
        "leastReliableSource": {"name": "Legacy CMDB", "score": 0.42},
        "mostReliableCategory": {"name": "Workstation", "score": 92.3},
        "leastReliableCategory": {"name": "Network", "score": 41.0},
    }
    for k, v in overrides.items():
        getattr(svc, k).return_value = v
    return svc


def _patch_list_query(total=1000, covered=900):
    """Patch the canonical count path used by data_quality field coverage.

    Returns (rows, total). The data_quality coverage computes total once (no
    filter) and a non-empty count per field (notempty filter); we make the
    no-filter call return ``total`` and any filtered call return ``covered``.
    """
    def _fake(db, resolver, *, filters=None, **kwargs):
        has_field_filter = bool(filters) and any(
            (f.get("op") in ("notempty", "empty")) for f in filters
        )
        return ([], covered if has_field_filter else total)
    return patch("src.mcp.tools.dashboard_tools.list_assets_query", side_effect=_fake)


# ===========================================================================
# section=summary
# ===========================================================================

class TestSummary:
    def test_surfaces_service_numbers_verbatim(self):
        """Summary must echo DashboardService.get_summary() numbers, not
        re-derive them — that is the entire UI-parity contract."""
        text, art = get_dashboard_impl(_svc(), None, None, section="summary")
        assert "1,234" in text          # totalAssets
        assert "Good" in text           # dataQuality.rating
        assert "Developing" in text     # visibility.level
        assert "52.3" in text           # visibility.score
        assert "78.5" in text           # completeness
        assert art is not None
        assert art["type"] == "table"
        assert art["meta"]["section"] == "summary"

    def test_default_section_is_summary(self):
        text, _ = get_dashboard_impl(_svc(), None, None)
        assert "1,234" in text


# ===========================================================================
# section=posture_tiers
# ===========================================================================

class TestPostureTiers:
    def test_renders_tiers_and_wow_deltas(self):
        text, art = get_dashboard_impl(_svc(), None, None, section="posture_tiers")
        # tier totals from the mock: secured 500, monitored 350, discovered 184
        assert "secured" in text.lower()
        assert "500" in text
        # week-over-week deltas surfaced (+25 secured, -5 monitored)
        assert "+25" in text or "25" in text
        assert art is not None
        assert art["type"] == "stacked_bar"

    def test_null_delta_renders_as_no_baseline_not_zero(self):
        """A null WoW delta means 'no 7-day-old snapshot yet' — reporting it as
        0 would falsely claim 'no change'."""
        text, _ = get_dashboard_impl(_svc(), None, None, section="posture_tiers")
        assert "+0" not in text  # discovered delta is None, must not show as +0


# ===========================================================================
# section=confidence_breakdown
# ===========================================================================

class TestConfidenceBreakdown:
    def test_renders_bands_and_builds_stacked_bar(self):
        text, art = get_dashboard_impl(
            _svc(), None, None, section="confidence_breakdown",
        )
        assert "confirmed" in text.lower()
        assert "400" in text            # server confirmed
        assert art["type"] == "stacked_bar"

    def test_drilldown_maps_band_to_confidence_level(self):
        """Drill-down on a confidence band must re-run to the SAME population
        the band counted: confirmed↔confidence_level high, under_covered↔medium,
        shadows↔low. A wrong mapping returns a different set than charted."""
        _, art = get_dashboard_impl(
            _svc(), None, None, section="confidence_breakdown", drilldown=True,
        )
        server = next(s for s in art["series"] if s["x_label"] == "server")
        confirmed = next(s for s in server["stacks"] if s["label"] == "confirmed")
        dd = confirmed["drilldown"]
        fields = {f["field"]: f["value"] for f in dd["filters"]}
        assert fields["Category"] == "server"
        assert fields["confidence_level"] == "high"

    def test_no_drilldown_by_default(self):
        _, art = get_dashboard_impl(
            _svc(), None, None, section="confidence_breakdown",
        )
        server = next(s for s in art["series"] if s["x_label"] == "server")
        assert all("drilldown" not in stack for stack in server["stacks"])


# ===========================================================================
# section=data_source_health
# ===========================================================================

class TestDataSourceHealth:
    def test_renders_per_source_health_table(self):
        text, art = get_dashboard_impl(
            _svc(), None, None, section="data_source_health",
        )
        assert "Jamf" in text and "vCenter" in text
        assert "800" in text            # Jamf asset count
        assert art["type"] == "table"
        assert len(art["series"]) == 2

    def test_view_individual_vs_grouped_passed_through(self):
        svc = _svc()
        get_dashboard_impl(svc, None, None,
                           section="data_source_health", view="grouped")
        svc.get_data_sources.assert_called_with(view="grouped")

    def test_drilldown_filters_on_data_source(self):
        _, art = get_dashboard_impl(
            _svc(), None, None, section="data_source_health", drilldown=True,
        )
        row = art["series"][0]
        dd = row["drilldown"]
        f = dd["filters"][0]
        assert f["field"] == "Data Source"
        assert f["value"] == "Jamf"

    def test_many_sources_cap_text_but_keep_full_artifact(self):
        """A deployment with many integrations must not list every source in the
        text (§8-#2): the text is capped and the omission announced, while the
        artifact keeps every source so the user still sees them all in the table."""
        from src.mcp.tools.dashboard_tools import _RANKING_CAP
        svc = _svc()
        n = _RANKING_CAP + 4
        svc.get_data_sources.return_value = {
            "count": n,
            "sources": [
                {"id": f"i{i}", "name": f"src-{i:02d}", "assetCount": 10,
                 "status": "healthy", "lastSyncDisplay": "1h ago",
                 "confidence": 50}
                for i in range(n)
            ],
        }
        text, art = get_dashboard_impl(
            svc, None, None, section="data_source_health",
        )
        assert "src-00" in text                          # first rows render
        assert f"src-{_RANKING_CAP:02d}" not in text     # tail capped in text
        assert "4 more sources not shown" in text        # overflow announced
        assert len(art["series"]) == n                   # artifact stays complete


# ===========================================================================
# section=reliability
# ===========================================================================

class TestReliability:
    def test_default_shows_extremes_with_source_scaled_to_pct(self):
        """Source scores are raw 0..1 → shown as %; category scores already %
        — mismatching the scale would misreport reliability."""
        text, art = get_dashboard_impl(_svc(), None, None, section="reliability")
        assert "Jamf" in text and "87.0%" in text     # 0.87 → 87.0%
        assert "Workstation" in text and "92.3%" in text
        assert art is not None

    def test_full_ranks_all_sources_and_categories(self):
        """full=True drops the most/least-only view for the COMPLETE ranking
        (the plan's removed [:10] cap), with sample sizes."""
        text, _ = get_dashboard_impl(
            _svc(), None, None, section="reliability", full=True,
        )
        # all sources present (Jamf 87%, vCenter 55%), all categories present
        assert "Jamf" in text and "vCenter" in text
        assert "Server" in text and "Network" in text and "Workstation" in text


# ===========================================================================
# section=data_quality
# ===========================================================================

class TestDataQuality:
    def test_ranks_categories_lowest_confidence_first(self):
        """Data-quality triage shows the worst categories first; reordering by
        size (or not sorting) would bury the low-confidence ones the user is
        hunting for."""
        with _patch_list_query():
            text, _ = get_dashboard_impl(
                _svc(), MagicMock(), MagicMock(), section="data_quality",
            )
        # Network (41%) must appear before Server (82%) in the ranking text
        assert text.index("Network") < text.index("Server")

    def test_field_coverage_uses_canonical_count(self):
        """Coverage% = non-empty / total via the SAME query engine Explorer
        uses (notempty operator), so it matches a user's own filter — not a
        bespoke SQL re-implementation."""
        with _patch_list_query(total=1000, covered=900):
            text, art = get_dashboard_impl(
                _svc(), MagicMock(), MagicMock(),
                section="data_quality", fields=["Hostname"],
            )
        assert "90.0%" in text          # 900/1000
        assert art["type"] == "table"

    def test_field_coverage_drilldown_targets_missing_assets(self):
        """Drill-down on a coverage row should surface the assets MISSING the
        field (op=empty) — that's the actionable population."""
        with _patch_list_query():
            _, art = get_dashboard_impl(
                _svc(), MagicMock(), MagicMock(), section="data_quality",
                fields=["Hostname"], drilldown=True,
            )
        dd = art["series"][0]["drilldown"]
        assert dd["filters"][0]["op"] == "empty"

    def test_unknown_field_is_loud_not_silent(self):
        """An unresolvable field must raise loudly (→ string error), never be
        dropped — a dropped field would silently mis-report coverage (§4)."""
        from src.mcp.tools.assets import FieldResolutionError

        def _raise(*a, **k):
            raise FieldResolutionError("Unknown filter field 'Nope'.")
        with patch("src.mcp.tools.dashboard_tools.list_assets_query",
                   side_effect=_raise):
            text, art = get_dashboard_impl(
                _svc(), MagicMock(), MagicMock(),
                section="data_quality", fields=["Nope"],
            )
        assert art is None
        assert "Nope" in text


# ===========================================================================
# Cross-cutting
# ===========================================================================

class TestSectionDispatch:
    def test_unknown_section_is_loud_error(self):
        text, art = get_dashboard_impl(_svc(), None, None, section="bogus")
        assert art is None
        assert "bogus" in text

    def test_none_service_returns_clean_unavailable_not_crash(self):
        """When the dashboard service isn't wired (misconfig/test), the tool
        must degrade to a clean message, never crash or leak internals."""
        text, art = get_dashboard_impl(None, None, None, section="summary")
        assert art is None
        assert "unavailable" in text.lower() or "not available" in text.lower()


# ===========================================================================
# get_dashboard_trend
# ===========================================================================

def _trend_svc(**overrides):
    svc = MagicMock()
    svc.get_posture_trend.return_value = {
        "days": 3,
        "series": {"secured": [10, 20, 30],
                   "monitored": [5, 6, 7],
                   "discovered": [1, 2, 3]},
    }
    svc.get_coverage_trend.return_value = {
        "metric": "srv_edr", "range": "3d", "granularity": "daily",
        "data": [{"label": "Jun 18", "added": 80, "removed": 20},
                 {"label": "Jun 19", "added": 85, "removed": 15},
                 {"label": "Jun 20", "added": 90, "removed": 10}],
    }
    svc.get_asset_timeline.return_value = {
        "range": "3d", "granularity": "daily",
        "data": [{"label": "Jun 18", "added": 3, "removed": 1},
                 {"label": "Jun 19", "added": 4, "removed": 0},
                 {"label": "Jun 20", "added": 2, "removed": 2}],
    }
    for k, v in overrides.items():
        getattr(svc, k).return_value = v
    return svc


class TestTrend:
    def test_posture_kind_surfaces_tier_series(self):
        text, art = get_dashboard_trend_impl(_trend_svc(), kind="posture", days=3)
        assert "secured" in text.lower()
        assert "30" in text                       # last secured value
        assert art["type"] == "table"
        assert art["meta"]["kind"] == "posture"

    def test_coverage_kind_passes_metric_and_days(self):
        svc = _trend_svc()
        get_dashboard_trend_impl(svc, kind="coverage", metric="ws_edr", days=14)
        svc.get_coverage_trend.assert_called_with(metric="ws_edr", days=14)

    def test_unknown_coverage_metric_is_loud_not_empty(self):
        """An unknown metric must be a loud error listing valid metrics — the
        service would otherwise return a silent empty series (§4)."""
        text, art = get_dashboard_trend_impl(
            _trend_svc(), kind="coverage", metric="bogus",
        )
        assert art is None
        assert "bogus" in text
        assert "srv_edr" in text                  # valid options listed

    def test_asset_kind_surfaces_added_removed(self):
        text, art = get_dashboard_trend_impl(_trend_svc(), kind="asset", days=3)
        assert "added" in text.lower()
        assert art["meta"]["kind"] == "asset"

    def test_timeline_is_alias_of_asset(self):
        svc = _trend_svc()
        get_dashboard_trend_impl(svc, kind="timeline", days=3)
        svc.get_asset_timeline.assert_called_once()

    def test_days_clamped_to_service_range(self):
        """The UI clamps days to 1..365; the chat tool must apply the SAME clamp
        so chat can't request a window the UI can't."""
        svc = _trend_svc()
        get_dashboard_trend_impl(svc, kind="posture", days=9999)
        assert svc.get_posture_trend.call_args.kwargs["days"] == 365
        svc2 = _trend_svc()
        get_dashboard_trend_impl(svc2, kind="posture", days=0)
        assert svc2.get_posture_trend.call_args.kwargs["days"] == 1

    def test_unknown_kind_is_loud(self):
        text, art = get_dashboard_trend_impl(_trend_svc(), kind="bogus")
        assert art is None
        assert "bogus" in text

    def test_none_service_is_clean_unavailable(self):
        text, art = get_dashboard_trend_impl(None, kind="posture")
        assert art is None
        assert "unavailable" in text.lower() or "not available" in text.lower()

    def test_bucket_week_sums_flows(self):
        """asset added/removed are per-day EVENT FLOWS — rolling 14 days into
        weeks must SUM them (total preserved), not sample."""
        data = [{"label": f"d{i}", "added": 1, "removed": 0} for i in range(14)]
        svc = _trend_svc(get_asset_timeline={
            "range": "14d", "granularity": "daily", "data": data})
        _, art = get_dashboard_trend_impl(
            svc, kind="asset", days=14, bucket="week",
        )
        rows = art["series"]
        assert len(rows) < 14                          # rolled up
        assert sum(r["added"] for r in rows) == 14     # sum preserved

    def test_bucket_week_lasts_levels(self):
        """posture tier counts are point-in-time LEVELS — rolling into weeks must
        take each week's last value, not sum daily snapshots."""
        secured = list(range(1, 15))   # 1..14, increasing
        svc = _trend_svc(get_posture_trend={
            "days": 14,
            "series": {"secured": secured,
                       "monitored": [0] * 14, "discovered": [0] * 14}})
        _, art = get_dashboard_trend_impl(
            svc, kind="posture", days=14, bucket="week",
        )
        rows = art["series"]
        assert len(rows) < 14
        # most recent bucket carries the latest level (14), never a sum
        assert rows[-1]["secured"] == 14
        assert sum(r["secured"] for r in rows) < sum(secured)

    def test_bad_custom_date_is_loud(self):
        text, art = get_dashboard_trend_impl(
            _trend_svc(), kind="posture", start="not-a-date", end="2026-06-20",
        )
        assert art is None
        assert "date" in text.lower()


# ===========================================================================
# get_relationship_graph
# ===========================================================================

def _graph_svc():
    svc = MagicMock()
    svc.get_flower_data.return_value = {
        "totalAssets": 100,
        "categories": [{"id": 1234, "name": "Server", "count": 60,
                        "color": "#ffffff"}],
        "operatingSystems": [{"id": 5678, "name": "Linux", "count": 50,
                              "color": "#00ff00"}],
        "subcategories": [{"id": 11, "name": "Databases", "category": "Server",
                           "categoryId": 1234, "count": 20, "os": [5678],
                           "sources": ["u1"], "color": "#000000"}],
        "dataSources": [{"id": "u1", "name": "vCenter", "count": 80,
                         "color": "#0000ff", "icon": "icons/vcenter.png"}],
    }
    svc.get_flower_data_v2.return_value = {
        "totalAssets": 100,
        "categories": [{"id": 1, "name": "Server", "count": 60,
                        "subcategories": [1], "sources": ["u1"], "os": [1],
                        "color": "#fff"}],
        "subcategories": [{"id": 1, "name": "Databases", "category": "Server",
                           "count": 20, "categories": [1], "sources": ["u1"],
                           "os": [1], "color": "#000"}],
        "operatingSystems": [{"id": 1, "name": "Linux", "count": 50,
                              "categories": [1], "subcategories": [1],
                              "sources": ["u1"], "color": "#0f0"}],
        "dataSources": [{"id": "u1", "name": "vCenter", "count": 80,
                         "categories": [1], "subcategories": [1], "os": [1],
                         "color": "#00f"}],
    }
    return svc


class TestRelationshipGraph:
    def test_v1_resolves_subcategory_links_to_names_not_ids(self):
        """The graph's value is named cross-links; leaving raw hash ids in would
        be opaque to the model. v1 subcategory→os/source must be NAMES."""
        _, art = get_relationship_graph_impl(_graph_svc(), version="v1")
        sub = art["meta"]["graph"]["subcategories"][0]
        assert sub["links"]["operatingSystems"] == ["Linux"]
        assert sub["links"]["dataSources"] == ["vCenter"]
        # the presentation hash id must not survive as a link
        assert 5678 not in sub["links"]["operatingSystems"]

    def test_v1_passes_only_set_filters(self):
        svc = _graph_svc()
        get_relationship_graph_impl(svc, version="v1", category="Server")
        svc.get_flower_data.assert_called_once_with({"category": "Server"})

    def test_v2_resolves_any_to_any_links_to_names(self):
        _, art = get_relationship_graph_impl(_graph_svc(), version="v2")
        cat = next(c for c in art["meta"]["graph"]["categories"]
                   if c["name"] == "Server")
        assert cat["links"]["subcategories"] == ["Databases"]
        assert cat["links"]["operatingSystems"] == ["Linux"]
        assert cat["links"]["dataSources"] == ["vCenter"]

    def test_v2_uses_v2_method(self):
        svc = _graph_svc()
        get_relationship_graph_impl(svc, version="v2")
        svc.get_flower_data_v2.assert_called_once()
        svc.get_flower_data.assert_not_called()

    def test_strips_presentation_color_and_icon(self):
        """color/icon are pure presentation — they must not leak into the chat
        artifact or the structured graph meta."""
        import json
        text, art = get_relationship_graph_impl(_graph_svc(), version="v1")
        blob = json.dumps(art) + text
        assert "color" not in blob
        assert "icon" not in blob

    def test_artifact_is_table_of_nodes(self):
        _, art = get_relationship_graph_impl(_graph_svc(), version="v2")
        assert art["type"] == "table"
        names = {row["entity"] for row in art["series"]}
        assert {"Server", "Databases", "Linux", "vCenter"} <= names

    def test_unknown_version_is_loud(self):
        text, art = get_relationship_graph_impl(_graph_svc(), version="v3")
        assert art is None
        assert "v3" in text or "version" in text.lower()

    def test_none_service_is_clean_unavailable(self):
        text, art = get_relationship_graph_impl(None, version="v1")
        assert art is None
        assert "unavailable" in text.lower() or "not available" in text.lower()


# ===========================================================================
# Artifact validity — the registry DROPS artifacts that fail validate_envelope,
# so an invalid shape silently loses the chart. Lock every emitted artifact to a
# type/shape the chat registry actually persists (donut/stacked_bar/table only).
# ===========================================================================

from src.api.blueprints.chat.artifacts import validate_envelope


class TestArtifactsAreRegistryValid:
    @pytest.mark.parametrize("section", [
        "summary", "posture_tiers", "confidence_breakdown",
        "data_source_health", "reliability",
    ])
    def test_section_artifact_survives_validation(self, section):
        _, art = get_dashboard_impl(_svc(), None, None, section=section)
        assert validate_envelope(art, tool_name="get_dashboard") is not None

    def test_data_quality_artifact_survives_validation(self):
        with _patch_list_query():
            _, art = get_dashboard_impl(
                _svc(), MagicMock(), MagicMock(), section="data_quality",
            )
        assert validate_envelope(art, tool_name="get_dashboard") is not None

    @pytest.mark.parametrize("kind", ["posture", "coverage", "asset"])
    def test_trend_artifact_survives_validation(self, kind):
        _, art = get_dashboard_trend_impl(_trend_svc(), kind=kind, days=3)
        assert validate_envelope(art, tool_name="get_dashboard_trend") is not None

    @pytest.mark.parametrize("version", ["v1", "v2"])
    def test_graph_artifact_survives_validation(self, version):
        _, art = get_relationship_graph_impl(_graph_svc(), version=version)
        assert validate_envelope(art, tool_name="get_relationship_graph") is not None


# ===========================================================================
# Registry integration — prove the full chat path: build_registry registers the
# tools, ToolRegistry.invoke runs them, and the artifact survives the registry's
# own validate_envelope (an invalid one would be silently dropped). Also proves
# the deprecated get_data_quality/get_reliability names shim onto get_dashboard.
# ===========================================================================

import asyncio

from src.api.blueprints.chat.tool_bindings import build_registry


def _full_svc():
    """One dashboard_service mock configured for every section/kind/version."""
    svc = _svc()
    t, g = _trend_svc(), _graph_svc()
    svc.get_posture_trend = t.get_posture_trend
    svc.get_coverage_trend = t.get_coverage_trend
    svc.get_asset_timeline = t.get_asset_timeline
    svc.get_flower_data = g.get_flower_data
    svc.get_flower_data_v2 = g.get_flower_data_v2
    return svc


class TestRegistryIntegration:
    def _registry(self, **kw):
        return build_registry(
            db=MagicMock(), suggestion_service=MagicMock(),
            field_resolver=MagicMock(), settings_manager=None, **kw,
        )

    def test_new_tools_present_when_service_wired(self):
        r = self._registry(dashboard_service=_full_svc())
        names = {t.name for t in r.for_role("admin")}
        assert {"get_dashboard", "get_dashboard_trend",
                "get_relationship_graph"} <= names

    def test_trend_and_graph_skipped_when_service_none(self):
        """Service-dependent tools follow the codebase convention: don't
        advertise when unconfigured (absence is more honest than a stub). But
        get_dashboard stays registered for back-compat."""
        r = self._registry(dashboard_service=None)
        names = {t.name for t in r.for_role("admin")}
        assert "get_dashboard_trend" not in names
        assert "get_relationship_graph" not in names
        assert "get_dashboard" in names

    def test_invoke_get_dashboard_summary(self):
        r = self._registry(dashboard_service=_full_svc())
        inv = asyncio.run(
            r.invoke("get_dashboard", {"section": "summary"}, user_role="admin")
        )
        assert inv.status == "ok"
        assert "1,234" in inv.result
        assert inv.artifact is not None and inv.artifact["type"] == "table"

    def test_invoke_trend_artifact_survives_registry(self):
        r = self._registry(dashboard_service=_full_svc())
        inv = asyncio.run(r.invoke(
            "get_dashboard_trend", {"kind": "posture", "days": 3},
            user_role="admin",
        ))
        assert inv.status == "ok"
        assert inv.artifact is not None     # passed validate_envelope

    def test_invoke_relationship_artifact_survives_registry(self):
        r = self._registry(dashboard_service=_full_svc())
        inv = asyncio.run(r.invoke(
            "get_relationship_graph", {"version": "v2"}, user_role="admin",
        ))
        assert inv.status == "ok"
        assert inv.artifact is not None

    def test_get_data_quality_shims_to_section(self):
        r = self._registry(dashboard_service=_full_svc())
        with _patch_list_query():
            inv = asyncio.run(
                r.invoke("get_data_quality", {}, user_role="admin")
            )
        assert inv.status == "ok"
        assert "completeness" in inv.result.lower()

    def test_get_reliability_shims_to_section(self):
        r = self._registry(dashboard_service=_full_svc())
        inv = asyncio.run(r.invoke("get_reliability", {}, user_role="admin"))
        assert inv.status == "ok"
        assert "reliab" in inv.result.lower()


# ===========================================================================
# Code-review fixes (PR #760): honest signalling of approximations
# ===========================================================================

class TestReviewFixes:
    def test_trend_custom_window_notes_today_anchor(self):
        """A custom start/end can only be a look-back ending today (service is
        anchored to today). The OUTPUT — not just the tool doc — must say so,
        or the model reports a historical range it didn't actually get."""
        text, _ = get_dashboard_trend_impl(
            _trend_svc(), kind="posture", start="2026-01-01", end="2026-01-07",
        )
        assert "anchored to today" in text.lower()

    def test_data_quality_labels_global_completeness_when_scoped(self):
        """With a category scope, completeness/rating are still inventory-wide
        (no category-scoped method exists) — they must be labelled as such, not
        implied to be the category's."""
        with _patch_list_query():
            text, _ = get_dashboard_impl(
                _svc(), MagicMock(), MagicMock(),
                section="data_quality", category="Server",
            )
        assert "inventory-wide" in text.lower()
