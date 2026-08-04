# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the chat tool registry bindings."""

import asyncio
import threading
from unittest.mock import MagicMock, patch

from src.api.blueprints.chat.tool_bindings import build_registry
from src.modules.fields import FieldCatalog, FieldResolver


def _resolver():
    """A live FieldResolver over an empty-DB catalog (built-in static/virtual/
    core fields only) — enough to exercise describe_catalog / run_chart bindings
    without a database."""
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


def test_build_registry_contains_existing_mcp_tools():
    db = MagicMock()
    svc_stub = MagicMock()
    r = build_registry(db=db, suggestion_service=svc_stub)
    names = {t.name for t in r.for_role("admin")}
    # Canonical tools are advertised to the LLM.
    assert "query_assets" in names
    assert "get_asset" in names
    assert "get_dashboard" in names
    assert "get_alerts" in names
    assert "get_pipeline_status" in names
    # NB: get_integration_status was folded into get_integration, which is now
    # gated on integration_service (see TestIntegrationObservabilityTools).
    assert "list_tag_rules" in names
    assert "get_asset_history" in names
    # Phase 3 tools
    assert "get_alert_detail" in names
    assert "semantic_suggest" in names
    assert "get_asset_categories" in names
    # Deprecated aliases stay INVOCABLE (a model may still call the old name out
    # of habit — erroring would just cost a retry iteration) but are NOT
    # advertised, so their schemas no longer cost input tokens on every turn.
    for alias in ("search_assets", "list_assets", "aggregate_assets",
                  "get_field_values", "list_fields", "get_data_quality"):
        assert alias not in names, f"{alias!r} should be hidden from the LLM"
        assert r.is_registered(alias), f"{alias!r} must remain invocable"


def test_build_registry_has_no_admin_only_tools():
    db = MagicMock()
    r = build_registry(db=db, suggestion_service=MagicMock())
    user_names = {t.name for t in r.for_role("user")}
    # All currently-bound tools are non-admin (admin tools come in Phase 7).
    # Don't pin the exact count — each new tool would otherwise force-edit
    # this test. Presence checks here + the Phase 6 subset test cover intent.
    assert "get_alert_detail" in user_names
    assert "query_assets" in user_names
    assert "get_asset_categories" in user_names


def test_tier1_phase6_tools_present():
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                       dashboard_service=MagicMock())
    user_names = {t.name for t in r.for_role("user")}
    admin_names = {t.name for t in r.for_role("admin")}
    assert {"list_posture_rules",
            "list_reports", "get_report"}.issubset(user_names)
    # get_reliability is a deprecated alias for get_dashboard(section='reliability'):
    # registered (dashboard_service present) and invocable, but not advertised.
    assert "get_reliability" not in user_names
    assert r.is_registered("get_reliability")
    # list_enrichment_datasets is now admin-gated: it folds the admin-only
    # `preview` op (the UI gates preview/apply on admin), so a non-admin must
    # not reach it. Present for admins, hidden from users.
    assert "list_enrichment_datasets" in admin_names
    assert "list_enrichment_datasets" not in user_names


def test_get_reliability_skipped_when_dashboard_service_none():
    # A missing service indicates misconfiguration — skip advertising the tool
    # rather than registering a stub that answers "not available". The LLM
    # would just retry it; absence is more honest.
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                       dashboard_service=None)
    names = {t.name for t in r.for_role("admin")}
    assert "get_reliability" not in names
    # Absent because its backing service is missing — NOT merely hidden like the
    # advertised-alias case: with no dashboard_service it is never registered.
    assert not r.is_registered("get_reliability")


def test_tier2_admin_tools_present_and_admin_gated():
    r = build_registry(
        db=MagicMock(), suggestion_service=MagicMock(),
        dashboard_service=MagicMock(),
        pipeline_service=MagicMock(),
        scheduler_service=MagicMock(),
        integration_service=MagicMock(),
        alerts_service=MagicMock(),
        settings_manager=MagicMock(),
    )
    admin_only = {
        "get_audit_trail", "list_pipeline_runs", "get_scheduler_status",
        "get_integration", "list_alert_rules",
        "get_settings", "get_field_registry", "get_chatbot_usage",
    }
    user_names = {t.name for t in r.for_role("user")}
    admin_names = {t.name for t in r.for_role("admin")}
    assert admin_only.isdisjoint(user_names)
    assert admin_only.issubset(admin_names)


