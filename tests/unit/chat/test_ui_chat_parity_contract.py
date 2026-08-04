# SPDX-License-Identifier: AGPL-3.0-or-later
"""UI -> chat read-parity CONTRACT TEST (plan SS2.H / SS6.2).

This is the build-failing drift guard that locks in the chatbot<->UI read parity
the chat-parity project delivered (53 tools). The field/operator/bucket checks
assert BEHAVIOUR (round-trip + resolve through the real query path), not mere
tool-name presence (the design's own stated risk); endpoint coverage is a
hand-reviewed manifest whose job is to make any drift (a new route, a removed
tool) a conscious decision rather than a silent gap:

1. **Endpoint coverage** - every UI *read* endpoint (`/api/...`, GET) maps to a
   covering chat tool via the hand-reviewed ``ENDPOINT_TO_TOOL`` manifest, or is
   listed in ``EXCLUDED_ENDPOINTS`` with a reason. A *new* read route that is in
   neither turns the build RED, forcing a conscious decision: add a tool, or
   document why it is out of scope. A dropped/renamed tool a manifest points at
   also turns the build RED.
2. **Field reachability** - every live-catalog field's ``encoded_key`` round-trips
   ``encode(decode(key)) == key`` AND resolves through the real ``query_assets``
   field path (``_query._normalize_filters``) without raising. A field the model
   can *discover* via ``describe_catalog`` but cannot *use* in ``query_assets`` is
   a parity defect (the model is told a field exists, then errors using it).
3. **Operator advertisement** - every ``OPERATOR_REGISTRY`` operator appears in
   some field's ``operator_specs`` (so the model can discover it per-field via
   ``describe_catalog``), except the documented ``EXCLUDED_OPERATORS``.
4. **Bucket coverage** - every catalog bucket has >=1 reachable, filterable field,
   else an entire class of data (e.g. all tags, all posture states) is
   unreachable from chat.

Lanes (SS6.2): the field/operator/bucket round-trips over the *static* catalog are
PURE (no DB) and gate every PR in the default lane. The dynamic buckets
(extended_attribute / integration_group / tag / posture) and the SS6.3
SQL<->SQL equivalence check need DB-backed catalog data and are marked
``integration`` so the pure core still gates every PR.

Why a manifest and not "tool name X exists"? Name presence proves nothing about
parity. The manifest is the human-reviewed claim "this UI surface IS reachable
from chat via this tool"; the test mechanises the *drift* half - you cannot add a
read endpoint, remove a tool, add an operator, or add a bucket without the
contract noticing.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock  # stands in for the DB adapter / chat
# services so the pure lane needs no live DB.

import pytest

from src.api.blueprints.chat.tool_bindings import build_registry
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.catalog import build_catalog_response
from src.modules.fields.encoding import decode, encode
from src.modules.fields.operators import OPERATOR_REGISTRY
from src.mcp.tools._query import FieldResolutionError, _normalize_filters
from src.mcp.tools.field_registry import _CATALOG_BUCKETS, _entry_matches


# ===========================================================================
# 1. ENDPOINT -> TOOL manifest (hand-reviewed; SS6.2 endpoint coverage)
# ===========================================================================
# Keyed by Flask *endpoint* name (unique; stable; survives path collisions like
# the two `.../schedule/next-runs` routes). Value = the chat tool that makes the
# same data reachable. Reviewed against the 53-tool registry on 2026-06-20.
ENDPOINT_TO_TOOL = {
    # --- Assets / Explorer (the catalog-driven core) ---
    "assets.list_assets": "query_assets",
    "assets.search_assets": "query_assets",
    "assets.aggregate_assets": "query_assets",
    "assets.get_field_values": "query_assets",
    "assets.get_asset_detail": "get_asset",
    "assets.get_asset_history": "get_asset_history",
    "assets.export_assets_csv": "export_assets",
    "assets.export_aggregate_csv": "export_assets",
    "assets.get_explorer_fields": "describe_catalog",
    "asset_edit.get_categories": "get_asset_categories",
    "asset_edit.get_tags": "get_asset",  # get_asset renders the asset's tags
    "external_assets.query_assets": "query_assets",      # /api/v1 (same data)
    "external_assets.inventory_assets": "query_assets",  # /api/v1 inventory
    "external_assets.list_fields": "describe_catalog",   # /api/v1 field catalog
    # --- Analytics / charts ---
    "charts.get_fields": "describe_catalog",
    # --- Dashboard / relationship (DashboardService-backed) ---
    "dashboard.get_dashboard_summary": "get_dashboard",
    "dashboard.get_dashboard_inventory": "get_dashboard",
    "dashboard.get_dashboard_posture": "get_dashboard",
    "dashboard.get_dashboard_confidence_breakdown": "get_dashboard",
    "dashboard.get_data_sources": "get_dashboard",
    "dashboard.get_dashboard_reliability": "get_dashboard",
    "dashboard.get_dashboard_asset_timeline": "get_dashboard_trend",
    "dashboard.get_dashboard_posture_trend": "get_dashboard_trend",
    "dashboard.get_dashboard_coverage_trend": "get_dashboard_trend",
    "dashboard.get_dashboard_flowers": "get_relationship_graph",
    "dashboard.get_dashboard_wheels_data": "get_relationship_graph",
    "dashboard.get_dashboard_alerts": "get_alerts",
    # --- Alerts ---
    "alerts.get_alerts": "get_alerts",
    "alerts.get_alert_history": "get_alert_history",
    # --- Posture & rules ---
    "posture_rules.list_categories": "list_rules",
    "posture_rules.get_category": "list_rules",
    "posture_rules.get_sub_rule_history": "get_rule_history",
    "tag_rules.list_rules": "list_rules",
    "tag_rules.get_rule": "list_rules",
    "tag_rules.get_rule_statistics": "list_rules",
    "tag_rules.get_fields": "describe_catalog",
    "alert_rules.list_rules": "list_rules",
    "alert_rules.get_rule": "list_rules",
    # --- Integrations (admin, ingestion-debug) ---
    "integrations_crud.list_integrations": "list_integrations",
    "integrations_crud.get_integration": "get_integration",
    "integrations_crud.get_integration_insights": "get_integration",
    "integrations_crud.get_integration_timeline": "get_integration",
    "integrations_crud.get_field_schema": "get_integration_mapping",
    "integrations_crud.get_stats": "list_integrations",
    "asset_generation.get_integration_health": "get_integration",
    "asset_generation.get_integration_generation_stats": "get_integration",
    "asset_generation.get_integration_generation_history": "get_integration",
    "asset_generation.get_integration_recent_errors": "get_integration",
    "asset_generation.get_generation_status": "list_pipeline_runs",
    "asset_generation.get_schedule_next_runs": "get_integration_schedule",
    "batches.list_batches": "list_integration_batches",
    "batches.get_batch": "get_batch",
    "batches.get_batch_records": "get_batch",
    "batches.get_mapped_records": "get_batch",
    "batches.get_mapping_preview": "get_integration_mapping",
    "batches.get_mapping_stats": "get_integration_mapping",
    "batches.get_raw_data": "get_integration_mapping",
    "batches.get_batch_stats": "get_integration",
    "batches.get_global_batch_stats": "list_integrations",
    "schedules.get_schedule": "get_integration_schedule",
    "schedules.get_next_runs": "get_integration_schedule",
    "schedules.get_scheduler_status": "get_scheduler_status",
    "connectors.list_available_connectors": "list_integrations",
    "connectors.get_connector_schema": "list_integrations",
    "integration_groups.list_groups": "list_integrations",
    "integration_groups.get_group": "list_integrations",
    "integration_groups.get_group_mapping": "list_integrations",
    # --- Enrichment ---
    "enrichment.list_datasets": "list_enrichment_datasets",
    "enrichment.get_dataset": "list_enrichment_datasets",
    "enrichment.list_ext_fields": "list_enrichment_datasets",
    # --- Pipeline ---
    "pipeline.get_pipeline_runs": "list_pipeline_runs",
    "pipeline.get_pipeline_run": "list_pipeline_runs",
    "pipeline.get_pipeline_run_phases": "list_pipeline_runs",
    "pipeline.get_pipeline_status": "list_pipeline_runs",
    # --- Scheduler ---
    "scheduler_monitoring.scheduler_health": "get_scheduler_status",
    "scheduler_monitoring.scheduler_metrics": "get_scheduler_status",
    "scheduler_monitoring.scheduler_alerts": "get_scheduler_status",
    "scheduler_monitoring.job_execution_history": "get_scheduler_status",
    "scheduler_monitoring.job_statistics": "get_scheduler_status",
    "scheduler_monitoring.job_health": "get_scheduler_status",
    # --- Monitoring / infra (Prometheus proxy) ---
    "monitoring.proxy_query": "query_metrics",
    "monitoring.proxy_query_range": "query_metrics",
    # --- Reports ---
    "reports.list_reports": "list_reports",
    "reports.get_report": "get_report",
    "reports.get_default_report": "get_report",
    # --- Settings / audit / field-registry ---
    "settings.get_all_settings": "get_settings",
    "settings.get_category_settings": "get_settings",
    "settings.get_single_setting": "get_settings",
    "settings.get_email_settings": "get_settings",
    "settings.get_settings_schema": "get_settings",
    "settings.get_security_effective": "get_settings",
    "field_registry.list_fields": "get_field_registry",
    # --- Unified Fields (Settings -> Data): user-defined merges surfaced as
    #     virtual:<slug> catalog fields ---
    "unified_fields.list_unified_fields": "list_unified_fields",
    "unified_fields.get_unified_field": "get_unified_field",
    "audit_trail.get_audit_trail": "get_audit_trail",
    # --- Suggestions ---
    "suggestions.get_suggestions": "semantic_suggest",
    # --- Chatbot self-observability ---
    "chat.admin_usage": "get_chatbot_usage",
}


# ===========================================================================
# EXCLUDED endpoints (hand-reviewed; each with a reason). A read endpoint here is
# deliberately NOT covered by a chat tool. Categories: auth/identity, health,
# static/docs/UI-chrome, mutation-or-action endpoints, and admin/ops surfaces
# the plan (SS1 in-scope list, SS8 ratified scope) deliberately deferred.
# ===========================================================================
EXCLUDED_ENDPOINTS = {
    # --- OpenAPI docs / framework (SS6.2 excludes docs/static) ---
    "flasgger.apispec": "OpenAPI spec endpoint (docs), not a data surface.",
    "flasgger.apidocs": "Swagger UI (docs), not a data surface.",
    # --- Health / liveness / system info (SS6.2 excludes health) ---
    "health.health_check": "Health probe.",
    "health.readiness_probe": "Health probe.",
    "health.liveness_probe": "Health probe.",
    "health.in_flight_check": "Health/ops probe (in-flight requests).",
    "health.log_file_health": "Health/ops probe (log files).",
    "health.system_about": "Build/version system-info, not asset data.",
    "health.get_update_check": "Update-availability system-info, not asset data.",
    "pipeline.get_data_version": "Cache-coherency signal (id of the last finished "
                                 "run) the UI polls to drop caches a pipeline run "
                                 "invalidated; the runs themselves are covered by "
                                 "list_pipeline_runs.",
    "charts.health_check": "Charts subsystem health probe.",
    "suggestions.health_check": "Suggestion-service health probe.",
    "monitoring.monitoring_status": "Prometheus reachability probe backing the "
                                    "Settings dashboards toggle; not asset data "
                                    "(monitoring data is covered by query_metrics).",
    "monitoring.monitoring_storage": (
        "Prometheus TSDB storage/retention telemetry backing the Settings "
        "dashboards panel; not asset data (metrics are covered by query_metrics)."
    ),
    # --- Auth / identity / user & session management (SS6.2 excludes auth) ---
    "auth.get_current_user": "Auth: current-user identity.",
    "auth.mfa_status": "Auth: MFA status.",
    "auth.oidc_callback": "Auth: OIDC callback.",
    "auth.oidc_test_discovery": "Auth: OIDC discovery test.",
    "auth.validate_onboarding_token": "Auth: onboarding token validation.",
    "auth.get_session_config": "Auth: session policy config.",
    "auth.get_sso_providers": "Auth: SSO provider list.",
    "auth.list_users": "Auth: user administration (not asset data).",
    "auth.get_user": "Auth: user administration.",
    "auth.get_user_stats": "Auth: user administration stats.",
    "account.get_account_mfa": "Auth: own-account MFA settings.",
    "teams_bot.bridge_config": "Auth: Teams-bridge linking config.",
    "teams_bot.get_conversation_session": "Auth: Teams-bridge conversation session.",
    # --- API-key credential management (security/admin surface; like users) ---
    "api_keys.list_api_keys": "API-key credential administration; security "
                              "surface outside the asset-data parity scope.",
    "api_keys.get_api_key": "API-key credential administration; security surface.",
    # --- Asset inline-edit / merge (mutation feature; SS8 defers mutations) ---
    "asset_edit.get_category_override": "Form-backing read for the category-override "
                                        "mutation (whether an asset was reclassified "
                                        "by hand, by whom, and what the pipeline "
                                        "predicted); the effective category is "
                                        "reachable via get_asset.",
    "asset_edit.get_label_override": "Form-backing read for the asset-label "
                                     "mutation; the effective label is reachable "
                                     "via the asset_label virtual field / get_asset.",
    "asset_edit.get_merge_history": "Asset-merge feature (mutation per SS8); merge "
                                    "audit is not a planned chat read.",
    "asset_edit.merge_preview": "Asset-merge dry-run (mutation preview per SS8).",
    "asset_edit.split_preview": "Asset-split dry-run (mutation preview); not a chat read.",
    "asset_edit.tag_bulk_candidates": "Picker support for the admin bulk-untag "
                                      "write (which manual tags the filtered set "
                                      "carries); chat has no bulk-write "
                                      "equivalent, and an asset's tags are "
                                      "already reachable via get_asset.",
    # --- Merge denylist (admin over-merge fix, PR #972) — not asset-data reads ---
    "merge_denylist.list_denylist": "Admin merge-denylist configuration (untrusted "
                                    "identifiers the pipeline must not merge on); "
                                    "admin config surface, not asset data.",
    "assets.get_merge_keys": "Per-asset merge-key diagnostic backing the admin "
                             "'Don't trust this identifier' workflow; niche "
                             "identity-mapping view, not a planned chat read.",
    # --- Action endpoints (side-effecting; not pure reads) ---
    "integrations_crud.test_connection": "Connection-test ACTION (GET+POST; "
                                         "triggers a live external call). The "
                                         "stored test state is in get_integration.",
    # --- UI chrome / static asset serving ---
    "integrations_crud.get_logo_map": "Integration-logo map (UI chrome).",
    "integrations_crud.list_available_logos": "Integration logos (UI chrome).",
    "integrations_crud.serve_uploaded_logo": "Serves a logo image file (static).",
    "teams_settings.manifest_zip": "Teams app manifest .zip download (provisioning).",
    "teams_settings.get_messaging_endpoint": "Teams messaging-endpoint provisioning.",
    # --- Chat's OWN infrastructure (not the asset-data surface) ---
    "chat.admin_audit": "Chat message/tool audit log (chat-admin infra); aggregate "
                        "self-observability is covered by get_chatbot_usage.",
    "chat.admin_get_config": "Chat LLM/provider configuration surface.",
    "chat.admin_list_bedrock_models": "Chat LLM model picker (provider config).",
    "chat.list_sessions": "The user's own chat conversation list (chat UI infra).",
    "chat.get_session": "A chat conversation transcript (chat UI infra).",
    "chat.usage": "Per-user own chat quota; admin aggregate is get_chatbot_usage.",
    # --- Admin/ops surfaces the plan did NOT enumerate as in-scope (SS1/SS8) ---
    "log_management.get_retention_settings": "Log-retention ops config; outside "
                                             "the asset-data parity scope.",
    "log_management.get_cleanup_status": "Log-cleanup job status (ops).",
    "reference_data.get_status": "Reference-data (scraper) freshness status (ops); "
                                 "not in the SS1 in-scope data surface.",
    "search.get_synonyms": "Search-synonym admin config; user-facing search is via "
                           "query_assets `search=` / semantic_suggest.",
    "search.get_synonym_stats": "Search-synonym admin stats.",
    "suggestions.get_stats": "Embedding-suggestion service ops stats; the capability "
                             "itself is semantic_suggest.",
    "admin_embedding.embedding_status": "Embedding-model load/ops status (admin).",
    "teams_settings.get_teams_settings": "Teams-integration config surface; not "
                                         "asset data.",
    # --- Alert delivery-channel config (#917-#920): webhook/email/syslog
    #     channel definitions + category routing — delivery configuration, not
    #     asset data (the alerts themselves are reachable via get_alerts) ---
    "alert_channels.list_channels": "Alert delivery-channel config "
                                    "(webhook/email/syslog), not asset data.",
    "alert_channels.get_channel": "Alert delivery-channel config "
                                  "(delivery settings), not asset data.",
    "alert_channels.list_channel_types": "Static delivery-channel-type metadata "
                                         "(config), not asset data.",
    "alert_channels.get_routing": "Alert category-to-channel routing config, "
                                  "not asset data.",
    # --- Metrics-forwarding config (#922): Prometheus remote_write settings ---
    "metrics_forwarding.get_config": "admin-only metrics-forwarding config (#922); "
                                     "secret masked to has_secret — settings surface, "
                                     "not chatbot asset-read data",
    # --- Saved Explorer filters (#775-780): a user's OWN saved queries — UI
    #     state, not asset data; the query itself runs via query_assets ---
    "saved_filters.list_saved_filters": "A user's personal saved Explorer "
                                        "queries (UI state), not asset data.",
    "saved_filters.get_saved_filter": "A user's personal saved Explorer query "
                                      "(UI state), not asset data.",
    # --- Field-registry ops / config (not the field catalog itself, which is
    #     reachable via describe_catalog / get_field_registry) ---
    "field_registry.synonyms_regen_status": "Background synonym-regen job status "
                                            "(ops), not asset data.",
    "field_registry.get_field_descriptions_config": "Field-descriptions LLM "
                                                    "settings (config), not "
                                                    "asset data.",
}


# ===========================================================================
# EXCLUDED operators (SS6.2 operator advertisement). Operators in
# OPERATOR_REGISTRY that intentionally appear in NO field's operator_specs.
# ===========================================================================
EXCLUDED_OPERATORS = {
    # The UI's BOOLEAN operator set (OPERATORS_BY_DATA_TYPE[BOOLEAN] /
    # operators._ORDER_BY_TYPE[BOOLEAN]) is eq / is_null / is_not_null - boolean
    # fields are filtered with `eq true|false`. is_true / is_false are
    # registry-internal (resolvable aliases + wired build_sql/match) but are NOT
    # part of the UI's per-field operator surface, so advertising them per-field
    # would diverge from the UI dropdown (a shared-component change outside this
    # phase). They remain DISCOVERABLE via describe_catalog view='operators' (the
    # full-registry dump) and EXECUTABLE on boolean fields, so excluding them from
    # the per-field-advertisement assertion does not reduce real UI->chat parity.
    "is_true": "Not in the UI's per-field BOOLEAN operator set (UI uses eq); "
               "registry-internal, still discoverable via describe_catalog "
               "view='operators' and executable.",
    "is_false": "Not in the UI's per-field BOOLEAN operator set (UI uses eq); "
                "registry-internal, still discoverable via describe_catalog "
                "view='operators' and executable.",
}


# Bucket taxonomy (the model-facing describe_catalog buckets, SS2.A). The four
# STATIC-source buckets are guaranteed by code (no DB); the four DYNAMIC buckets
# require live catalog data and are exercised in the integration lane.
_STATIC_BUCKETS = ("target", "advanced", "core_enrichment", "virtual")
_DYNAMIC_BUCKETS = ("extended_attribute", "integration_group", "tag", "posture")


# ===========================================================================
# Helpers
# ===========================================================================
def _pure_resolver() -> FieldResolver:
    """A live FieldResolver over an empty-DB catalog: the built-in static / core /
    virtual fields only (no extended_attribute / tag / posture, which need a DB).
    Mirrors the canonical `_resolver()` used across the chat/mcp test suite."""
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


def _all_services_registry_tool_names() -> set:
    """Tool names from a registry built with EVERY backing service present, so the
    service-gated tools (list_rules, get_integration, query_metrics, ...) are all
    registered. Admin role sees every tool (admin-gated included)."""
    kwargs = {name: MagicMock(name=name)
              for name in inspect.signature(build_registry).parameters}
    registry = build_registry(**kwargs)
    return {spec.name for spec in registry.for_role("admin")}


def _api_get_endpoints(app) -> dict:
    """{endpoint_name: path} for every `/api/...` rule that serves GET - the UI
    read surface the contract enumerates (mutations are POST/PUT/PATCH/DELETE and
    drop out; non-/api framework/static/teams pages drop out by prefix)."""
    out = {}
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        if "GET" not in ((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            continue
        out[rule.endpoint] = rule.rule
    return out


def _catalog_entries(resolver) -> list:
    return build_catalog_response(resolver.catalog()).get("entries", [])


def _assert_field_round_trips_and_reaches(entry, resolver) -> None:
    """A single field's two parity guarantees: its encoded_key round-trips
    encode/decode, and a real (advertised) filter on it resolves through the
    query_assets field path without raising - i.e. what the model discovers, it
    can actually query."""
    enc = entry["encoded_key"]
    assert encode(decode(enc)) == enc, (
        f"encoded_key {enc!r} does not round-trip encode(decode(key)) == key; "
        f"the catalog emits a non-canonical key the query path can't honour."
    )
    op_codes = [s["code"] for s in (entry.get("operator_specs") or [])]
    assert op_codes, (
        f"field {enc!r} advertises NO operators - it is unfilterable, so it is "
        f"discoverable via describe_catalog but unusable in query_assets."
    )
    # Use the field's own first advertised operator - the most honest "real
    # filter on this field" - and assert it resolves through _normalize_filters
    # (the exact path query_assets/list_assets_query/aggregate use).
    normalized = _normalize_filters(
        [{"field": enc, "op": op_codes[0], "value": "x"}], resolver=resolver,
    )
    assert normalized and len(normalized) == 1, (
        f"query_assets could not resolve a filter on discoverable field {enc!r}."
    )
    # The field_id the repo will receive must be the SAME catalog field (so the
    # query hits the field the model asked for, not a silently-different one).
    # encode() accepts the model_dump dict directly (no coerce needed).
    assert encode(normalized[0]["field_id"]) == enc, (
        f"filter on {enc!r} normalised to a different field "
        f"{normalized[0]['field_id']!r}."
    )


def _field_reaches(entry, resolver) -> bool:
    """Bool form of the per-field guarantee: True iff the field round-trips AND
    resolves through the query path. Reuses the asserting helper so the two can't
    drift apart; any failure (assertion OR a FieldResolutionError raised by the
    query path for a dead field) means 'does not reach'."""
    try:
        _assert_field_round_trips_and_reaches(entry, resolver)
        return True
    except Exception:
        return False


def _buckets_present(entries) -> set:
    return {
        b for b in _CATALOG_BUCKETS
        if any(_entry_matches(e, bucket=b, needle="", src="") for e in entries)
    }


def _bucket_has_reachable_field(entries, bucket, resolver) -> bool:
    """True iff the bucket has >=1 field that is actually reachable+filterable
    (not merely one that advertises operators) - honest to the assertion's name."""
    return any(
        _entry_matches(e, bucket=bucket, needle="", src="")
        and _field_reaches(e, resolver)
        for e in entries
    )


