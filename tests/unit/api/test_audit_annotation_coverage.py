# SPDX-License-Identifier: AGPL-3.0-or-later
"""Audit-annotation coverage guard (#1150, slice 2).

Every mutating (POST/PUT/PATCH/DELETE) ``/api/`` endpoint must either carry
semantic audit coverage in its view — a call to ``AuditLogger.log_*`` (which
annotates the request through the single integration point in
``log_audit_event``) or a direct ``annotate_audit`` call — or sit in the
explicit ALLOWLIST below with a reason.

The guard is self-pruning: an allowlisted endpoint whose view HAS gained
coverage fails the suite until its entry is removed, so the list cannot rot
in either direction. New mutating endpoints must ship with audit coverage —
the HTTP-shaped audit_trail row the middleware writes for every request is
not a substitute for the semantic columns.
"""

import inspect

# endpoint name -> one-line reason it legitimately has no semantic audit event
# in the view. THIS LIST MAY ONLY SHRINK: an entry leaves when its endpoint
# gains coverage (the stale-entry check below enforces it), and new mutating
# endpoints are expected to arrive covered, not allowlisted.
#
# Reason taxonomy:
#   compute-only ... read-only operation over POST (preview/test/query) —
#                    nothing is persisted, so there is no mutation to audit
#   pre-existing gap ... mutates state but emits no audit event today; known
#                    debt the allowlist exists to inventory, not to grow
#   service-layer ... the view delegates to a service that emits the event
ALLOWLIST = {
    # --- auth blueprint: events flow through AuditService.log_auth in the
    # --- service layer, which annotates via AuditLogger — the auth fold of
    # --- #1150 (slice 3) made audit_trail the system of record.
    'auth.login': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.register': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.logout': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.refresh_token': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.request_password_reset': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.verify_password_reset': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.forgot_password_lookup': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.forgot_password_totp': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.create_user': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.update_user': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.disable_user': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.enable_user': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.admin_password_reset': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.force_password_change': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.reset_user_mfa': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.delete_user': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.resend_invite': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.complete_onboarding': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.mfa_setup': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.mfa_verify': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.mfa_disable': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.mfa_challenge': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.mfa_enroll_setup': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.mfa_enroll_verify': 'service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.ldap_test': 'compute-only LDAP connection test; nothing persisted',
    'auth.oidc_login': 'OIDC login initiation (redirect); service-layer coverage: AuditService.log_auth annotates via AuditLogger',
    'auth.oidc_exchange': 'OIDC code exchange; service-layer coverage: AuditService.log_auth annotates via AuditLogger',

    # --- account (self-service): auth-family mutations, unaudited today.
    'account.update_account_profile': 'self-service profile mutation; pre-existing gap',
    'account.change_account_password': 'self-service password change; pre-existing gap',
    'account.account_mfa_totp_setup': 'self-service MFA setup; pre-existing gap',
    'account.account_mfa_totp_verify': 'self-service MFA verify; pre-existing gap',
    'account.update_account_mfa': 'self-service MFA mutation; pre-existing gap',
    'account.remove_account_mfa': 'self-service MFA removal; pre-existing gap',

    # --- teams bot bridge: machine-to-machine endpoints, no admin user context.
    'teams_bot.initiate_link': 'bot bridge link flow; pre-existing gap',
    'teams_bot.confirm_link': 'bot bridge link flow; pre-existing gap',
    'teams_bot.exchange_token': 'bot bridge token exchange; pre-existing gap',
    'teams_bot.upsert_conversation_session': 'bot bridge session upsert; pre-existing gap',
    'teams_bot.delete_conversation_session': 'bot bridge session delete; pre-existing gap',

    # --- tag rules
    'tag_rules.preview_rule': 'compute-only preview; nothing persisted',
    'tag_rules.execute_rule_endpoint': 'single-rule execution; pre-existing gap (execute-all is covered)',

    # --- asset edit
    'asset_edit.reclassify_asset': 'asset reclassify; pre-existing gap',
    'asset_edit.reclassify_bulk': 'bulk reclassify; pre-existing gap',
    'asset_edit.clear_bulk_overrides': 'bulk override clear; pre-existing gap',
    'asset_edit.add_tag_bulk': 'bulk tag apply; pre-existing gap',
    'asset_edit.remove_tag_bulk': 'bulk tag removal; pre-existing gap',
    'asset_edit.add_custom_subcategory': 'custom subcategory create; pre-existing gap',
    'asset_edit.add_tag': 'manual tag apply; pre-existing gap',
    'asset_edit.remove_tag': 'manual tag removal; pre-existing gap',
    'asset_edit.execute_merge': 'asset merge; pre-existing gap',
    'asset_edit.unmerge_asset': 'asset unmerge; pre-existing gap',
    'asset_edit.split': 'asset split; pre-existing gap',
    'asset_edit.set_label_override': 'label override; pre-existing gap',
    'asset_edit.clear_label_override': 'label override clear; pre-existing gap',
    'asset_edit.clear_category_override': 'category override clear; pre-existing gap',

    # --- charts / reports
    'charts.query_charts': 'compute-only chart query over POST; nothing persisted',
    'reports.create_report': 'report CRUD; pre-existing gap',
    'reports.update_report': 'report CRUD; pre-existing gap',
    'reports.delete_report': 'report CRUD; pre-existing gap',
    'reports.clone_report': 'report clone; pre-existing gap',

    # --- health / reference data
    'health.force_update_check': 'compute-only version re-check; cache refresh only',
    'reference_data.check_for_updates': 'compute-only upstream check; nothing persisted',
    'reference_data.apply_from_upstream': 'reference bundle apply; pre-existing gap',
    'reference_data.upload_bundle': 'reference bundle upload; pre-existing gap',

    # --- settings: the view delegates to settings_manager, which emits the
    # --- AuditLogger event one layer down (application_setting update).
    'settings.update_setting': 'service-layer coverage: settings_manager emits the audit event',
    'settings.reset_setting': 'service-layer coverage: settings_manager emits the audit event',
    'settings.send_email_test': 'compute-only test email; nothing persisted',
    'teams_settings.test_connection': 'compute-only connection test; nothing persisted',

    # --- integrations (non-CRUD actions)
    'integrations_crud.test_connection': 'compute-only connection test; nothing persisted',
    'integrations_crud.get_sample_data': 'compute-only sample fetch; nothing persisted',
    'integrations_crud.run_integration': 'manual sync trigger; pre-existing gap',

    # --- schedules / batches / pipeline triggers
    'schedules.update_schedule': 'schedule mutation; pre-existing gap',
    'schedules.enable_schedule': 'schedule mutation; pre-existing gap',
    'schedules.disable_schedule': 'schedule mutation; pre-existing gap',
    'schedules.trigger_now': 'schedule trigger; pre-existing gap',
    'batches.delete_batch': 'batch delete; pre-existing gap',
    'batches.trigger_batch_mapping': 'mapping trigger; pre-existing gap',
    'batches.remap_batch': 'remap trigger; pre-existing gap',
    'asset_generation.trigger_asset_generation': 'pipeline trigger; pre-existing gap',
    'asset_generation.trigger_batch_generation': 'pipeline trigger; pre-existing gap',
    'pipeline.trigger_pipeline': 'pipeline trigger; pre-existing gap',
    'scheduler_monitoring.refresh_statistics': 'materialized-view refresh; pre-existing gap',

    # --- preview cache
    'preview.fetch_preview_data': 'compute-only preview fetch; nothing persisted',
    'preview.preview_filter_count': 'compute-only count; nothing persisted',
    'preview.delete_preview_cache': 'cache clear; pre-existing gap',
    'preview.test_custom_http': 'compute-only HTTP test; nothing persisted',

    # --- integration groups
    'integration_groups.create_group': 'group CRUD; pre-existing gap',
    'integration_groups.update_group': 'group CRUD; pre-existing gap',
    'integration_groups.delete_group': 'group CRUD; pre-existing gap',
    'integration_groups.add_members': 'group membership; pre-existing gap',
    'integration_groups.remove_member': 'group membership; pre-existing gap',

    # --- log management
    'log_management.update_retention_settings': 'retention config mutation; pre-existing gap',
    'log_management.trigger_cleanup': 'cleanup trigger; pre-existing gap',

    # --- alerts / alert rules / alert channels
    'alerts.patch_alert': 'alert ack/status mutation; pre-existing gap',
    'alerts.alertmanager_webhook': 'inbound machine webhook; no admin user context',
    'alert_rules.create_rule': 'alert rule CRUD; pre-existing gap',
    'alert_rules.update_rule': 'alert rule CRUD; pre-existing gap',
    'alert_rules.delete_rule': 'alert rule CRUD; pre-existing gap',
    'alert_rules.toggle_rule': 'alert rule toggle; pre-existing gap',
    'alert_rules.update_anomaly_threshold': 'threshold mutation; pre-existing gap',
    'alert_rules.preview_rule': 'compute-only preview; nothing persisted',
    'alert_channels.create_channel': 'channel CRUD; pre-existing gap',
    'alert_channels.update_channel': 'channel CRUD; pre-existing gap',
    'alert_channels.delete_channel': 'channel CRUD; pre-existing gap',
    'alert_channels.test_channel': 'compute-only delivery test; nothing persisted',
    'alert_channels.set_routing': 'routing mutation; pre-existing gap',

    # --- metrics forwarding
    'metrics_forwarding.set_config': 'forwarding config mutation; pre-existing gap',
    'metrics_forwarding.test_connection': 'compute-only connection test; nothing persisted',

    # --- enrichment
    'enrichment.preview_dataset': 'compute-only preview; nothing persisted',

    # --- admin embedding
    'admin_embedding.refresh_embeddings': 'embedding refresh trigger; pre-existing gap',

    # --- field registry
    'field_registry.update_field': 'field registry mutation; pre-existing gap',
    'field_registry.regenerate_field': 'field regeneration trigger; pre-existing gap',
    'field_registry.regenerate_synonyms': 'synonym regeneration trigger; pre-existing gap',
    'field_registry.set_field_descriptions_enabled': 'descriptions toggle; pre-existing gap',

    # --- API keys
    'api_keys.create_api_key': 'API-key lifecycle; pre-existing gap',
    'api_keys.update_api_key': 'API-key lifecycle; pre-existing gap',
    'api_keys.delete_api_key': 'API-key lifecycle; pre-existing gap',

    # --- unified fields
    'unified_fields.create_unified_field': 'unified field CRUD; pre-existing gap',
    'unified_fields.update_unified_field': 'unified field CRUD; pre-existing gap',
    'unified_fields.delete_unified_field': 'unified field CRUD; pre-existing gap',
    'unified_fields.preview_unified_field': 'compute-only preview; nothing persisted',

    # --- posture rules: the two previews are compute-only by design; every
    # --- real mutation in this blueprint is covered (slice 2).
    'posture_rules.preview_output': 'compute-only preview; nothing persisted',
    'posture_rules.preview_sub_rule': 'compute-only preview; nothing persisted',

    # --- chat
    'chat.create_session': 'chat session CRUD; pre-existing gap',
    'chat.rename_session': 'chat session rename; pre-existing gap',
    'chat.autotitle_session': 'chat session autotitle; pre-existing gap',
    'chat.delete_session': 'chat session delete; pre-existing gap',
    'chat.feedback': 'chat feedback submit; pre-existing gap',
    'chat.admin_patch_config': 'chat config mutation; pre-existing gap',
    'chat.admin_test_config': 'compute-only config test; nothing persisted',
    'chat.stream_turn_route': 'chat turn (SSE stream); pre-existing gap',

    # --- saved filters / merge denylist
    'saved_filters.create_saved_filter': 'saved filter CRUD; pre-existing gap',
    'saved_filters.update_saved_filter': 'saved filter CRUD; pre-existing gap',
    'saved_filters.delete_saved_filter': 'saved filter CRUD; pre-existing gap',
    'merge_denylist.add_denylist': 'denylist mutation; pre-existing gap',
    'merge_denylist.delete_denylist': 'denylist mutation; pre-existing gap',
}