def test_service_dependent_tools_skipped_when_service_none():
    # Same pattern as get_reliability: services that aren't configured don't
    # advertise their tools at all. This test covers the 5 Phase 7 admin tools.
    r = build_registry(
        db=MagicMock(), suggestion_service=MagicMock(),
        pipeline_service=None, scheduler_service=None,
        integration_service=None, alerts_service=None, settings_manager=None,
    )
    names = {t.name for t in r.for_role("admin")}
    assert "list_pipeline_runs" not in names
    assert "get_scheduler_status" not in names
    assert "get_integration" not in names
    assert "list_alert_rules" not in names
    assert "get_settings" not in names
    # db-only tools remain registered.
    assert "get_audit_trail" in names
    assert "get_field_registry" in names


def test_integration_admin_tools_present_and_admin_gated():
    """The five integration ingestion-debug/catalog tools register when the
    integration service is configured, and are hidden from non-admins."""
    r = build_registry(
        db=MagicMock(), suggestion_service=MagicMock(),
        integration_service=MagicMock(),
        mapping_preview_service=MagicMock(),
        integration_group_service=MagicMock(),
    )
    tools = {
        "list_integration_batches", "get_batch", "get_integration_mapping",
        "list_integrations", "get_integration_schedule",
    }
    user_names = {t.name for t in r.for_role("user")}
    admin_names = {t.name for t in r.for_role("admin")}
    assert tools.isdisjoint(user_names)
    assert tools.issubset(admin_names)


def test_integration_admin_tools_skipped_when_integration_service_none():
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                       integration_service=None)
    names = {t.name for t in r.for_role("admin")}
    for name in ("list_integration_batches", "get_batch",
                 "get_integration_mapping", "list_integrations",
                 "get_integration_schedule"):
        assert name not in names


def test_list_integrations_tool_dispatches_to_service():
    """End-to-end through the registry: the binding wires the connector
    catalog to the integration service and returns its real data."""
    integ_svc = MagicMock()
    integ_svc.get_available_connectors.return_value = [
        {"integration_type": "jamf", "display_name": "Jamf Pro",
         "category": "MDM", "config_schema": {"properties": {"base_url": {}}}},
    ]
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                       integration_service=integ_svc)
    inv = asyncio.run(r.invoke("list_integrations", {"view": "connectors"},
                               user_role="admin"))
    assert inv.status == "ok"
    assert "Jamf Pro" in inv.result


def test_integration_admin_tool_denied_for_non_admin():
    """A non-admin invoking an admin-gated integration tool is denied at the
    registry — never served ingestion data."""
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                       integration_service=MagicMock())
    inv = asyncio.run(r.invoke("list_integration_batches",
                               {"integration": "anything"}, user_role="user"))
    assert inv.status == "denied"


# --- admin/observability tools (slice 3b: get_integration / query_metrics /
#     get_chatbot_usage) -----------------------------------------------------

class TestIntegrationObservabilityTools:
    _IID = "11111111-1111-1111-1111-111111111111"

    def test_get_integration_gated_on_integration_service(self):
        with_svc = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                                  integration_service=MagicMock())
        without = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                                 integration_service=None)
        assert "get_integration" in {t.name for t in with_svc.for_role("admin")}
        assert "get_integration" not in {t.name for t in without.for_role("admin")}
        # admin-gated when present
        assert "get_integration" not in {t.name for t in with_svc.for_role("user")}

    def test_get_integration_dispatches_to_services(self):
        """End-to-end through the registry: the binding wires BOTH the
        integration service and the dashboard service, and returns real data."""
        from src.modules.integrations.models.integration import (
            Integration, IntegrationStatus,
        )
        import uuid as _uuid
        integ_svc = MagicMock()
        integ_svc.get_integration.return_value = Integration(
            name="Beszel", integration_type="custom_http", category="Monitoring",
            config={}, field_mapping={}, id=_uuid.UUID(self._IID),
            status=IntegrationStatus.ENABLED)
        ds = MagicMock()
        ds.get_data_sources.return_value = {"count": 1, "sources": [{
            "id": self._IID, "name": "Beszel", "assetCount": 42,
            "status": "healthy", "lastSyncDisplay": "1 hour ago",
            "lastRunDisplay": "1 hour ago", "confidence": 90}]}
        # settings_manager omitted → wrapper uses (None, None) thresholds.
        r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                           integration_service=integ_svc, dashboard_service=ds)
        inv = asyncio.run(r.invoke("get_integration", {"integration": self._IID},
                                   user_role="admin"))
        assert inv.status == "ok"
        assert "Beszel" in inv.result and "42" in inv.result
        ds.get_data_sources.assert_called_once_with(view="individual")

    def test_query_metrics_gated_on_monitoring_service(self):
        """query_metrics advertises only when the Prometheus proxy is wired —
        otherwise it could only ever answer 'not available'."""
        with_mon = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                                  monitoring_service=MagicMock())
        without = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                                 monitoring_service=None)
        assert "query_metrics" in {t.name for t in with_mon.for_role("admin")}
        assert "query_metrics" not in {t.name for t in without.for_role("admin")}
        assert "query_metrics" not in {t.name for t in with_mon.for_role("user")}

    def test_get_chatbot_usage_present_and_admin_gated(self):
        """Self-observability needs only the db, so it's always present — but
        admin-gated (usage aggregates across all users)."""
        r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
        assert "get_chatbot_usage" in {t.name for t in r.for_role("admin")}
        assert "get_chatbot_usage" not in {t.name for t in r.for_role("user")}