# ===========================================================================
# 1. Endpoint coverage (default lane - no live DB via blueprint_app)
# ===========================================================================
def test_partition_covers_the_describe_catalog_bucket_taxonomy():
    """The static/dynamic split must exactly cover the model-facing bucket
    vocabulary. If a 9th bucket is ever added to describe_catalog, this fails -
    forcing the new bucket into a lane rather than going silently unchecked."""
    assert set(_STATIC_BUCKETS) | set(_DYNAMIC_BUCKETS) == set(_CATALOG_BUCKETS)
    assert set(_STATIC_BUCKETS).isdisjoint(_DYNAMIC_BUCKETS)


def test_manifest_is_internally_consistent():
    """A manifest entry can't be in both maps, and every mapped tool must exist in
    the (all-services) registry - catches a typo or a removed/renamed tool."""
    overlap = set(ENDPOINT_TO_TOOL) & set(EXCLUDED_ENDPOINTS)
    assert not overlap, f"endpoints both mapped AND excluded: {sorted(overlap)}"

    tool_names = _all_services_registry_tool_names()
    missing = sorted(set(ENDPOINT_TO_TOOL.values()) - tool_names)
    assert not missing, (
        f"ENDPOINT_TO_TOOL points at tool(s) not in the registry: {missing}. "
        f"A tool was renamed/removed - fix the manifest or restore the tool."
    )


