# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Application Settings Schema

Defines hot-reloadable application settings stored in database.
Does NOT include logging/infrastructure settings (those use .env).

Architecture Decision:
- Infrastructure Settings (Environment Variables):
  - Logging configuration, database settings, cache config
  - Requires app restart to change
  - Set in .env file

- Application Settings (SettingsManager):
  - Asset generation, logging, security, retention rules
  - Hot-reloadable without restart
  - Stored in database

This module defines ONLY application-level settings.
"""

from typing import Dict, Any, Literal

from src.modules.monitoring.durations import is_writable_duration
from src.modules.shared.confidence_thresholds import (  # noqa: F401 — re-export
    DEFAULT_CONFIDENCE_THRESHOLDS,
    get_confidence_thresholds,
)


def monitoring_dashboards_enabled(settings_manager=None, env_enabled: bool = False) -> bool:
    """Resolve whether the Prometheus dashboards / `/api/monitoring` proxy are active.

    Reads the runtime `monitoring.dashboards` setting (auto|enabled|disabled):
    - 'auto' (default) follows the deploy-time MONITORING_ENABLED env flag, so a
      deployment that never touches the UI toggle keeps its existing behavior.
    - 'enabled' / 'disabled' are explicit UI overrides applied without a restart.

    Falls back to `env_enabled` when no settings manager is available (outside a
    Flask/MCP context) or on any read error, so this path never crashes a caller.
    """
    if settings_manager is None:
        return env_enabled
    try:
        choice = settings_manager.get('monitoring', 'dashboards')
    except Exception:
        return env_enabled
    if choice == 'enabled':
        return True
    if choice == 'disabled':
        return False
    return env_enabled  # 'auto' (default) or any unexpected value


# Type definitions for better type safety
SettingType = Literal['boolean', 'integer', 'string', 'float', 'json']


class SettingDefinition:
    """Definition for a single setting with validation rules."""

    def __init__(
        self,
        setting_type: SettingType,
        default: Any,
        description: str,
        min_value: int = None,
        max_value: int = None,
        options: list = None,
        value_validator=None,
        section: str = None,
        hidden: bool = False,
    ):
        self.type = setting_type
        self.default = default
        self.description = description
        self.min = min_value
        self.max = max_value
        self.options = options
        self.value_validator = value_validator
        self.section = section
        # When True, the setting stays in the schema (so settings_manager.set()
        # still validates writes) but the generic schema-driven Settings UI
        # skips rendering it — for keys a bespoke tab already owns.
        self.hidden = hidden

    def validate(self, value: Any) -> bool:
        """
        Validate a value against this setting's rules.

        Args:
            value: Value to validate

        Returns:
            bool: True if valid, False otherwise
        """
        # Type validation
        if self.type == 'boolean' and not isinstance(value, bool):
            return False
        elif self.type == 'integer' and not isinstance(value, int):
            return False
        elif self.type == 'string' and not isinstance(value, str):
            return False
        elif self.type == 'float' and not isinstance(value, (int, float)):
            return False
        elif self.type == 'json' and not isinstance(value, dict):
            return False

        # Range validation for numeric types
        if self.type in ('integer', 'float'):
            if self.min is not None and value < self.min:
                return False
            if self.max is not None and value > self.max:
                return False

        # Options validation
        if self.options is not None and value not in self.options:
            return False

        # Custom validator
        if self.value_validator is not None:
            return self.value_validator(value)

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'type': self.type,
            'default': self.default,
            'description': self.description,
            'min': self.min,
            'max': self.max,
            'options': self.options,
            'hidden': self.hidden,
        }


# =============================================================================
# Validators
# =============================================================================

def _validate_calibration_curve(v):
    """Validate calibration curve: dict, keys/values in 0-1, monotonic."""
    if not isinstance(v, dict) or len(v) < 2:
        return False
    try:
        pairs = sorted((float(k), float(val)) for k, val in v.items())
    except (ValueError, TypeError):
        return False
    for key, val in pairs:
        if not (0.0 <= key <= 1.0 and 0.0 <= val <= 1.0):
            return False
    # Monotonically non-decreasing
    for i in range(len(pairs) - 1):
        if pairs[i][1] > pairs[i + 1][1]:
            return False
    return True


# =============================================================================
# Application Settings Schema
# =============================================================================

APPLICATION_SETTINGS: Dict[str, Dict[str, SettingDefinition]] = {

    # System - platform-level toggles
    'system': {
        'enable_update_check': SettingDefinition(
            setting_type='boolean',
            default=False,
            description=(
                'Allow a daily check against the public release feed for a '
                'newer AssetAtlas version. Off by default; makes no outbound '
                'network call while disabled (safe for air-gapped installs).'
            )
        ),
    },

    # Monitoring dashboards — runtime on/off for the Prometheus dashboards viewer
    # and the /api/monitoring proxy. `hidden` so the generic schema-driven Settings
    # UI skips it; the bespoke Settings -> Monitoring -> Dashboards tab owns the
    # toggle. Resolved via monitoring_dashboards_enabled() above.
    'monitoring': {
        'dashboards': SettingDefinition(
            setting_type='string',
            default='auto',
            options=['auto', 'enabled', 'disabled'],
            description=(
                'Serve the Prometheus dashboards and the /api/monitoring proxy. '
                '"auto" follows the deploy-time MONITORING_ENABLED flag; '
                '"enabled"/"disabled" override it from the UI without a restart. '
                'Requires the monitoring stack (Prometheus + exporters) deployed; '
                'enabling without it shows a connection error, not data.'
            ),
            hidden=True,
        ),
        'retention': SettingDefinition(
            setting_type='string',
            default='',  # empty = "not overridden; use the deployed value"
            description=(
                'Desired Prometheus metrics retention (e.g. 30d). Empty uses the '
                'deployed value. Applied on the host with `assetatlas-cli '
                'apply-retention` (restarts Prometheus).'
            ),
            value_validator=lambda v: v == '' or is_writable_duration(v),
            hidden=True,  # the bespoke Monitoring tab owns it, like dashboards
        ),
    },

    # Asset Generation - Business logic configuration
    'asset_generation': {
        'pipeline_schedule_enabled': SettingDefinition(
            setting_type='boolean',
            default=False,
            description='Enable automatic scheduled pipeline runs'
        ),
        'pipeline_schedule_interval_hours': SettingDefinition(
            setting_type='integer',
            default=24,
            min_value=1,
            max_value=168,
            description='Hours between automatic pipeline runs (1-168)'
        ),
        'pipeline_schedule_mode': SettingDefinition(
            setting_type='string',
            default='interval',
            options=['interval', 'cron'],
            description='Schedule mode: interval (every N hours) or cron (cron expression)'
        ),
        'pipeline_schedule_cron': SettingDefinition(
            setting_type='string',
            default='0 0 * * *',
            description='Cron expression for pipeline schedule (when mode is cron)'
        ),
        'pipeline_schedule_timezone': SettingDefinition(
            setting_type='string',
            default='UTC',
            description='Timezone for cron schedule (e.g. America/New_York)'
        ),
        'pipeline_max_expected_duration_minutes': SettingDefinition(
            setting_type='integer',
            default=240,
            min_value=5,
            max_value=1440,
            description=(
                'Max expected duration (minutes) a pipeline run may stay in running '
                'or pending status before the pipeline watchdog fires a '
                'pipeline-stuck alert (5-1440 minutes, default 240).'
            )
        ),
        'pipeline_stale_grace_minutes': SettingDefinition(
            setting_type='integer',
            default=60,
            min_value=5,
            max_value=10080,
            description=(
                'Grace (minutes) allowed after a scheduled pipeline run was due '
                'before the pipeline watchdog fires a pipeline-stale alert. Must '
                'cover a normal run start plus its duration, or a healthy run that '
                'is merely slow to begin looks overdue (5-10080 minutes, '
                'default 60).'
            )
        ),
        'mapping_expiration_days': SettingDefinition(
            setting_type='integer',
            default=30,
            min_value=7,
            max_value=365,
            description='Days of inactivity before component mappings expire (7-365)'
        ),
        'removal_grace_period_days': SettingDefinition(
            setting_type='integer',
            default=30,
            min_value=0,
            max_value=365,
            description=(
                'Days an asset absent from all inputs is kept (carried forward, '
                'silent) before it is hard-removed. 0 = remove immediately '
                '(legacy). Effective value is capped by mapping_expiration_days. '
                '(0-365, default 30)'
            )
        ),
        'source_reliability_scores': SettingDefinition(
            setting_type='json',
            default={
                'endpoint security': 0.95,
                'cloud platform': 0.90,
                'mobile device management': 0.85,
                'network configuration management': 0.80,
                'network traffic': 0.75,
                'identity and access management': 0.75,
                'vulnerability management': 0.70,
                'observability platform': 0.70,
                'telemetry derived': 0.70,
                'cmdb': 0.65,
                'asset discovery': 0.60,
                'other': 0.50,
            },
            description=(
                'Reliability scores per data source category'
                ' (0.0-1.0). Higher = more trusted.'
            ),
            value_validator=lambda v: (
                isinstance(v, dict)
                and all(
                    isinstance(s, (int, float))
                    and 0.0 <= s <= 1.0
                    for s in v.values()
                )
            ),
        ),
        'calibration_curve': SettingDefinition(
            setting_type='json',
            default={
                '0.0': 0.05,
                '0.2': 0.15,
                '0.3': 0.30,
                '0.4': 0.45,
                '0.5': 0.60,
                '0.6': 0.72,
                '0.7': 0.82,
                '0.8': 0.90,
                '0.9': 0.95,
                '1.0': 1.00,
            },
            description=(
                'Confidence calibration curve. Maps raw score buckets'
                ' (0.0-1.0) to calibrated output values.'
                ' Stretches the compressed multiplicative range'
                ' into a more intuitive scale.'
            ),
            value_validator=_validate_calibration_curve,
        ),
        'confidence_threshold_medium': SettingDefinition(
            setting_type='float',
            default=0.45,
            min_value=0.0,
            max_value=1.0,
            description='Confidence scores below this are Low, at or above are Medium'
        ),
        'confidence_threshold_high': SettingDefinition(
            setting_type='float',
            default=0.70,
            min_value=0.0,
            max_value=1.0,
            description='Confidence scores at or above this are High'
        ),
        'enable_knowledge_base': SettingDefinition(
            setting_type='boolean',
            default=True,
            description='Enable Tier 2 device knowledge base lookup for unresolved assets'
        ),
        'rule_confidence_threshold': SettingDefinition(
            setting_type='float',
            default=0.85,
            min_value=0.0,
            max_value=1.0,
            description='Rule predictions below this confidence are passed to lower tiers (0.0-1.0)'
        ),
        'ml_confidence_threshold': SettingDefinition(
            setting_type='float',
            default=0.70,
            min_value=0.0,
            max_value=1.0,
            description='Minimum CatBoost confidence to accept a prediction (0.0-1.0)'
        ),
        'enable_ml_predictions': SettingDefinition(
            setting_type='boolean',
            default=False,
            description='Enable Tier 3 CatBoost ML prediction for unresolved assets'
        ),
    },

    # Embedding / semantic search — field-description generation.
    'embedding': {
        'llm_descriptions_enabled': SettingDefinition(
            setting_type='boolean',
            default=True,
            description=(
                'Automatically generate field descriptions and synonyms with the '
                'configured chatbot LLM during pipeline runs. On by default, but '
                'only takes effect once a chat LLM provider is enabled (with no '
                'usable provider the generator no-ops and new fields get basic '
                'fallback descriptions — semantic search still works). Turn off '
                'to opt out even when a provider is available.'
            ),
            section='Field Descriptions',
        ),
    },

    # Security - Authentication and session configuration
    'security': {
        'session_timeout_minutes': SettingDefinition(
            setting_type='integer',
            default=60,
            min_value=5,
            max_value=1440,
            description='Session inactivity timeout in minutes (5 min to 24 hours)'
        ),
        'public_base_url': SettingDefinition(
            setting_type='string',
            default='',
            description=(
                'HTTPS URL where users reach AssetAtlas '
                '(e.g., https://assetatlas.example.com). Used for email '
                'links, the Teams app, and the OIDC redirect URI. Empty '
                'falls back to the ASSETATLAS_PUBLIC_BASE_URL env var.'
            ),
        ),
    },

    # Auth - Password policy and other auth knobs surfaced in the Security UI.
    # Mirrors rows seeded by migrations 086 and 101. Only keys exposed to the
    # Settings UI need to be listed here; settings_manager.set() rejects keys
    # that aren't in the schema, so adding more rows requires a matching entry.
    'auth': {
        'password_expiration_days': SettingDefinition(
            setting_type='integer',
            default=180,
            min_value=0,
            max_value=3650,
            description=(
                'Number of days a local-account password remains valid before '
                'the user is required to change it on next login. 0 = never '
                'expires (recommended per NIST 800-63B). Has no effect on '
                'LDAP or OIDC users.'
            ),
        ),
    },

    # Logging - Hot-reloadable logging configuration
    # NOTE: Some logging settings (LOG_LEVEL, SQL query logging, audit logging) remain in
    # environment variables due to architectural constraints (circular dependencies with DB pool,
    # thread safety concerns, or compliance requirements).
    'logging': {
        'enable_request_logging': SettingDefinition(
            setting_type='boolean',
            default=True,
            description='Log HTTP request start/completion with timing and user context'
        ),
        'enable_performance_tracking': SettingDefinition(
            setting_type='boolean',
            default=True,
            description='Track request latency percentiles (P50, P95, P99) and performance metrics'
        ),
        'health_check_log_level': SettingDefinition(
            setting_type='string',
            default='INFO',
            options=['DEBUG', 'INFO', 'WARNING'],
            description=(
                'Level at which requests to the health check endpoints are logged. '
                'INFO keeps probe traffic visible in the request log; DEBUG puts it '
                'below the usual INFO threshold so it does not appear at all; '
                'WARNING records every probe, but at warning severity. Slow or '
                'failing probes are always logged at WARNING regardless of this setting.'
            )
        ),
        'slow_request_threshold_ms': SettingDefinition(
            setting_type='integer',
            default=1000,
            min_value=100,
            max_value=10000,
            description='Threshold in milliseconds for logging slow requests as warnings'
        )
    },

    # Alerting - Alert delivery framework (#917)
    'alerting': {
        'ALERT_DELIVERY_ENABLED': SettingDefinition(
            setting_type='boolean',
            default=True,
            description='Master switch for outbound alert delivery',
            section='Alerting',
        ),
        'ALERT_DELIVERY_MAX_ATTEMPTS': SettingDefinition(
            setting_type='integer',
            default=5,
            min_value=1,
            max_value=20,
            description='Max send attempts per delivery before it is marked failed',
            section='Alerting',
        ),
        'ALERT_DELIVERY_TIMEOUT_SECONDS': SettingDefinition(
            setting_type='integer',
            default=10,
            min_value=1,
            max_value=120,
            description='Per-attempt send timeout in seconds',
            section='Alerting',
        ),
        'ALERT_DELIVERY_DRAIN_INTERVAL_SECONDS': SettingDefinition(
            setting_type='integer',
            default=25,
            min_value=5,
            max_value=600,
            description='How often the scheduler worker drains pending deliveries',
            section='Alerting',
        ),
        # Both read inline by the 'data-source-stale' rule's SQL (migration 140), not
        # by Python — the query COALESCEs to the same defaults if a row is missing.
        # They drive the rule's two independent branches: past-due for its own
        # scheduled run, and a schedule-blind sync-age backstop.
        'DATA_SOURCE_STALE_GRACE_MINUTES': SettingDefinition(
            setting_type='integer',
            default=60,
            min_value=5,
            max_value=10080,
            description='Minutes an integration may be past its own scheduled sync '
                        'time before the Data Source Stale alert fires',
            section='Alerting',
        ),
        'DATA_SOURCE_MAX_SYNC_AGE_HOURS': SettingDefinition(
            setting_type='integer',
            default=48,
            min_value=1,
            max_value=8760,
            description='Hours an integration may go without any sync at all before '
                        'the Data Source Stale alert fires, regardless of its schedule',
            section='Alerting',
        ),
    },

    # Retention - Data retention policies
    'retention': {
        'BATCH_RETENTION_DAYS': SettingDefinition(
            setting_type='integer',
            default=7,
            min_value=1,
            max_value=365,
            description='Keep batches from the last N days',
            section='Ingestion Data',
        ),
        'BATCH_RETENTION_COUNT': SettingDefinition(
            setting_type='integer',
            default=5,
            min_value=1,
            max_value=100,
            description='Keep latest N batches per integration (regardless of age)',
            section='Ingestion Data',
        ),
        'EXCLUDED_RECORDS_RETENTION_DAYS': SettingDefinition(
            setting_type='integer',
            default=90,
            min_value=7,
            max_value=730,
            description='Excluded records retention in days',
            section='Pipeline Data',
        ),
        'ASSET_CHRONICLE_RETENTION_DAYS': SettingDefinition(
            setting_type='integer',
            default=90,
            min_value=7,
            max_value=730,
            description='Asset change history retention in days',
            section='Pipeline Data',
        ),
        'ASSETS_HISTORY_RETENTION_DAYS': SettingDefinition(
            setting_type='integer',
            default=90,
            min_value=7,
            max_value=730,
            description='Keep asset history snapshots from the last N days',
            section='Pipeline Data',
        ),
        'ASSETS_HISTORY_RETENTION_COUNT': SettingDefinition(
            setting_type='integer',
            default=5,
            min_value=1,
            max_value=100,
            description='Keep latest N asset history snapshots (regardless of age)',
            section='Pipeline Data',
        ),
        'ASSET_PROVIDERS_HISTORY_RETENTION_DAYS': SettingDefinition(
            setting_type='integer',
            default=90,
            min_value=7,
            max_value=730,
            description='Keep asset providers history snapshots from the last N days',
            section='Pipeline Data',
        ),
        'ASSET_PROVIDERS_HISTORY_RETENTION_COUNT': SettingDefinition(
            setting_type='integer',
            default=5,
            min_value=1,
            max_value=100,
            description='Keep latest N asset providers history snapshots (regardless of age)',
            section='Pipeline Data',
        ),
        'ALERT_EVENTS_RETENTION_DAYS': SettingDefinition(
            setting_type='integer',
            default=90,
            min_value=7,
            max_value=730,
            description='Keep alert firing-history rows from the last N days',
            section='Pipeline Data',
        ),
        'ALERT_EVENTS_RETENTION_COUNT': SettingDefinition(
            setting_type='integer',
            default=30,
            min_value=1,
            max_value=100,
            description='Keep alert firing-history for the latest N pipeline runs (regardless of age)',
            section='Pipeline Data',
        ),
        'ALERT_DELIVERIES_RETENTION_DAYS': SettingDefinition(
            setting_type='integer',
            default=30,
            min_value=1,
            max_value=730,
            description='Keep alert delivery/audit rows from the last N days',
            section='Pipeline Data',
        ),
        'AUDIT_TRAIL_RETENTION_DAYS': SettingDefinition(
            setting_type='integer',
            default=90,
            min_value=7,
            max_value=730,
            description='Audit trail retention in days',
            section='Audit & Auth',
        ),
        'AUDIT_TRAIL_LOG_READS': SettingDefinition(
            setting_type='boolean',
            default=False,
            description='Log GET requests in the audit trail',
            section='Audit & Auth',
        ),
    },

    # Chat / LLM — per-provider config rows live in chat_providers; this
    # category just tracks which one is active. UI is the only source of
    # truth for the provider rows themselves (see issue #636).
    'chat': {
        'active_provider': SettingDefinition(
            setting_type='string',
            default='anthropic',
            # 'none' = every provider disabled (chat turned off from the
            # provider grid); see ChatProvidersService.get_active_provider.
            options=['anthropic', 'openai', 'openai_compat', 'aws_bedrock',
                     'foundry', 'none'],
            description='Active chat LLM provider.',
            section='Provider',
            # Owned by the bespoke provider Configuration tab (via
            # /api/chat/config); hidden from the generic schema-driven renderer
            # so the Behavior tab doesn't show a duplicate control.
            hidden=True,
        ),
        'tool_routing_enabled': SettingDefinition(
            setting_type='boolean',
            default=False,
            description=(
                'Advertise only a core tool set + question-relevant tools to '
                'the chat LLM (with find_tools recovery), instead of all '
                'tools. Reduces input tokens per turn.'
            ),
        ),
        'tool_routing_top_k': SettingDefinition(
            setting_type='integer',
            default=5,
            min_value=1,
            max_value=20,
            description=(
                'How many question-relevant gated tools to advertise per turn '
                'when tool_routing_enabled is on.'
            ),
        ),
    },

    # LDAP - Directory authentication. Defaults must mirror migration 088.
    'ldap': {
        'enabled': SettingDefinition(
            setting_type='boolean',
            default=False,
            description='Master toggle for LDAP authentication.',
            # The LDAP tab promotes this into a master switch above the
            # settings card (#1149). Hidden so the generic renderer stops
            # drawing it as row one of fifteen and it isn't offered twice —
            # it stays in the schema so settings_manager.set() still
            # validates writes to it.
            hidden=True,
        ),
        'server_url': SettingDefinition(
            setting_type='string',
            default='',
            description='LDAP server URL (ldap://host:389 or ldaps://host:636).'
        ),
        'use_ssl': SettingDefinition(
            setting_type='boolean',
            default=True,
            description='Use LDAPS (TLS). Disabling means cleartext bind credentials over the wire.'
        ),
        'verify_cert': SettingDefinition(
            setting_type='boolean',
            default=True,
            description='Validate server TLS certificate. Disable only for self-signed dev environments.'
        ),
        'connect_timeout_seconds': SettingDefinition(
            setting_type='integer',
            default=10,
            min_value=1,
            max_value=60,
            description='LDAP connection + bind timeout in seconds.'
        ),
        'bind_dn': SettingDefinition(
            setting_type='string',
            default='',
            description='Service account DN used to search for users (e.g., CN=svc_assetatlas,OU=Service Accounts,DC=corp,DC=com).'
        ),
        'bind_password': SettingDefinition(
            setting_type='string',
            default='',
            description='Service account password; stored encrypted at rest via Fernet helper.'
        ),
        'base_dn': SettingDefinition(
            setting_type='string',
            default='',
            description='Base DN for user search (e.g., DC=corp,DC=com).'
        ),
        'user_search_filter': SettingDefinition(
            setting_type='string',
            default='(&(objectClass=person)(|(sAMAccountName={username})(mail={username})(userPrincipalName={username})))',
            description='LDAP filter to find a user by their submitted username. The literal "{username}" is replaced with the safely-escaped input.'
        ),
        'attribute_mail': SettingDefinition(
            setting_type='string',
            default='mail',
            description="LDAP attribute that holds the user's email address."
        ),
        'attribute_full_name': SettingDefinition(
            setting_type='string',
            default='displayName',
            description="LDAP attribute that holds the user's display name (cn is a common fallback)."
        ),
        'attribute_groups': SettingDefinition(
            setting_type='string',
            default='memberOf',
            description='LDAP attribute listing the groups the user belongs to (multi-valued, full DN per value).'
        ),
        'group_to_role_mapping': SettingDefinition(
            setting_type='json',
            default={},
            description='JSON object mapping LDAP group DN (case-insensitive) to AssetAtlas role. Example: {"CN=AssetAtlas Admins,OU=Groups,DC=corp,DC=com": "admin"}. Roles: user|admin|super_admin.'
        ),
        'default_role': SettingDefinition(
            setting_type='string',
            default='user',
            options=['user', 'admin', 'super_admin'],
            description='Role assigned when no group in group_to_role_mapping matches the user.'
        ),
        'allow_local_fallback': SettingDefinition(
            setting_type='boolean',
            default=True,
            description='When LDAP rejects credentials, fall through to local password verification. Disable to enforce LDAP-only logins.'
        ),
    },

    # OIDC - Single sign-on. Defaults must mirror migrations 089, 091 and 138.
    'oidc': {
        'enabled': SettingDefinition(
            setting_type='boolean',
            default=False,
            description='Master toggle for OIDC SSO authentication.',
            # Same as ldap.enabled — the OIDC tab owns this control (#1149).
            hidden=True,
        ),
        'provider_name': SettingDefinition(
            setting_type='string',
            default='SSO',
            description='Display label on the LoginPage button (e.g., "Microsoft Entra", "Google", "SSO").'
        ),
        'discovery_url': SettingDefinition(
            setting_type='string',
            default='',
            description='OIDC discovery document URL (e.g., https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration).'
        ),
        'client_id': SettingDefinition(
            setting_type='string',
            default='',
            description='OIDC application (client) ID issued by the identity provider.'
        ),
        'client_secret': SettingDefinition(
            setting_type='string',
            default='',
            description='OIDC client secret; stored encrypted at rest via Fernet helper.'
        ),
        'redirect_uri': SettingDefinition(
            setting_type='string',
            default='',
            description='Public callback URL registered in the IdP (e.g., https://app.example.com/api/auth/oidc/callback). MUST match what is configured in the IdP exactly.'
        ),
        'frontend_complete_url': SettingDefinition(
            setting_type='string',
            default='/oidc-complete',
            description='Frontend route the callback redirects to with the one-time exchange code (relative path).'
        ),
        'scopes': SettingDefinition(
            setting_type='string',
            default='openid email profile',
            description='Space-separated OIDC scopes to request. Add a groups scope if your provider exposes group claims (e.g., "openid email profile groups" for some IdPs, or no extra scope for Entra with groups in the token).'
        ),
        'claim_mail': SettingDefinition(
            setting_type='string',
            default='email',
            description='ID token claim that holds the user\'s email address (Entra: "email" or "preferred_username").'
        ),
        'claim_name': SettingDefinition(
            setting_type='string',
            default='name',
            description="ID token claim that holds the user's display name."
        ),
        'claim_groups': SettingDefinition(
            setting_type='string',
            default='groups',
            description="ID token claim that holds the user's groups (multi-valued; values typically full DNs or group object IDs depending on IdP)."
        ),
        'group_to_role_mapping': SettingDefinition(
            setting_type='json',
            default={},
            description='JSON object mapping group claim values (case-insensitive) to AssetAtlas roles. Example: {"00000000-0000-0000-0000-000000000001": "admin"}. Roles: user|admin|super_admin.'
        ),
        'default_role': SettingDefinition(
            setting_type='string',
            default='user',
            options=['user', 'admin', 'super_admin'],
            description='Role assigned when no group in group_to_role_mapping matches the user.'
        ),
        'allow_local_fallback': SettingDefinition(
            setting_type='boolean',
            default=True,
            description='Continue to allow local password authentication on the LoginPage. Disable to require SSO for everyone.'
        ),
        'auto_create_users': SettingDefinition(
            setting_type='boolean',
            default=False,
            description='Provision local user records on first successful SSO sign-in. Disable to require admins to pre-create users by email.'
        ),
        'allowed_email_domains': SettingDefinition(
            setting_type='string',
            default='',
            description='Comma-separated list of email domains permitted to auto-provision a local account via OIDC (e.g., "example.com,corp.io"). Empty = all domains allowed. Only consulted when oidc.auto_create_users = true.'
        ),
        'http_timeout_seconds': SettingDefinition(
            setting_type='integer',
            default=10,
            min_value=1,
            max_value=60,
            description='HTTP timeout for outbound calls to the IdP (discovery, token exchange, JWKS).'
        ),
        'post_logout_redirect_uri': SettingDefinition(
            setting_type='string',
            default='',
            description='Optional public URL the IdP redirects to after RP-initiated logout (e.g., https://app.example.com/login). MUST be registered in the IdP if set. Empty = the IdP shows its own "signed out" page; the SSO cookie is still terminated.'
        ),
    },

    # Teams - Microsoft Teams chatbot bridge.
    # Defaults must mirror migration 096.
    'teams': {
        'enabled': SettingDefinition(
            setting_type='boolean',
            default=False,
            description='Master toggle for the Microsoft Teams chatbot bridge.',
            # Same as ldap.enabled / oidc.enabled — the bespoke ChatbotTeamsTab
            # owns this control (#1227). Nothing renders CategorySettings for
            # 'teams' today, so this marks the contract rather than fixing a
            # live duplicate: without it, the first tab pointed at the category
            # offers the same flag twice through two different controls.
            hidden=True,
        ),
        'bot_app_id': SettingDefinition(
            setting_type='string',
            default='',
            description='Entra application (client) ID of the Azure Bot resource (UUID).'
        ),
        'bot_app_password': SettingDefinition(
            setting_type='string',
            default='',
            description='Azure Bot client secret; stored encrypted at rest via Fernet helper.'
        ),
        'bot_app_tenant_id': SettingDefinition(
            setting_type='string',
            default='',
            description='Microsoft Entra tenant ID (UUID) the Azure Bot is pinned to.'
        ),
        'messaging_endpoint': SettingDefinition(
            setting_type='string',
            default='',
            description='Operator-supplied messaging endpoint URL (HTTPS). Empty = computed from security.public_base_url.'
        ),
    },

    # Email - SMTP outbound email configuration (see issue #489)
    'email': {
        'enabled': SettingDefinition(
            setting_type='boolean',
            default=False,
            description='Master toggle. When false, all outbound email sends are short-circuited.'
        ),
        'smtp_host': SettingDefinition(
            setting_type='string',
            default='',
            description='SMTP relay hostname (e.g. smtp.office365.com).'
        ),
        'smtp_port': SettingDefinition(
            setting_type='integer',
            default=587,
            min_value=1,
            max_value=65535,
            description='SMTP relay port (587 for STARTTLS, 465 for implicit TLS, 25 for plain).'
        ),
        'smtp_security': SettingDefinition(
            setting_type='string',
            default='starttls',
            options=['none', 'starttls', 'tls'],
            description='Transport security: none (plain), starttls (opportunistic), tls (implicit).'
        ),
        'smtp_username': SettingDefinition(
            setting_type='string',
            default='',
            description='SMTP auth username. Blank = unauthenticated relay.'
        ),
        'smtp_password': SettingDefinition(
            setting_type='string',
            default='',
            description='SMTP auth password. Stored encrypted at rest. UI redacts on read.'
        ),
        'from_address': SettingDefinition(
            setting_type='string',
            default='',
            description='From address on outbound mail (e.g. no-reply@customer.com).'
        ),
        'from_display_name': SettingDefinition(
            setting_type='string',
            default='AssetAtlas',
            description='Display name shown alongside the From address.'
        ),
        'reply_to_address': SettingDefinition(
            setting_type='string',
            default='',
            description='Optional Reply-To address. Blank = no Reply-To header.'
        ),
    },
}


# =============================================================================
# Helper Functions
# =============================================================================

def get_setting_definition(category: str, key: str) -> SettingDefinition:
    """
    Get the definition for a specific setting.

    Args:
        category: Setting category (e.g., 'features', 'ui')
        key: Setting key (e.g., 'enable_ml_predictions')

    Returns:
        SettingDefinition: Setting definition

    Raises:
        KeyError: If category or key doesn't exist
    """
    if category not in APPLICATION_SETTINGS:
        raise KeyError(f"Unknown settings category: {category}")

    if key not in APPLICATION_SETTINGS[category]:
        raise KeyError(f"Unknown setting key: {category}.{key}")

    return APPLICATION_SETTINGS[category][key]


def get_default_value(category: str, key: str) -> Any:
    """
    Get the default value for a setting.

    Args:
        category: Setting category
        key: Setting key

    Returns:
        Default value for the setting
    """
    definition = get_setting_definition(category, key)
    return definition.default


def validate_setting_value(category: str, key: str, value: Any) -> bool:
    """
    Validate a value for a specific setting.

    Args:
        category: Setting category
        key: Setting key
        value: Value to validate

    Returns:
        bool: True if valid, False otherwise
    """
    try:
        definition = get_setting_definition(category, key)
        return definition.validate(value)
    except KeyError:
        return False


def get_all_categories() -> list:
    """Get list of all setting categories."""
    return list(APPLICATION_SETTINGS.keys())


def get_category_settings(category: str) -> Dict[str, SettingDefinition]:
    """
    Get all settings in a category.

    Args:
        category: Setting category

    Returns:
        Dict of setting key -> SettingDefinition

    Raises:
        KeyError: If category doesn't exist
    """
    if category not in APPLICATION_SETTINGS:
        raise KeyError(f"Unknown settings category: {category}")

    return APPLICATION_SETTINGS[category]


def get_schema_dict() -> Dict[str, Dict[str, Dict[str, Any]]]:
    """
    Get the entire schema as a dictionary for serialization.

    Returns:
        Dict representation of the entire schema
    """
    schema = {}
    for category, settings in APPLICATION_SETTINGS.items():
        schema[category] = {}
        for key, definition in settings.items():
            schema[category][key] = definition.to_dict()

    return schema