def test_semantic_suggest_uses_lazy_getter_when_provided():
    """The embedding service is preloaded in a background thread — at factory
    time the ref is still None. A lazy getter must be resolved at invoke time
    so semantic_suggest starts working as soon as the preload finishes."""
    import asyncio
    box: dict = {"service": None}
    call_count = {"n": 0}

    def getter():
        call_count["n"] += 1
        return box["service"]

    r = build_registry(db=MagicMock(), suggestion_service_getter=getter)
    # Simulate preload completing after registry is built
    svc = MagicMock()
    svc.get_suggestions.return_value = {
        "suggestions": [{"field_name": "os_normalized",
                         "display_name": "Operating System",
                         "type": "field", "score": 0.9}],
    }
    box["service"] = svc

    inv = asyncio.run(r.invoke("semantic_suggest",
                               {"query": "operating system"},
                               user_role="admin"))
    assert "os_normalized" in inv.result
    assert call_count["n"] >= 1, "getter was not called at invoke time"


def test_semantic_suggest_getter_returning_none_falls_back_gracefully():
    """If the getter still returns None (preload failed, etc.), return a
    clear 'unavailable' message, not a crash."""
    import asyncio
    r = build_registry(db=MagicMock(),
                       suggestion_service_getter=lambda: None)
    inv = asyncio.run(r.invoke("semantic_suggest", {"query": "anything"},
                               user_role="admin"))
    assert ("not available" in inv.result.lower()
            or "unavailable" in inv.result.lower())


# --- aggregate_assets artifact production ---------------------------------

def _patch_repo_1d(counts):
    """Context manager returning a patch of AssetRepository whose
    aggregate_assets returns a 1-D result with the given {row: count}."""
    return patch(
        "src.mcp.tools.assets.AssetRepository",
        return_value=MagicMock(
            aggregate_assets=MagicMock(return_value={
                "rows": list(counts.keys()),
                "columns": [],
                "data": {row: {"count": n} for row, n in counts.items()},
                "grand_total": sum(counts.values()),
            }),
        ),
    )


def test_aggregate_assets_1d_produces_donut_artifact():
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
    with _patch_repo_1d({"server": 5, "workstation": 3}):
        inv = asyncio.run(r.invoke(
            "aggregate_assets",
            {"row_field": "category"},
            user_role="user",
        ))
    assert inv.status == "ok"
    # Text summary still carries the counts for the LLM.
    assert "COUNT: 8" in inv.result
    assert "server" in inv.result
    # And now also an artifact payload for the frontend to render.
    assert inv.artifact is not None
    assert inv.artifact["type"] == "donut"
    assert inv.artifact["title"] == "Assets by Category"
    labels = [s["label"] for s in inv.artifact["series"]]
    assert set(labels) == {"server", "workstation"}
    assert inv.artifact["meta"]["total"] == 8


def test_aggregate_assets_2d_produces_stacked_bar_artifact():
    """2-D pivots now produce a stacked-bar artifact."""
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
    with patch(
        "src.mcp.tools.assets.AssetRepository",
        return_value=MagicMock(
            aggregate_assets=MagicMock(return_value={
                "rows": ["server"],
                "columns": ["win", "linux"],
                "data": {"server": {"win": 2, "linux": 3}},
                "grand_total": 5,
            }),
        ),
    ):
        inv = asyncio.run(r.invoke(
            "aggregate_assets",
            {"row_field": "category", "col_field": "os_normalized"},
            user_role="user",
        ))
    assert inv.status == "ok"
    assert inv.artifact is not None
    assert inv.artifact["type"] == "stacked_bar"
    assert inv.artifact["meta"]["total"] == 5