def test_every_ui_read_endpoint_maps_to_a_chat_tool(blueprint_app):
    """SS6.2 endpoint coverage: every `/api` GET route is either mapped to a
    covering chat tool or explicitly excluded with a reason. A new read route in
    NEITHER map fails the build (parity drift)."""
    endpoints = _api_get_endpoints(blueprint_app)

    uncovered = sorted(
        f"{ep}  ({endpoints[ep]})"
        for ep in endpoints
        if ep not in ENDPOINT_TO_TOOL and ep not in EXCLUDED_ENDPOINTS
    )
    assert not uncovered, (
        "New UI read endpoint(s) with no chat coverage decision:\n  "
        + "\n  ".join(uncovered)
        + "\n\nAdd each to ENDPOINT_TO_TOOL (the covering chat tool) OR to "
        "EXCLUDED_ENDPOINTS (with a one-line reason). Parity must be a conscious "
        "decision, never a silent gap."
    )


def test_manifest_has_no_stale_endpoint_entries(blueprint_app):
    """Every manifest key must be a CURRENT `/api` GET endpoint, so the manifest
    can't rot as routes are renamed/removed."""
    endpoints = set(_api_get_endpoints(blueprint_app))
    stale = sorted((set(ENDPOINT_TO_TOOL) | set(EXCLUDED_ENDPOINTS)) - endpoints)
    assert not stale, (
        f"Manifest references endpoint(s) that no longer serve GET under /api: "
        f"{stale}. Remove the stale entries."
    )