_MUTATING_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
_COVERAGE_MARKERS = ('AuditLogger.log_', 'annotate_audit')


def _mutating_views(app):
    """Yield (endpoint, view_func) for every /api/ rule with a mutating method."""
    seen = set()
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith('/api/'):
            continue
        if not (rule.methods & _MUTATING_METHODS):
            continue
        if rule.endpoint in seen:
            continue
        seen.add(rule.endpoint)
        yield rule.endpoint, app.view_functions[rule.endpoint]


def _is_covered(view_func):
    """True when the view's own source references the audit machinery.

    inspect.getsource unwraps the @wraps decorator chain (jwt_required,
    require_admin, handle_errors, validate_request, response_wrapper all use
    functools.wraps) down to the real handler body.
    """
    try:
        source = inspect.getsource(view_func)
    except (OSError, TypeError):
        return False
    return any(marker in source for marker in _COVERAGE_MARKERS)


def _evaluate(app, allowlist):
    """Return (uncovered, stale) endpoint lists for an app against an allowlist."""
    uncovered, stale = [], []
    for endpoint, view in _mutating_views(app):
        covered = _is_covered(view)
        if endpoint in allowlist:
            if covered:
                stale.append(endpoint)
        elif not covered:
            uncovered.append(endpoint)
    return uncovered, stale