def test_aggregate_assets_unknown_field_returns_hint_without_artifact():
    """Validation failures bypass the query + chart path entirely — the
    LLM gets the hint, and no artifact is produced or persisted."""
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
    with patch("src.mcp.tools.assets.AssetRepository") as MockRepo:
        inv = asyncio.run(r.invoke(
            "aggregate_assets",
            {"row_field": "definitely_not_a_field"},
            user_role="user",
        ))
    assert inv.status == "ok"  # LLM-visible error routed via hint, not status
    assert "definitely_not_a_field" in inv.result
    assert "semantic_suggest" in inv.result
    assert inv.artifact is None
    MockRepo.return_value.aggregate_assets.assert_not_called()


def test_aggregate_assets_empty_result_no_artifact():
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
    with _patch_repo_1d({}):
        inv = asyncio.run(r.invoke(
            "aggregate_assets",
            {"row_field": "category"},
            user_role="user",
        ))
    assert inv.status == "ok"
    assert "No data found" in inv.result
    # Empty input → builder returns None → no chart to render.
    assert inv.artifact is None


# --- list_assets artifact production -----------------------------------------

def test_list_assets_binding_emits_table_with_snake_cased_fields():
    """Verify the table artifact emitted by _list_assets contains the
    snake-cased fields (hostname, category, ...), not the encoded FieldId
    keys returned by the raw repo layer."""
    encoded_row = {
        "id": "a-1",
        "static:Hostname": ["host1.example"],
        "static:Category": "server",
        "static:OS%20Normalized": ["Windows 10"],
        "static:Data%20Source": ["jamf"],
    }
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
    with patch(
        "src.mcp.tools.assets.AssetRepository",
        return_value=MagicMock(
            list_assets=MagicMock(return_value=([encoded_row], 1)),
        ),
    ):
        inv = asyncio.run(r.invoke(
            "list_assets",
            {},
            user_role="user",
        ))
    assert inv.status == "ok"
    artifact = inv.artifact
    assert artifact is not None
    assert artifact["type"] == "table"
    # Each series row must carry snake_case keys, not the encoded FieldId form.
    assert len(artifact["series"]) == 1
    row = artifact["series"][0]
    assert "hostname" in row
    assert row["hostname"] == ["host1.example"]
    assert row["category"] == "server"
    # Encoded-key forms must not appear.
    assert "static:Hostname" not in row
    assert "static:Category" not in row


# --- deprecation shims (old names → query_assets) ----------------------------

class TestDeprecationShims:
    """The 4 old tool names (search_assets / list_assets / aggregate_assets /
    get_field_values) stay callable as thin shims onto query_assets, so existing
    evals + clients keep working through the 1-2 release deprecation window.
    Each test proves the shim still returns a real result, not an error."""

    def _registry(self):
        return build_registry(db=MagicMock(), suggestion_service=MagicMock())

    def _repo_list(self, rows, total):
        return patch(
            "src.mcp.tools.assets.AssetRepository",
            return_value=MagicMock(
                list_assets=MagicMock(return_value=(rows, total)),
            ),
        )

    def test_search_assets_shim_returns_results(self):
        with self._repo_list(
            [{"id": "a1", "hostname": ["host01"], "category": "server"}], 1,
        ):
            inv = asyncio.run(self._registry().invoke(
                "search_assets", {"query": "host01"}, user_role="user",
            ))
        assert inv.status == "ok"
        assert "host01" in inv.result

    def test_list_assets_shim_returns_results_with_artifact(self):
        with self._repo_list(
            [{"id": "a1", "hostname": ["host01"], "category": "server"}], 1,
        ):
            inv = asyncio.run(self._registry().invoke(
                "list_assets", {}, user_role="user",
            ))
        assert inv.status == "ok"
        assert "host01" in inv.result
        assert inv.artifact is not None and inv.artifact["type"] == "table"

    def test_aggregate_assets_shim_returns_count(self):
        with _patch_repo_1d({"server": 5, "workstation": 3}):
            inv = asyncio.run(self._registry().invoke(
                "aggregate_assets", {"row_field": "category"}, user_role="user",
            ))
        assert inv.status == "ok"
        assert "COUNT: 8" in inv.result

    def test_get_field_values_shim_returns_values(self):
        with patch(
            "src.mcp.tools.assets.AssetRepository",
            return_value=MagicMock(
                get_field_values=MagicMock(return_value=(
                    [{"value": "server", "count": 7}], 1)),
            ),
        ):
            inv = asyncio.run(self._registry().invoke(
                "get_field_values", {"field": "category"}, user_role="user",
            ))
        assert inv.status == "ok"
        assert "server" in inv.result