# ===========================================================================
# 2/3/4. Field reachability + operator advertisement + bucket coverage
#        PURE lane (static catalog, no DB) - gates every PR.
# ===========================================================================
@pytest.fixture(scope="module")
def _pure():
    """(resolver, entries) over the static empty-DB catalog, materialised ONCE for
    all pure-lane tests (the static catalog is deterministic and stateless)."""
    resolver = _pure_resolver()
    return resolver, _catalog_entries(resolver)


def test_static_catalog_fields_round_trip_and_are_reachable(_pure):
    """Every static/core/virtual field round-trips encode/decode AND resolves
    through the query_assets field path."""
    resolver, entries = _pure
    assert entries, "empty static catalog - the built-in fields disappeared."
    for entry in entries:
        _assert_field_round_trips_and_reaches(entry, resolver)


def test_reachability_check_rejects_a_dead_field(_pure):
    """Positive control against vacuity: the reachability mechanism the tests
    above rely on must REJECT a field that isn't in the catalog. A fabricated
    extended-attribute field for a non-existent source must raise through the same
    _normalize_filters path - otherwise the round-trip/reachability assertions
    could pass without proving anything."""
    resolver, _entries = _pure
    dead = "ext:__no_such_source__:__no_such_attr__"
    with pytest.raises(FieldResolutionError):
        _normalize_filters(
            [{"field": dead, "op": "eq", "value": "x"}], resolver=resolver,
        )