def test_mutating_endpoints_are_audit_covered_or_allowlisted(blueprint_app):
    uncovered, stale = _evaluate(blueprint_app, ALLOWLIST)

    assert not stale, (
        'These endpoints have gained audit coverage — remove their ALLOWLIST '
        f'entries (the list may only shrink): {sorted(stale)}'
    )
    assert not uncovered, (
        'Mutating /api endpoints with no AuditLogger.log_* / annotate_audit '
        f'in the view: {sorted(uncovered)} — add audit coverage; new '
        'endpoints may NOT be allowlisted.'
    )


def test_guard_fails_on_synthetic_unannotated_mutation():
    """Proof the guard bites in both directions: an un-annotated POST view is
    flagged, an otherwise-identical covered view is not, a GET is out of
    scope, and an allowlisted-but-covered endpoint is reported stale (the
    self-pruning direction)."""
    from flask import Flask

    from src.api.utils.audit_logger import AuditLogger  # noqa: F401  (marker text in the covered view)

    app = Flask(__name__)

    @app.route('/api/probe-uncovered', methods=['POST'])
    def probe_uncovered():
        return {'ok': True}

    @app.route('/api/probe-covered', methods=['POST'])
    def probe_covered():
        # Never executed — the guard inspects source, it does not run views.
        AuditLogger.log_data_operation(
            resource='x', resource_id='1', action='update',
            user_id='u', username='u',
        )
        return {'ok': True}

    @app.route('/api/probe-read', methods=['GET'])
    def probe_read():
        return {'ok': True}

    uncovered, stale = _evaluate(app, {'probe_covered': 'seeded entry'})
    assert uncovered == ['probe_uncovered']
    assert stale == ['probe_covered']