# --- get_asset: human-identifier resolution + rich detail --------------------

class TestGetAssetBinding:
    """get_asset now resolves a UUID OR a human identifier and renders
    UI-fidelity detail (ext-attr VALUES, folded source breakdown)."""

    _AID = "11111111-1111-1111-1111-111111111111"

    def _repo(self):
        repo = MagicMock()
        repo.query_assets.return_value = (
            [{"id": self._AID, "hostname": ["host01"]}], 1)
        repo.get_asset_by_id.return_value = {
            "id": self._AID, "hostname": ["host01"], "category": "workstation",
            "extended_attributes": {"AssetAtlas": {"ip_public": ["8.8.8.8"]}},
        }
        repo.get_asset_tags.return_value = []
        repo.get_asset_providers.return_value = [
            {"data_source": "CrowdStrike", "source_name": "CrowdStrike Falcon",
             "source_type": "edr", "data_source_fields": {"cpu_model": "Xeon"}},
        ]
        return repo

    def _invoke(self, args):
        repo = self._repo()
        r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
        with patch("src.mcp.tools.assets.AssetRepository", return_value=repo), \
             patch("src.api.blueprints.chat.tool_bindings.AssetRepository",
                   return_value=repo):
            inv = asyncio.run(r.invoke("get_asset", args, user_role="user"))
        return inv, repo

    def test_resolves_hostname_and_shows_ext_attr_values(self):
        inv, repo = self._invoke({"identifier": "host01"})
        assert inv.status == "ok"
        assert "host01" in inv.result
        assert "Xeon" in inv.result                       # ext-attr VALUE
        assert inv.artifact is not None and inv.artifact["type"] == "table"
        repo.query_assets.assert_called_once()            # human id resolved

    def test_accepts_asset_id_alias_uuid_without_lookup(self):
        inv, repo = self._invoke({"asset_id": self._AID})
        assert inv.status == "ok"
        assert "host01" in inv.result
        repo.query_assets.assert_not_called()             # UUID → no search

    def test_unknown_identifier_is_loud_without_artifact(self):
        repo = self._repo()
        repo.query_assets.return_value = ([], 0)
        r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
        with patch("src.mcp.tools.assets.AssetRepository", return_value=repo):
            inv = asyncio.run(r.invoke("get_asset", {"identifier": "ghost"},
                                       user_role="user"))
        assert inv.status == "ok"
        assert "ghost" in inv.result
        assert inv.artifact is None

    def test_source_breakdown_shim_delegates_and_shows_conflict(self):
        """The standalone get_asset_source_breakdown is retired to a shim onto
        get_asset, whose detail now carries the per-source conflict roll-up."""
        repo = MagicMock()
        repo.get_asset_by_id.return_value = {
            "id": self._AID, "hostname": ["host01"], "category": "server"}
        repo.get_asset_tags.return_value = []
        repo.get_asset_providers.return_value = [
            {"data_source": "Jamf", "source_name": "Jamf", "source_type": "mdm",
             "hostname": "host01"},
            {"data_source": "Intune", "source_name": "Intune",
             "source_type": "mdm", "hostname": "HOST01-alt"},
        ]
        r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
        with patch("src.mcp.tools.assets.AssetRepository", return_value=repo), \
             patch("src.api.blueprints.chat.tool_bindings.AssetRepository",
                   return_value=repo):
            inv = asyncio.run(r.invoke("get_asset_source_breakdown",
                                       {"asset_id": self._AID}, user_role="user"))
        assert inv.status == "ok"
        assert "conflict" in inv.result.lower()
        assert "host01-alt" in inv.result.lower()


# --- get_asset_history: resolve + interleave + custom range ------------------