def test_every_operator_is_advertised_by_some_field(_pure):
    """SS6.2 operator advertisement: every registry operator (minus the documented
    EXCLUDED_OPERATORS) appears in some field's operator_specs, so the model can
    discover it per-field. The static catalog already spans every filterable data
    type, so the full advertised set is provable without a DB. NOTE: `advertised`
    is the union over all fields, so this guards 'reachable by SOME field' (the
    §6.2 wording); per-data-type advertisement is left to the operator registry's
    own tests."""
    _resolver, entries = _pure
    advertised = {
        s["code"]
        for e in entries
        for s in (e.get("operator_specs") or [])
    }
    registry = set(OPERATOR_REGISTRY)

    # Excluded ops must be real, and genuinely NOT advertised (so the exclusion
    # list self-cleans: if one is later advertised, this reminds us to drop it).
    assert set(EXCLUDED_OPERATORS) <= registry, (
        f"EXCLUDED_OPERATORS lists non-operators: "
        f"{sorted(set(EXCLUDED_OPERATORS) - registry)}"
    )
    wrongly_excluded = sorted(set(EXCLUDED_OPERATORS) & advertised)
    assert not wrongly_excluded, (
        f"Operator(s) {wrongly_excluded} are now advertised by a field - remove "
        f"them from EXCLUDED_OPERATORS."
    )

    unadvertised = sorted((registry - set(EXCLUDED_OPERATORS)) - advertised)
    assert not unadvertised, (
        f"Operator(s) {unadvertised} are in OPERATOR_REGISTRY but advertised by "
        f"NO field's operator_specs - the model can't discover them per-field. "
        f"Wire them into operators._ORDER_BY_TYPE, or document them in "
        f"EXCLUDED_OPERATORS with a reason."
    )