class TestGetAssetHistoryBinding:
    _AID = "22222222-2222-2222-2222-222222222222"

    def test_resolves_hostname_and_interleaves_alerts(self):
        repo = MagicMock()
        repo.query_assets.return_value = (
            [{"id": self._AID, "hostname": ["host01"]}], 1)
        db = MagicMock()
        db.execute_query.side_effect = [
            [{"id": self._AID, "hostname": ["host01"], "category": "server",
              "ip": []}],
            [{"id": 1, "time": "2026-04-20T10:00:00Z",
              "event_type": "attribute_updated", "changed_attributes": None,
              "attribute_changes": {"owner": {"old": ["a"], "new": ["b"],
                                              "source": "Jamf"}}}],
            [{"id": "alert-x", "type": "compliance", "severity": "high",
              "title": "EDR missing", "description": "", "status": "active",
              "created_at": "2026-04-21T09:00:00Z", "resolved_at": None}],
        ]
        r = build_registry(db=db, suggestion_service=MagicMock())
        with patch("src.mcp.tools.assets.AssetRepository", return_value=repo):
            inv = asyncio.run(r.invoke(
                "get_asset_history",
                {"identifier": "host01", "time_range": "30d"},
                user_role="user"))
        assert inv.status == "ok"
        assert "owner" in inv.result
        assert "EDR missing" in inv.result
        repo.query_assets.assert_called_once()

    def test_custom_range_threads_through(self):
        db = MagicMock()
        db.execute_query.side_effect = [
            [{"id": self._AID, "hostname": ["h"], "category": "server",
              "ip": []}],
            [], [],
        ]
        r = build_registry(db=db, suggestion_service=MagicMock())
        inv = asyncio.run(r.invoke(
            "get_asset_history",
            {"identifier": self._AID, "start": "2026-01-01",
             "end": "2026-02-01"},
            user_role="user"))
        assert inv.status == "ok"
        starts = [c.args[1].get("start") for c in db.execute_query.call_args_list
                  if len(c.args) > 1 and isinstance(c.args[1], dict)
                  and "start" in c.args[1]]
        assert starts and all(s.year == 2026 and s.month == 1 for s in starts)


# --- analytics + catalog tools (run_chart / run_report / describe_catalog) ---

class TestAnalyticsAndCatalogTools:
    def _registry(self, db=None):
        return build_registry(db=db or MagicMock(), suggestion_service=MagicMock(),
                              field_resolver=_resolver())

    def test_tools_registered_for_users(self):
        names = {t.name for t in self._registry().for_role("user")}
        assert {"run_chart", "run_report", "describe_catalog"}.issubset(names)

    def test_describe_catalog_surfaces_per_field_operators(self):
        """The discovery gap describe_catalog closes: the model sees which
        operators each field accepts (today's list_fields dropped them)."""
        inv = asyncio.run(self._registry().invoke(
            "describe_catalog",
            {"view": "fields", "search": "Confidence Score"}, user_role="user"))
        assert inv.status == "ok"
        assert "between" in inv.result

    def test_describe_catalog_operators_view(self):
        inv = asyncio.run(self._registry().invoke(
            "describe_catalog", {"view": "operators"}, user_role="user"))
        assert inv.status == "ok"
        assert "within_last" in inv.result and "any of" in inv.result

    def test_run_chart_builds_donut_through_registry(self):
        db = MagicMock()
        db.execute_query.return_value = [{"label": "server", "value": 5},
                                         {"label": "workstation", "value": 3}]
        inv = asyncio.run(self._registry(db).invoke(
            "run_chart", {"query_type": "distribution", "dimension": "Category"},
            user_role="user"))
        assert inv.status == "ok"
        assert inv.artifact is not None and inv.artifact["type"] == "donut"
        # the binding must wire the args through correctly — assert the canned
        # buckets actually reach the artifact, not just that a donut shell exists
        labels = {s["label"] for s in inv.artifact["series"]}
        assert {"server", "workstation"} <= labels

    def test_run_chart_rejects_unknown_query_type_via_schema(self):
        """The enum gate rejects an out-of-set query_type cleanly BEFORE the
        handler runs, so the model gets a corrective message not a crash."""
        inv = asyncio.run(self._registry().invoke(
            "run_chart", {"query_type": "pie"}, user_role="user"))
        assert inv.status == "error"
        assert "pie" in inv.result or "allowed" in inv.result.lower()

    def test_run_report_replays_through_registry(self):
        db = MagicMock()
        db.execute_query.side_effect = [
            [{"id": "r1", "title": "R", "charts": [{
                "title": "C", "queryConfig": {
                    "queryType": "distribution",
                    "select": {"dimension": "static:Category"},
                    "filters": [], "options": {}}}]}],
            [{"label": "server", "value": 5}],
        ]
        inv = asyncio.run(self._registry(db).invoke(
            "run_report",
            {"report_id": "11111111-1111-1111-1111-111111111111"},
            user_role="user"))
        assert inv.status == "ok"
        assert inv.artifact is not None and inv.artifact["type"] == "donut"

    def test_list_fields_shim_now_uses_describe_catalog(self):
        """list_fields is kept as a deprecation shim onto describe_catalog so old
        callers/evals keep working through the deprecation window."""
        inv = asyncio.run(self._registry().invoke(
            "list_fields", {"search": "Category"}, user_role="user"))
        assert inv.status == "ok"
        assert "static:Category" in inv.result