def test_static_buckets_have_a_reachable_filterable_field(_pure):
    """SS6.2 bucket coverage (static buckets): each is present and has a field
    that actually round-trips + resolves (not merely one advertising operators)."""
    resolver, entries = _pure
    present = _buckets_present(entries)
    missing = sorted(set(_STATIC_BUCKETS) - present)
    assert not missing, f"static catalog bucket(s) absent: {missing}"
    for bucket in _STATIC_BUCKETS:
        assert _bucket_has_reachable_field(entries, bucket, resolver), (
            f"bucket {bucket!r} has no reachable, filterable field."
        )


# ===========================================================================
# Integration lane (live DB) - extends reachability + bucket coverage to the
# DYNAMIC catalog (extended_attribute / integration_group / tag / posture) and
# adds the SS6.3 SQL<->SQL equivalence check. Marked `integration` so the pure
# core above still gates every PR.
# ===========================================================================
@pytest.fixture(scope="module")
def _live():
    """(adapter, FieldResolver) over the REAL catalog (live DB). Skips when the DB
    is unreachable, like the rest of the integration suite."""
    import os

    import psycopg2

    from src.database.postgres_adapter import PostgreSQLAdapter
    from src.database.postgres_connection import PostgreSQLConnectionManager

    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set - skipping integration parity lane")
    try:
        psycopg2.connect(url).close()
    except psycopg2.OperationalError as exc:
        pytest.skip(f"cannot connect to database: {exc}")

    adapter = PostgreSQLAdapter(PostgreSQLConnectionManager(database_url=url))
    return adapter, FieldResolver(catalog=FieldCatalog(db_adapter=adapter))