# --- §2.D Posture & Rules tools -----------------------------------------------

def test_compliance_matrix_present_non_admin_and_dispatches():
    repo = MagicMock()
    repo.list_categories.return_value = [{"id": "secured", "name": "Secured"}]
    repo.get_category_stats.return_value = {
        "secured": {"pass": 1, "fail": 0, "n_a": 0, "by_asset_category": {}}}
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                       posture_repository=repo)
    user_names = {t.name for t in r.for_role("user")}
    assert "get_compliance_matrix" in user_names  # mirrors UI read access
    inv = asyncio.run(r.invoke("get_compliance_matrix", {"group_by": "none"},
                               user_role="user"))
    assert inv.status == "ok"
    assert "Secured" in inv.result
    assert inv.artifact is not None and inv.artifact["type"] == "table"


def test_compliance_matrix_skipped_when_repo_none():
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                       posture_repository=None)
    assert "get_compliance_matrix" not in {t.name for t in r.for_role("admin")}


def test_list_rules_present_non_admin_when_a_rule_service_configured():
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                       alerts_service=MagicMock())
    assert "list_rules" in {t.name for t in r.for_role("user")}


def test_rule_authoring_tools_admin_gated():
    r = build_registry(
        db=MagicMock(), suggestion_service=MagicMock(),
        tag_rule_service=MagicMock(), tag_preview_service=MagicMock(),
        alerts_service=MagicMock(),
        posture_category_read_service=MagicMock(),
        posture_preview_service=MagicMock(),
        posture_sub_rule_mgmt_service=MagicMock())
    user_names = {t.name for t in r.for_role("user")}
    admin_names = {t.name for t in r.for_role("admin")}
    authoring = {"preview_rule", "preview_rule_output", "get_rule_history"}
    assert authoring.isdisjoint(user_names)
    assert authoring.issubset(admin_names)


def test_rule_tools_skipped_when_no_services():
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
    names = {t.name for t in r.for_role("admin")}
    for name in ("list_rules", "preview_rule", "preview_rule_output",
                 "get_rule_history", "get_compliance_matrix"):
        assert name not in names


def test_preview_rule_denied_for_non_admin():
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                       posture_preview_service=MagicMock())
    inv = asyncio.run(r.invoke(
        "preview_rule",
        {"rule_kind": "posture",
         "target_filters": [{"field": "Category", "op": "eq", "value": "x"}]},
        user_role="user"))
    assert inv.status == "denied"


def test_list_rules_invoke_dispatches_to_alert_service():
    alert_svc = MagicMock()
    alert_svc.get_all_rules.return_value = {"rules": [
        {"id": "a1", "name": "A1", "type": "security", "severity": "high",
         "source": "system", "enabled": True, "evaluation_type": "sql",
         "query": "SELECT 1"}]}
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                       alerts_service=alert_svc)
    inv = asyncio.run(r.invoke("list_rules", {"rule_kind": "alert"},
                               user_role="user"))
    assert inv.status == "ok"
    assert "A1" in inv.result and "id=a1" in inv.result


def test_find_tools_is_registered_and_invocable():
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock())
    assert r.is_registered("find_tools")


def test_find_tools_handler_resolves_router_without_app_context():
    """Regression for the PR #803 find_tools recovery-net bug.

    The chat turn — and every tool handler it invokes via registry.invoke —
    runs inside a daemon worker thread with NO Flask app context (see
    routes.py: ``threading.Thread(target=lambda: asyncio.run(driver()))``).
    The original handler read ``getattr(current_app, "chat_tool_router", None)``
    there; current_app raises ``RuntimeError`` outside an app context, and
    getattr's default only swallows ``AttributeError`` — so the RuntimeError
    propagated, invoke() caught it as a generic tool error, and the routing
    recovery net was silently dead whenever the flag was on. The fix injects
    the router via the ``current_tool_router`` contextvar (set per-turn in
    service._stream_inner), which DOES survive the thread boundary because a new
    task copies the current context.

    This drives the exact production topology: invoke find_tools inside a
    context-less worker thread with the contextvars set, and require an OK
    result that lists a discoverable tool — not the RuntimeError-derived error
    the bug produced.
    """
    from src.api.blueprints.chat.tool_router import (
        ToolRouter, current_chat_role, current_tool_router)

    registry = build_registry(db=MagicMock(), suggestion_service=MagicMock())
    router = ToolRouter(registry, embedder=None, top_k=5)
    result: dict = {}

    def _target():
        async def _drive():
            # Mirror service._stream_inner: set both contextvars before any
            # tool invocation. No app context is pushed in this thread.
            current_chat_role.set("admin")
            current_tool_router.set(router)
            inv = await registry.invoke(
                "find_tools", {"query": "pipeline run status"},
                user_role="admin")
            result["status"] = inv.status
            result["text"] = inv.result or ""
        asyncio.run(_drive())

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=10)

    # On the bug: status == "error" with "Working outside of application
    # context" in the error. Fixed: a normal OK result naming the gated tool.
    assert result.get("status") == "ok", (
        "find_tools errored in a context-less worker thread — the handler "
        f"could not resolve its router: {result!r}")
    # And the recovery net actually rendered loadable tools — the router
    # resolved AND find() returned matches. Assert the load message (the
    # behavior that matters) rather than a specific tool name, which is a
    # keyword-ranking artifact brittle to tool renames.
    assert "Loaded these tools" in result["text"], result


# --- #939 UI->chat parity additions: alert history + unified fields ----------

_UUID = "550e8400-e29b-41d4-a716-446655440000"


def test_get_alert_history_present_non_admin_and_gated_on_service():
    """get_alert_history mirrors the jwt-only Alerts drilldown endpoint: present
    for regular users when the alerts service is wired, absent without it."""
    with_svc = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                              alerts_service=MagicMock())
    without = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                             alerts_service=None)
    assert "get_alert_history" in {t.name for t in with_svc.for_role("user")}
    assert "get_alert_history" not in {t.name for t in without.for_role("admin")}


def test_get_alert_history_dispatches_to_alerts_service():
    """End-to-end through the registry: the binding wires the alerts service and
    renders its firing-history series."""
    alert_svc = MagicMock()
    alert_svc.get_alert_history.return_value = {
        "alert_id": _UUID,
        "history": [
            {"pipeline_run_id": "run-1", "status": "firing", "asset_count": 4,
             "severity": "high", "started_at": "2026-04-20T09:00:00",
             "created_at": "2026-04-20T09:00:00"},
        ],
    }
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                       alerts_service=alert_svc)
    inv = asyncio.run(r.invoke("get_alert_history", {"alert_id": _UUID},
                               user_role="user"))
    assert inv.status == "ok"
    assert "firing-history event(s)" in inv.result
    assert "high" in inv.result and "run-1" in inv.result
    alert_svc.get_alert_history.assert_called_once_with(_UUID, days=90)


def test_unified_fields_tools_present_non_admin_and_gated_on_service():
    """list_unified_fields / get_unified_field mirror the jwt-only Unified
    Fields read endpoints: present for regular users when the service is wired,
    absent without it."""
    with_svc = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                              unified_fields_service=MagicMock())
    without = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                             unified_fields_service=None)
    user_names = {t.name for t in with_svc.for_role("user")}
    assert {"list_unified_fields", "get_unified_field"}.issubset(user_names)
    admin_without = {t.name for t in without.for_role("admin")}
    assert "list_unified_fields" not in admin_without
    assert "get_unified_field" not in admin_without


def test_unified_fields_tools_dispatch_to_service():
    """End-to-end through the registry: both bindings wire the unified-fields
    service and return its real data (catalog + single field)."""
    field = {"id": _UUID, "name": "Primary Hostname", "function": "coalesce",
             "sources": ["static:Hostname", "ext:Jamf:device_name"],
             "data_type": "string", "encoded_key": "virtual:primary_hostname",
             "created_at": None, "updated_at": None}
    svc = MagicMock()
    svc.list.return_value = [field]
    svc.get.return_value = field
    r = build_registry(db=MagicMock(), suggestion_service=MagicMock(),
                       unified_fields_service=svc)

    lst = asyncio.run(r.invoke("list_unified_fields", {}, user_role="user"))
    assert lst.status == "ok"
    assert "Primary Hostname" in lst.result
    assert "virtual:primary_hostname" in lst.result

    one = asyncio.run(r.invoke("get_unified_field", {"field_id": _UUID},
                               user_role="user"))
    assert one.status == "ok"
    assert "Primary Hostname" in one.result and "coalesce" in one.result
    svc.get.assert_called_once_with(_UUID)