@pytest.mark.integration
def test_live_catalog_fields_round_trip_and_are_reachable(_live):
    """Every field in the FULL live catalog (incl ext/group/tag/posture)
    round-trips and resolves through the query_assets path."""
    _adapter, resolver = _live
    entries = _catalog_entries(resolver)
    assert entries, "empty live catalog"
    for entry in entries:
        _assert_field_round_trips_and_reaches(entry, resolver)


@pytest.mark.integration
def test_dynamic_buckets_are_reachable_and_filterable(_live):
    """SS6.2 bucket coverage (dynamic buckets). Skips if this deployment has no
    dynamic catalog data at all (nothing to validate); otherwise every dynamic
    bucket that IS present must be filterable."""
    _adapter, resolver = _live
    entries = _catalog_entries(resolver)
    present_dynamic = _buckets_present(entries) & set(_DYNAMIC_BUCKETS)
    if not present_dynamic:
        pytest.skip(
            "no extended_attribute/tag/posture fields in this DB - integration "
            "data required to exercise the dynamic buckets."
        )
    for bucket in present_dynamic:
        assert _bucket_has_reachable_field(entries, bucket, resolver), (
            f"dynamic bucket {bucket!r} present but has no filterable field."
        )


@pytest.mark.integration
def test_query_assets_count_matches_rules_compiler(_live):
    """SS6.3 SQL<->SQL equivalence (scaffold): the chat query path
    (AssetRepository._build_filter_clauses, via list_assets_query) and the rules
    engine (AssetQueryCompiler.build_where_clause) must return the SAME asset
    count for the same filter. Both clause-builders delegate to the shared
    OPERATOR_REGISTRY.build_sql, so this does NOT independently re-derive counts —
    it guards drift between the two SQL clause-ASSEMBLY paths (field resolution,
    routing, param handling), not build_sql itself nor a Python/in-memory matcher
    (the SQL<->in-memory boundary is covered by the operator-unification suite).
    Values are drawn from live data so the check is data-agnostic; fields with no
    data are skipped. 'Data Source' is deliberately NOT in the matrix: the repo
    OR-expands it to integration group-member names (a chat-only behaviour the
    compiler lacks), so the two counts legitimately differ there."""
    from src.mcp.tools.assets import get_field_values_query, list_assets_query
    from src.modules.shared.asset_query_compiler import AssetQueryCompiler

    db, resolver = _live
    compiler = AssetQueryCompiler(resolver)

    # A representative scalar/array, eq/contains matrix over static fields.
    matrix = [
        ("Category", "eq"),
        ("OS Normalized", "eq"),
        ("Owner", "contains"),       # STRING_ARRAY -> array contains
    ]
    checked = 0
    for field_name, op in matrix:
        values, _total, err = get_field_values_query(
            db, resolver, field=field_name, limit=1, filters=[],
        )
        if err or not values:
            continue
        value = values[0]["value"]
        # ONE filter, fed to both engines, so the comparison can't silently test
        # two different filters if this is ever edited.
        flt = {"field": field_name, "op": op, "value": value}
        field_id_dict = _normalize_filters([flt], resolver=resolver)[0]["field_id"]

        # chat path (Explorer repo): list_assets_query -> AssetRepository
        _rows, chat_total = list_assets_query(
            db, resolver, filters=[flt], page=1, limit=1,
        )
        # rules path: AssetQueryCompiler -> SELECT COUNT(*)
        clause, params = compiler.build_where_clause(
            [{"field_id": field_id_dict, "operator": op, "value": value}],
            logic="AND",
        )
        assert clause, f"compiler produced no clause for {field_name} {op}"
        count_rows = db.execute_query(
            f"SELECT COUNT(*) AS n FROM assets WHERE {clause}", params,
        )
        rules_total = count_rows[0]["n"]

        assert chat_total == rules_total, (
            f"engine divergence on {field_name} {op} {value!r}: chat path counted "
            f"{chat_total}, rules compiler counted {rules_total}. The chat query "
            f"path and the shared rules engine must agree (SS6.3)."
        )
        checked += 1

    if checked == 0:
        pytest.skip("no live values for the equivalence matrix fields.")
