# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Application Factory

Creates and configures Flask application instances with dependency injection.
Implements the application factory pattern for clean initialization and testing.

Usage:
    from src.api.factory import create_app

    app = create_app()  # Uses default development config
    app = create_app('production')  # Uses production config
    app = create_app('test')  # Uses test config for testing
"""

import os
import sys
import logging
import time
import threading
from flask import Flask, request
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_compress import Compress
from typing import Optional

from src.api.config import get_config
from src.api.utils.caching import init_cache, cache, _update_cache_stats, _log_cache_operation
from src.api.utils.logging_config import configure_structured_logging
from src.api.utils.metrics import init_metrics, register_db_gauges
from src.api.utils.performance_tracking import init_performance_tracking
from src.api.middleware.request_id import init_request_id_middleware
from src.database.postgres_connection import PostgreSQLConnectionManager
from src.database.postgres_adapter import PostgreSQLAdapter
from flasgger import Swagger


logger = logging.getLogger(__name__)

# Module-level flag to prevent duplicate initialization logs in debug mode
_initialization_logged = False


def _should_log_init(app: Flask) -> bool:
    """
    Determine if initialization messages should be logged.

    Prevents duplicate logs by:
    1. Flask debug mode: Only log in child process (WERKZEUG_RUN_MAIN='true')
    2. Gunicorn production: Suppress verbose logs (only log from master/first worker)
    3. Only logging once per process (module-level flag)

    Args:
        app: Flask application instance

    Returns:
        bool: True if logs should be emitted, False otherwise
    """
    global _initialization_logged

    # If already logged in this process, don't log again
    if _initialization_logged:
        return False

    # In Flask debug mode with reloader: only log from child process.
    # With --no-reload, WERKZEUG_RUN_MAIN is never set — single process, safe to log.
    reloader_disabled = '--no-reload' in sys.argv
    if app.debug and os.getenv('WERKZEUG_RUN_MAIN') != 'true' and not reloader_disabled:
        return False

    # In production with Gunicorn: detect multiple workers to suppress verbose logs
    # Gunicorn sets SERVER_SOFTWARE environment variable
    is_gunicorn = 'gunicorn' in os.getenv('SERVER_SOFTWARE', '').lower()

    if is_gunicorn and not app.debug:
        # In Gunicorn mode, suppress verbose initialization logs
        # Each worker will still initialize services, but won't spam logs
        # Only log from workers with PID ending in specific pattern (pseudo-random selection)
        worker_pid = os.getpid()
        # Only log from worker whose PID modulo 4 equals 0 (approximately 1 in 4 workers)
        if worker_pid % 4 != 0:
            _initialization_logged = True  # Mark as logged to prevent future attempts
            return False

    # Mark as logged and allow logging
    _initialization_logged = True
    return True


def create_app(config_name: Optional[str] = None) -> Flask:
    """
    Create and configure Flask application instance.

    This factory function:
    1. Creates Flask app instance
    2. Loads configuration
    3. Initializes database connections
    4. Initializes services (tag rules, assets, dashboard, integrations)
    5. Registers blueprints
    6. Sets up error handlers
    7. Starts scheduler (if enabled and not in reloader parent process)

    Args:
        config_name: Configuration name ('development', 'production', 'test')
                    If None, uses FLASK_ENV environment variable

    Returns:
        Configured Flask application instance

    Example:
        app = create_app()
        app.run(host='0.0.0.0', port=5001)
    """
    # Create Flask app
    app = Flask(__name__)

    # Load configuration
    config = get_config(config_name)
    app.config.from_object(config)
    config.init_app(app)

    # Configure structured logging (must be done early)
    configure_structured_logging(app)

    # Determine if we should log initialization messages (prevents duplicates in debug mode)
    should_log = _should_log_init(app)
    if should_log:
        logger.info(f"Creating {app.config['APP_NAME']} v{app.config['APP_VERSION']}")
        logger.info(f"Environment: {config_name or os.getenv('FLASK_ENV', 'development')}")
        logger.info(f"Debug mode: {app.config['DEBUG']}")

    # Initialize request ID middleware (for request tracking)
    init_request_id_middleware(app)

    # Initialize audit trail middleware (captures user actions)
    from src.api.middleware.audit_middleware import init_audit_middleware
    init_audit_middleware(app)

    # Enable CORS
    CORS(app, origins=app.config['CORS_ORIGINS'])

    # Enable response compression (gzip/brotli/zstd)
    app.config['COMPRESS_ALGORITHM'] = 'gzip'
    app.config['COMPRESS_LEVEL'] = 6
    app.config['COMPRESS_MIN_SIZE'] = 1000  # Only compress responses > 1KB
    app.config['COMPRESS_MIMETYPES'] = ['application/json', 'text/html', 'text/css', 'text/javascript', 'application/javascript']
    Compress(app)

    # Trust X-Forwarded-* headers from configured number of reverse-proxy hops
    _init_proxy_fix(app, should_log)

    # Initialize Prometheus metrics (must be done before blueprints)
    init_metrics(app)

    # Initialize performance tracking (budget warnings and percentiles)
    init_performance_tracking(app)

    # Initialize database
    _init_database(app, should_log)

    # Initialize cache
    _init_cache(app, should_log)

    # Initialize JWT authentication
    _init_jwt(app, should_log)

    # Initialize services
    _init_services(app, should_log)

    # Register blueprints
    _register_blueprints(app, should_log)

    # Register error handlers
    _register_error_handlers(app, should_log)

    # Initialize OpenAPI/Swagger documentation
    _init_swagger(app, should_log)

    # Start scheduler (if enabled)
    _init_scheduler(app, should_log)

    if should_log:
        logger.info("Application initialization complete")

    return app


def _start_connection_monitoring(app: Flask, connection_manager, should_log: bool = True) -> None:
    """
    Start periodic connection pool monitoring in background thread.

    Logs pool statistics every 5 minutes to track connection usage and detect leaks.
    Only runs in child process (not Flask reloader parent).
    """
    # Only start monitoring in child process to avoid duplicates in debug mode
    if app.debug and os.getenv('WERKZEUG_RUN_MAIN') != 'true':
        return

    def monitor_connections():
        """Background thread function to periodically log connection stats"""
        while True:
            try:
                # Wait 5 minutes between checks
                time.sleep(300)  # 300 seconds = 5 minutes

                # Log connection pool stats
                connection_manager.log_pool_stats()
            except Exception as e:
                logger.error(f"Error in connection monitoring thread: {e}")

    # Start monitoring thread as daemon (will exit when main process exits)
    monitor_thread = threading.Thread(
        target=monitor_connections,
        name="db-connection-monitor",
        daemon=True
    )
    monitor_thread.start()

    if should_log:
        logger.info("Connection pool monitoring started (logging every 5 minutes)")


def _init_proxy_fix(app: Flask, should_log: bool = True) -> None:
    """Wrap the WSGI app with ProxyFix when TRUSTED_PROXY_HOPS > 0.

    Behind a reverse proxy (e.g. nginx), Flask's request.remote_addr is the
    proxy's IP unless we trust X-Forwarded-* headers. ProxyFix rewrites
    request.remote_addr / request.scheme / request.host based on the trusted
    number of proxy hops. Set TRUSTED_PROXY_HOPS=0 (default) when the API is
    exposed directly; set it to the proxy hop count otherwise.
    """
    hops = app.config.get('TRUSTED_PROXY_HOPS', 0)
    if hops <= 0:
        if should_log:
            logger.info("ProxyFix disabled (TRUSTED_PROXY_HOPS=0); request.remote_addr is the direct client.")
        return

    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)
    if should_log:
        logger.info(f"ProxyFix middleware enabled (trusted_proxy_hops={hops}).")


def _init_database(app: Flask, should_log: bool = True) -> None:
    """Initialize database connection manager and adapter"""
    if should_log:
        logger.info(f"Connecting to database...")

    # Create connection manager with connection pooling settings
    # Reduced pool sizes to prevent connection exhaustion
    # Development: 5-10 connections (single worker)
    # Production: 20-30 connections (multiple workers)
    is_production = not app.config.get('DEBUG', False)

    if is_production:
        # Production defaults: Higher pool for multiple workers
        default_pool_size = 10
        default_max_overflow = 10
    else:
        # Development defaults: Lower pool for single worker
        default_pool_size = 5
        default_max_overflow = 5

    pool_size = app.config.get('DB_POOL_SIZE', default_pool_size)
    max_overflow = app.config.get('DB_MAX_OVERFLOW', default_max_overflow)

    connection_manager = PostgreSQLConnectionManager(
        database_url=app.config['DATABASE_URL'],
        min_connections=pool_size,
        max_connections=pool_size + max_overflow
    )

    # Create adapter
    db_adapter = PostgreSQLAdapter(connection_manager=connection_manager)

    # Store in app context for access in blueprints
    app.db_adapter = db_adapter
    app.connection_manager = connection_manager

    # Expose the live DB gauges (active alerts by severity, total assets) on
    # /metrics — queried at scrape time by a custom collector. Best-effort:
    # never let instrumentation registration block application startup.
    try:
        register_db_gauges(db_adapter)
    except Exception:
        if should_log:
            logger.warning("Could not register DB metric gauges", exc_info=True)

    if should_log:
        logger.info(
            f"Database connection established with pool: "
            f"size={pool_size}, max_overflow={max_overflow}, "
            f"total_max={pool_size + max_overflow}, "
            f"mode={'production' if is_production else 'development'}"
        )

    # Start periodic connection monitoring (every 5 minutes)
    _start_connection_monitoring(app, connection_manager, should_log)

    # Request-scoped connection reuse: all DB calls within a single HTTP
    # request share one pooled connection instead of acquiring/releasing
    # separately.  Non-Flask contexts (scheduler, CLI) are unaffected.
    @app.before_request
    def init_db_scope():
        connection_manager.begin_scope()

    @app.teardown_request
    def cleanup_db_scope(exception=None):
        connection_manager.end_scope(error=exception)


def _init_cache(app: Flask, should_log: bool = True) -> None:
    """Initialize caching layer (Redis or simple in-memory cache)"""
    cache_type = app.config.get('CACHE_TYPE', 'redis')
    if should_log:
        logger.info(f"Initializing cache: type={cache_type}")

    try:
        init_cache(app)
        if should_log:
            logger.info("Cache initialized successfully")
    except Exception as e:
        logger.warning(f"Cache initialization failed: {e}")
        logger.warning("Falling back to simple in-memory cache")
        # Fallback to simple cache if Redis is unavailable
        app.config['CACHE_TYPE'] = 'simple'
        init_cache(app)


def _init_jwt(app: Flask, should_log: bool = True) -> None:
    """
    Initialize JWT authentication with Flask-JWT-Extended.

    Sets up JWT manager with callbacks for:
    - User identity loading (creating tokens)
    - User lookup (loading user for protected routes)
    - Token revocation checking (blacklist)
    - Error handlers
    """
    if should_log:
        logger.info("Initializing JWT authentication...")

    jwt = JWTManager(app)

    # Import repositories for callbacks
    from src.auth.repositories.user_repository import UserRepository
    from src.auth.repositories.session_repository import SessionRepository

    # Create repository instances
    user_repo = UserRepository(app.connection_manager)
    session_repo = SessionRepository(app.connection_manager)

    @jwt.user_identity_loader
    def user_identity_loader(user):
        """
        Convert user object to JWT identity (user ID).
        Called when creating access/refresh tokens with create_access_token(identity=user).

        Args:
            user: User object or dict with 'id' field

        Returns:
            str: User ID as string
        """
        if isinstance(user, dict):
            return str(user['id'])
        return str(user.id) if hasattr(user, 'id') else str(user)

    @jwt.user_lookup_loader
    def user_lookup_loader(_jwt_header, jwt_data):
        """
        Load user object from database given JWT identity.
        Called on every protected route to load the current user.
        Uses caching to avoid database queries on every request.

        Args:
            _jwt_header: JWT header dict (unused)
            jwt_data: JWT payload containing 'sub' (subject/user ID)

        Returns:
            dict: User object from database, or None if not found
        """
        user_id = jwt_data["sub"]
        cache_key = f"jwt_user:{user_id}"
        cache_namespace = "jwt_auth"

        # Try to get from cache first
        start_time = time.time()
        try:
            cached_user = cache.get(cache_key)
            duration_ms = (time.time() - start_time) * 1000

            if cached_user is not None:
                # Cache HIT - user data cached
                _update_cache_stats('get', 'hit', cache_namespace)
                _log_cache_operation('get', 'hit', cache_key, duration_ms, cache_namespace)
                return cached_user

            # Cache MISS
            _update_cache_stats('get', 'miss', cache_namespace)
            _log_cache_operation('get', 'miss', cache_key, duration_ms, cache_namespace)
        except Exception as e:
            logger.debug(f"Cache GET error for user {user_id}: {e}")
            _update_cache_stats('get', 'error', cache_namespace)

        # Fetch from database
        try:
            user = user_repo.get_user_by_id(user_id)

            if user:
                # Cache the user data (TTL: 60 seconds for security)
                try:
                    set_start = time.time()
                    cache.set(cache_key, user, timeout=60)
                    set_duration_ms = (time.time() - set_start) * 1000
                    _update_cache_stats('set', 'success', cache_namespace)
                    _log_cache_operation('set', 'success', cache_key, set_duration_ms, cache_namespace)
                except Exception as e:
                    logger.debug(f"Cache SET error for user {user_id}: {e}")
                    _update_cache_stats('set', 'error', cache_namespace)

            return user
        except Exception as e:
            logger.error(f"Failed to load user {user_id}: {e}")
            return None

    @jwt.token_in_blocklist_loader
    def check_if_token_revoked(jwt_header, jwt_payload):
        """
        Check if a token has been revoked/blacklisted.
        Called on every protected route before allowing access.
        Uses caching to avoid database queries on every request.

        Args:
            jwt_header: JWT header dict
            jwt_payload: JWT payload containing 'jti' (JWT ID)

        Returns:
            bool: True if token is revoked, False if valid
        """
        # Bridge-minted access tokens carry src=teams_bridge and are
        # issued only by the /api/auth/teams-bridge/exchange endpoint
        # (gated on the service API key, with a 5-minute TTL). They
        # are not recorded in the sessions table, so the normal
        # revocation lookup below would always treat them as revoked.
        # The service-key gate is a sufficient authentication boundary.
        if jwt_payload.get("src") == "teams_bridge":
            return False

        jti = jwt_payload["jti"]
        token_type = jwt_payload["type"]
        cache_key = f"jwt_session:{jti}"
        cache_namespace = "jwt_auth"

        try:
            # For access tokens: Check if session exists and is active
            if token_type == "access":
                # Try cache first
                start_time = time.time()
                try:
                    cached_result = cache.get(cache_key)
                    duration_ms = (time.time() - start_time) * 1000

                    if cached_result is not None:
                        # Cache HIT
                        _update_cache_stats('get', 'hit', cache_namespace)
                        _log_cache_operation('get', 'hit', cache_key, duration_ms, cache_namespace)
                        # cached_result is the session dict or False if not found/inactive
                        if cached_result is False:
                            return True  # Revoked (cached as revoked)
                        return False  # Valid (cached session)

                    # Cache MISS
                    _update_cache_stats('get', 'miss', cache_namespace)
                    _log_cache_operation('get', 'miss', cache_key, duration_ms, cache_namespace)
                except Exception as e:
                    logger.debug(f"Cache GET error for session {jti}: {e}")
                    _update_cache_stats('get', 'error', cache_namespace)

                # Fetch from database
                session = session_repo.get_session_by_access_jti(jti)

                # Cache the result (TTL: 30 seconds for security)
                try:
                    set_start = time.time()
                    if not session or not session.get('is_active'):
                        # Cache negative result (revoked)
                        cache.set(cache_key, False, timeout=30)
                    else:
                        # Cache positive result (valid session)
                        cache.set(cache_key, session, timeout=30)
                    set_duration_ms = (time.time() - set_start) * 1000
                    _update_cache_stats('set', 'success', cache_namespace)
                    _log_cache_operation('set', 'success', cache_key, set_duration_ms, cache_namespace)
                except Exception as e:
                    logger.debug(f"Cache SET error for session {jti}: {e}")
                    _update_cache_stats('set', 'error', cache_namespace)

                if not session:
                    return True  # Session not found = revoked
                if not session.get('is_active'):
                    return True  # Session deactivated = revoked
                # Check if session has expired
                expires_at = session.get('expires_at')
                if expires_at:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    if hasattr(expires_at, 'tzinfo') and expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    if now > expires_at:
                        return True  # Session expired = revoked
                return False  # Session active and not expired

            # For refresh tokens: Check if session is still active via session_id claim
            elif token_type == "refresh":
                session_id = jwt_payload.get("session_id")
                if not session_id:
                    return True  # No session claim = revoked
                session = session_repo.get_session(session_id)
                if not session or not session.get('is_active'):
                    return True  # Session gone or deactivated = revoked
                expires_at = session.get('expires_at')
                if expires_at:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    if hasattr(expires_at, 'tzinfo') and expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    if now > expires_at:
                        return True  # Session expired = revoked

                # Compare the inbound refresh JWT against the stored hash.
                # After a legitimate rotation the stored hash reflects the NEW
                # token, so the OLD token's hash will no longer match — that's
                # refresh-token reuse (RFC 6819 §5.2.2.3), the strongest
                # compromise signal we have. Revoke the entire session so the
                # attacker is locked out immediately; the legitimate user is
                # forced to re-login.
                from src.auth.services.token_service import TokenService
                auth_header = request.headers.get('Authorization', '') if request else ''
                if not auth_header.startswith('Bearer '):
                    return True  # No bearer token = can't verify, revoke defensively
                raw_jwt = auth_header[7:]
                stored_hash = session.get('refresh_token_hash')
                if not TokenService.verify_refresh_token_hash(raw_jwt, stored_hash):
                    # Refresh-token reuse detected — revoke the session.
                    try:
                        session_repo.deactivate_session(session_id)
                    except Exception as deact_err:
                        logger.error(
                            f"Failed to deactivate session {session_id} after "
                            f"refresh-token reuse detection: {deact_err}"
                        )
                    try:
                        from src.auth.services.audit_service import AuditService
                        audit_service = AuditService(app.connection_manager)
                        audit_service.log_auth(
                            action='refresh_token_reuse_detected',
                            success=False,
                            user_id=session.get('user_id'),
                            ip=request.remote_addr if request else None,
                            user_agent=request.headers.get('User-Agent') if request else None,
                            failure_reason='session_revoked_for_safety',
                            metadata={'session_id': str(session_id)},
                        )
                    except Exception as audit_err:
                        logger.error(
                            f"Failed to audit-log refresh-token reuse for "
                            f"session {session_id}: {audit_err}"
                        )
                    return True  # Revoked

                return False

            return False
        except Exception as e:
            # Fail closed: a transient DB/cache error must not let a possibly-revoked
            # token through. Treating the token as revoked forces the client to
            # refresh and gives operators a clean audit signal instead of silent
            # acceptance under a partial outage. Trade-off: a sustained DB outage
            # rejects refresh tokens too (this branch covers both access and
            # refresh paths), so users have no recovery until the DB is back —
            # documented as the safer of the two failure modes.
            logger.error(f"Error checking token revocation; failing closed: {e}")
            return True

    # JWT Error handlers
    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        """Handle expired token errors"""
        return {
            'error': 'token_expired',
            'message': 'The token has expired'
        }, 401

    @jwt.invalid_token_loader
    def invalid_token_callback(error):
        """Handle invalid token errors"""
        return {
            'error': 'invalid_token',
            'message': 'Invalid token format or signature'
        }, 401

    @jwt.unauthorized_loader
    def missing_token_callback(error):
        """Handle missing token errors"""
        return {
            'error': 'missing_token',
            'message': 'Authorization token is required'
        }, 401

    @jwt.revoked_token_loader
    def revoked_token_callback(jwt_header, jwt_payload):
        """Handle revoked token errors"""
        return {
            'error': 'token_revoked',
            'message': 'The token has been revoked'
        }, 401

    if should_log:
        logger.info("JWT authentication initialized successfully")


def _init_services(app: Flask, should_log: bool = True) -> None:
    """Initialize all application services"""
    if should_log:
        logger.info("Initializing services...")

    db_adapter = app.db_adapter

    # Field Resolver — the single choke-point for field identifier resolution
    # and SQL generation across tag_rules, posture, charts, reports, Explorer
    # (issue #455). Stateless service; FieldCatalog owns a 60s TTL cache.
    from src.modules.fields import FieldCatalog, FieldResolver
    from src.api.utils.caching import get_cached_group_mapping
    from src.modules.fields.unified.repository import UnifiedFieldsRepository
    from src.modules.fields.unified.service import UnifiedFieldsService

    unified_fields_repo = UnifiedFieldsRepository(db_adapter)
    field_catalog = FieldCatalog(
        db_adapter=db_adapter,
        group_mapping_provider=get_cached_group_mapping,
        unified_fields_provider=unified_fields_repo.list_for_catalog,
    )
    app.field_resolver = FieldResolver(catalog=field_catalog)
    app.unified_fields_service = UnifiedFieldsService(
        unified_fields_repo,
        catalog=field_catalog,
        resolver=app.field_resolver,
        db_adapter=db_adapter,
    )

    if should_log:
        logger.info("FieldResolver + Unified Fields service initialized")

    # Tag Rules services — QueryEngineService first (needed by preview + execution)
    from src.modules.tag_rules.services.query_engine_service import QueryEngineService
    from src.modules.tag_rules.services.rule_management_service import RuleManagementService
    from src.modules.tag_rules.services.preview_service import PreviewService
    from src.modules.tag_rules.services.rule_execution_service import RuleExecutionService

    app.query_engine = QueryEngineService(resolver=app.field_resolver)
    app.rule_mgmt_service = RuleManagementService(
        db_adapter,
        auto_execute=app.config['TAG_RULES_AUTO_EXECUTE']
    )
    app.preview_service = PreviewService(
        db_adapter,
        query_engine=app.query_engine,
        resolver=app.field_resolver,
    )
    app.execution_service = RuleExecutionService(
        db_adapter,
        query_engine=app.query_engine,
        resolver=app.field_resolver,
        rule_mgmt=app.rule_mgmt_service,
    )
    # Backref so RuleManagementService can auto-execute on create/update.
    # Two-phase wire-up: rule_mgmt is built first because RuleExecutionService
    # depends on it (circular dep), then the execution service is injected back.
    app.rule_mgmt_service.execution_service = app.execution_service

    if should_log:
        logger.info("Tag rules services initialized")

    # Posture Rules services
    from src.modules.posture_rules.repositories.posture_repository import PostureRepository
    from src.modules.posture_rules.services.category_read_service import PostureCategoryReadService
    from src.modules.posture_rules.services.sub_rule_management_service import PostureSubRuleManagementService
    from src.modules.posture_rules.services.posture_evaluation_service import PostureEvaluationService
    from src.modules.posture_rules.services.posture_preview_service import PosturePreviewService

    posture_repo = PostureRepository(db_adapter=db_adapter)

    app.posture_repository = posture_repo
    app.posture_category_read_service = PostureCategoryReadService(repository=posture_repo)
    # Evaluator first so the management service can drive single-category
    # re-evals from its CUD paths (guarded by POSTURE_RULES_AUTO_EXECUTE).
    app.posture_evaluation_service = PostureEvaluationService(
        repository=posture_repo,
        query_engine=app.query_engine,
        db_adapter=db_adapter,
        resolver=app.field_resolver,
    )
    app.posture_sub_rule_mgmt_service = PostureSubRuleManagementService(
        repository=posture_repo,
        evaluator=app.posture_evaluation_service,
        auto_execute=app.config['POSTURE_RULES_AUTO_EXECUTE'],
    )
    app.posture_preview_service = PosturePreviewService(
        repository=posture_repo,
        query_engine=app.query_engine,
        db_adapter=db_adapter,
        resolver=app.field_resolver,
    )

    if should_log:
        logger.info("Posture rules services initialized")

    # Asset services
    from src.modules.assets.repositories.asset_repository import AssetRepository
    from src.modules.assets.services.asset_service import AssetService
    from src.modules.assets.services.asset_history_service import AssetHistoryService
    from src.modules.alerts.repository import AlertsRepository

    app.asset_repository = AssetRepository(db_adapter, resolver=app.field_resolver)
    # AlertsRepository is constructed here — ahead of the alerts-services block
    # below — so AssetService can receive it for the per-asset alert_count badge
    # on the asset-detail header (#891). It only needs db_adapter + query_engine,
    # both already available at this point.
    app.alerts_repository = AlertsRepository(db_adapter, query_compiler=app.query_engine)
    app.asset_service = AssetService(app.asset_repository, app.alerts_repository)
    app.asset_history_service = AssetHistoryService(db_adapter)

    from src.modules.reference_data.repository import ReferenceDataRepository
    from src.modules.reference_data.service import ReferenceDataService
    from src.api.utils import metrics as metrics_module
    app.reference_data_service = ReferenceDataService(
        repository=ReferenceDataRepository(db_adapter),
        connection_manager=app.connection_manager,
        metrics=metrics_module,
        app_version=app.config.get('APP_VERSION', '0.0.0'),
    )

    if should_log:
        logger.info("Asset services initialized")

    # Asset edit services (reclassify, tag, merge)
    from src.modules.assets.services.reclassify_service import ReclassifyService
    from src.modules.assets.services.tag_service import TagService
    from src.modules.assets.services.merge_service import MergeService
    from src.modules.asset_generation.repositories.category_override_repository import CategoryOverrideRepository
    from src.modules.assets.repositories.custom_subcategory_repository import CustomSubcategoryRepository
    from src.modules.assets.services.asset_label_override_service import AssetLabelOverrideService
    from src.modules.asset_generation.repositories.asset_label_override_repository import AssetLabelOverrideRepository

    app.category_override_repo = CategoryOverrideRepository(db_adapter)
    app.custom_subcategory_repo = CustomSubcategoryRepository(db_adapter)
    app.asset_label_override_repo = AssetLabelOverrideRepository(db_adapter)
    app.reclassify_service = ReclassifyService(
        db_adapter,
        app.category_override_repo,
        custom_subcategory_repo=app.custom_subcategory_repo,
    )
    app.tag_service = TagService(db_adapter)
    app.merge_service = MergeService(db_adapter, label_override_repo=app.asset_label_override_repo)

    app.asset_label_override_service = AssetLabelOverrideService(
        db_adapter, app.asset_label_override_repo,
    )

    if should_log:
        logger.info("Asset edit services initialized (reclassify, tag, merge, label_override)")

    # Reports services
    from src.modules.reports.repositories.reports_repository import ReportsRepository
    from src.modules.reports.repositories.system_preferences_repository import SystemPreferencesRepository
    from src.modules.reports.services.reports_service import ReportsService
    from src.modules.reports.services.system_preferences_service import SystemPreferencesService

    app.reports_repository = ReportsRepository(db_adapter)
    app.reports_service = ReportsService(app.reports_repository)
    app.system_preferences_repository = SystemPreferencesRepository(db_adapter)
    app.system_preferences_service = SystemPreferencesService(app.system_preferences_repository)

    if should_log:
        logger.info("Reports services initialized")

    # Saved Explorer filters (per-user CRUD)
    from src.api.blueprints.saved_filters.repository import SavedFilterRepository
    from src.api.blueprints.saved_filters.service import SavedFiltersService

    app.saved_filters_repository = SavedFilterRepository(db_adapter)
    app.saved_filters_service = SavedFiltersService(app.saved_filters_repository)

    # Merge denylist (admin-managed identifier exclusion list for the merge pipeline)
    from src.modules.merge_denylist.repository import MergeDenylistRepository
    from src.modules.merge_denylist.service import MergeDenylistService

    app.merge_denylist_repository = MergeDenylistRepository(db_adapter)
    app.merge_denylist_service = MergeDenylistService(app.merge_denylist_repository)

    # Asset split (admin over-merge remediation) — reuses denylist + pipeline status
    from src.modules.assets.repositories.split_repository import AssetSplitRepository
    from src.modules.assets.services.asset_split_service import AssetSplitService

    app.asset_split_repository = AssetSplitRepository(db_adapter)
    app.asset_split_service = AssetSplitService(
        app.asset_split_repository,
        app.merge_denylist_service,
        lambda: app.pipeline_service.get_current_status(),
    )

    # Settings manager for dynamic configuration (application settings only)
    from src.api.modules.settings import SettingsManager
    app.settings_manager = SettingsManager(app.connection_manager)

    try:
        from src.api.utils.teams_env_import import (
            import_legacy_teams_env_once,
        )
        import_legacy_teams_env_once(app.settings_manager)
    except Exception as exc:  # noqa: BLE001
        # Never let a boot-time helper crash the API.
        logger.warning("Teams env-import skipped: %s", exc)

    if should_log:
        logger.info("Settings manager initialized (application settings only)")

    # Dashboard services (must follow settings_manager so thresholds are configurable)
    from src.modules.dashboard.repositories.dashboard_repository import DashboardRepository
    from src.modules.dashboard.services.dashboard_service import DashboardService

    app.dashboard_repository = DashboardRepository(db_adapter)
    app.dashboard_service = DashboardService(app.dashboard_repository, settings_manager=app.settings_manager)

    if should_log:
        logger.info("Dashboard services initialized")

    # Email service (SMTP sending, gated on email settings)
    from src.auth.services.email_service import EmailService
    app.email_service = EmailService(app.settings_manager)
    # Bridge the SMTP sender into the email alert channel (#919). The channel is
    # a context-free singleton; this is how its send()/send-test reach EmailService.
    from src.modules.alert_delivery.channels.email_channel import set_email_service
    set_email_service(app.email_service)
    if should_log:
        logger.info("Email service initialized")

    # Integration services
    from src.modules.integrations.services import IntegrationService, SchedulerService
    from src.modules.integrations.services.asset_generation_trigger_service import AssetGenerationTriggerService
    from src.modules.integrations import discover_connectors

    app.integration_service = IntegrationService()

    if not app.config.get('TESTING'):
        try:
            app.integration_service.cleanup_stale_running_flags()
        except Exception as e:
            logger.warning(f"Stale is_running cleanup skipped: {e}")

    from src.modules.integrations.services.mapping_preview_service import MappingPreviewService
    app.mapping_preview_service = MappingPreviewService()
    app.asset_generation_trigger_service = AssetGenerationTriggerService(
        db_adapter=db_adapter,
        settings_manager=app.settings_manager,
        posture_evaluation_service=app.posture_evaluation_service,
    )
    app.scheduler_service = SchedulerService()
    app.scheduler_service.initialize(app.integration_service)

    # Store scheduler service in config for monitoring endpoints
    app.config['SCHEDULER_SERVICE'] = app.scheduler_service

    # Discover all connectors on initialization
    discover_connectors()
    connector_count = len(app.integration_service.get_available_connectors())

    if not app.config.get('TESTING'):
        try:
            from src.modules.integrations.repositories.technology_repository import (
                IntegrationTechnologyRepository,
            )
            IntegrationTechnologyRepository().warn_missing_logos()
        except Exception as e:
            logger.warning(f"Skipped technology logo validation: {e}")

    if should_log:
        logger.info(f"Discovered {connector_count} connector types")
        logger.info("Integration services initialized")
        logger.info("Blueprints will access services via current_app (dependency injection)")

    # Integration group services
    from src.modules.integrations.repositories.integration_group_repository import IntegrationGroupRepository
    from src.modules.integrations.services.integration_group_service import IntegrationGroupService

    app.integration_group_repository = IntegrationGroupRepository(db_adapter)
    app.integration_group_service = IntegrationGroupService(
        app.integration_group_repository,
        app.integration_service
    )

    if should_log:
        logger.info("Integration group services initialized")

    # Pipeline service for async pipeline execution and tracking
    from src.modules.pipeline.pipeline_service import PipelineService
    app.pipeline_service = PipelineService(
        connection_manager=app.connection_manager,
        trigger_service=app.asset_generation_trigger_service
    )

    # Pipeline phase service for the Settings → Pipeline operator view
    from src.modules.pipeline.pipeline_phase_service import PipelinePhaseService
    app.pipeline_phase_service = PipelinePhaseService(app.db_adapter)

    if should_log:
        logger.info("Pipeline service initialized")

    # Log cleanup service for log retention management
    from src.modules.logs.services import LogCleanupService
    app.log_cleanup_service = LogCleanupService(
        log_base_dir="logs",
        connection_manager=app.connection_manager
    )

    if should_log:
        logger.info("Log cleanup service initialized")

    # Alerts service for alert evaluation and management.
    # app.alerts_repository is built earlier (asset-services block) because
    # AssetService depends on it for per-asset alert counts (#891).
    from src.modules.alerts.service import AlertsService
    from src.modules.alert_delivery.wiring import build_alert_delivery_service

    app.alert_delivery_service = build_alert_delivery_service(
        db_adapter, settings_manager=app.settings_manager)
    app.alerts_service = AlertsService(
        app.alerts_repository, delivery_service=app.alert_delivery_service)

    from src.modules.anomaly_detection.service import AnomalyDetectionService
    app.anomaly_service = AnomalyDetectionService(
        db_adapter, app.alerts_repository, delivery_service=app.alert_delivery_service)

    # Prometheus monitoring — opt-in via MONITORING_ENABLED
    from src.modules.alerts.prometheus_webhook_service import PrometheusWebhookService

    if app.config.get('MONITORING_ENABLED'):
        # Alertmanager rules sync stays env-gated: it needs the monitoring overlay
        # (the alertmanager container) actually deployed. The dashboards proxy
        # below does NOT — it's toggled at request time by the runtime
        # `monitoring.dashboards` setting (Settings -> Monitoring -> Dashboards).
        from src.modules.alerts.prometheus_sync import sync_prometheus_rules
        rules_dir = app.config.get('MONITORING_RULES_DIR', 'monitoring/alerts')
        sync_result = sync_prometheus_rules(rules_dir, app.db_adapter)
        logger.info("Prometheus rules synced from %s: %s", rules_dir, sync_result)

    # MonitoringService is always attached: its constructor only stores the
    # Prometheus URL (no I/O), and the /api/monitoring proxy gates itself per
    # request on the runtime toggle — so the dashboards can be enabled/disabled
    # from the UI without a restart.
    from src.modules.monitoring.service import MonitoringService
    app.monitoring_service = MonitoringService(app.config['PROMETHEUS_URL'])

    app.prometheus_webhook_service = PrometheusWebhookService(app.db_adapter)

    # Metrics forwarding runtime config (#922 P2b) — the remote_write target for the
    # opt-in metrics-forwarder agent. The shared-config dir + reload URL come from env
    # (see the `forwarding` compose profile); defaults live in the service.
    from src.modules.metrics_forwarding.wiring import build_metrics_forwarding_service
    app.metrics_forwarding_service = build_metrics_forwarding_service(db_adapter)

    if should_log:
        logger.info("Alerts service initialized")

    # Enrichment service
    from src.modules.enrichment.repositories.enrichment_repository import EnrichmentRepository
    from src.modules.enrichment.services.enrichment_service import EnrichmentService

    app.enrichment_repository = EnrichmentRepository(db_adapter)
    app.enrichment_service = EnrichmentService(
        app.enrichment_repository, db_adapter, field_resolver=app.field_resolver
    )

    if should_log:
        logger.info("Enrichment service initialized")

    # Chat-providers service: needed by both init_chat_service (later)
    # and the pipeline LLM factory just below. Initializing it here keeps
    # both downstream consumers reading from the same instance.
    from src.api.blueprints.chat.admin_config import (
        ChatProvidersRepository, ChatProvidersService,
    )
    app.chat_providers_service = ChatProvidersService(
        repo=ChatProvidersRepository(app.db_adapter),
        settings_manager=getattr(app, "settings_manager", None),
    )

    # Field registry (shared instance). The pipeline LLM client is NOT cached
    # here — EmbeddingIntegrationService resolves it fresh per run from the
    # current chat-provider config (handles post-startup config changes and the
    # no-app-context scheduler path).
    try:
        from src.services.embedding.field_registry import FieldRegistry
        app.field_registry_service = FieldRegistry()
    except Exception as e:
        logger.warning(f"Field registry service not available: {e}")
        app.field_registry_service = None

    try:
        from src.modules.asset_generation.services.embedding_integration import (
            EmbeddingIntegrationService,
        )
        app.embedding_integration_service = EmbeddingIntegrationService(
            db_adapter,
            field_registry=app.field_registry_service,
            chat_providers_service=app.chat_providers_service,
        )
    except Exception as e:
        logger.warning(f"Embedding integration service not available: {e}")
        app.embedding_integration_service = None

    # API Key services
    from src.modules.api_keys.repository import ApiKeyRepository
    from src.modules.api_keys.service import ApiKeyService

    app.api_key_repository = ApiKeyRepository(db_adapter)
    app.api_key_service = ApiKeyService(app.api_key_repository)

    if should_log:
        logger.info("API key services initialized")

    # Preload suggestion service (embedding model) in background thread
    # Skip in test mode to avoid loading a heavy ML model during tests
    if not app.config.get('TESTING'):
        _preload_suggestion_service(app, should_log)

    init_chat_service(app, should_log)
    init_chat_rate_limiter(app, should_log)

    _init_teams_link_service(app, should_log)


def _init_teams_link_service(app: Flask, should_log: bool = True) -> None:
    """Build the TeamsLinkService if a secret is configured, else skip.

    Without TEAMS_LINK_SECRET_KEY the Teams bot endpoints are dead routes
    (initiate/exchange will 503 via require_service_api_key, confirm will
    AttributeError when reaching for current_app.teams_link_service).
    Tests rely on the attribute existing so we still attach a stub when
    the bridge is disabled by setting the attribute to None.
    """
    from src.modules.teams_link.repository import (TeamsLinkRepository,
                                                     UsedJtiStore)
    from src.modules.teams_link.service import TeamsLinkService

    secret = app.config.get("TEAMS_LINK_SECRET_KEY", "")
    if not secret:
        app.teams_link_service = None
        if should_log:
            logger.info(
                "Teams link service disabled (TEAMS_LINK_SECRET_KEY unset)",
            )
        return

    from src.api.utils.public_base_url import resolve_public_base_url

    app.teams_link_service = TeamsLinkService(
        repo=TeamsLinkRepository(db_adapter=app.db_adapter),
        used_jti_store=UsedJtiStore(db_adapter=app.db_adapter),
        secret_key=secret,
        public_base_url=lambda: resolve_public_base_url(
            getattr(app, 'settings_manager', None),
        ),
        token_ttl_seconds=300,
    )
    if should_log:
        logger.info("Teams link service initialized")


def init_chat_service(app: Flask, should_log: bool = True) -> None:
    """Build the ChatService and attach it to the app, if CHAT_ENABLED.

    The blueprint registration is gated on this attribute being non-None.
    Wires the LLM client, tool registry, and per-turn limits from app.config.
    ``chat_providers_service`` is initialized earlier in ``_init_services``
    because the pipeline LLM factory also reads from it; the admin /config
    routes work even when chat itself is disabled, so admins can configure
    providers before flipping CHAT_ENABLED.
    """
    if not app.config.get("CHAT_ENABLED", False):
        app.chat_service = None
        if should_log:
            logger.info("Chat service disabled (CHAT_ENABLED=false)")
        return

    # Imported outside the try because an `except` clause cannot reference a
    # name the try block binds — the handler below needs it to tell "not set up
    # yet" apart from a real fault.
    from src.api.blueprints.chat.admin_config.service import (
        ProviderNotConfiguredError,
    )

    try:
        from src.api.blueprints.chat.llm import make_llm_client
        from src.api.blueprints.chat.service import ChatService
        from src.api.blueprints.chat.tool_bindings import build_registry

        registry = build_registry(
            db=app.db_adapter,
            # Lazy getter: the embedding suggestion service is preloaded in
            # a background thread, so it's usually still None at this point.
            # Looking it up at tool-invoke time means semantic_suggest starts
            # working as soon as preload finishes (instead of being stuck with
            # a captured None for the process's lifetime).
            suggestion_service_getter=lambda: getattr(app, "suggestion_service", None),
            field_resolver=getattr(app, "field_resolver", None),
            unified_fields_service=getattr(app, "unified_fields_service", None),
            dashboard_service=getattr(app, "dashboard_service", None),
            pipeline_service=getattr(app, "pipeline_service", None),
            pipeline_phase_service=getattr(app, "pipeline_phase_service", None),
            scheduler_service=getattr(app, "scheduler_service", None),
            integration_service=getattr(app, "integration_service", None),
            mapping_preview_service=getattr(app, "mapping_preview_service", None),
            integration_group_service=getattr(
                app, "integration_group_service", None),
            alerts_service=getattr(app, "alerts_service", None),
            enrichment_service=getattr(app, "enrichment_service", None),
            # MonitoringService is now always attached (the /api/monitoring proxy
            # gates itself at request time), but the chat `query_metrics` tool is
            # still wired on the deploy-time MONITORING_ENABLED flag: without the
            # stack it could only error, so pass None to keep it unadvertised and
            # preserve the tool's clean "monitoring is off" message.
            monitoring_service=(
                getattr(app, "monitoring_service", None)
                if app.config.get("MONITORING_ENABLED") else None
            ),
            settings_manager=getattr(app, "settings_manager", None),
            # Rules & posture (§2.D chat read-parity): the unified list_rules /
            # preview_rule / preview_rule_output / get_rule_history /
            # get_compliance_matrix tools wrap these existing services (all
            # filter matching delegates to query_engine = AssetQueryCompiler).
            query_engine=getattr(app, "query_engine", None),
            tag_rule_service=getattr(app, "rule_mgmt_service", None),
            tag_preview_service=getattr(app, "preview_service", None),
            posture_repository=getattr(app, "posture_repository", None),
            posture_category_read_service=getattr(
                app, "posture_category_read_service", None),
            posture_preview_service=getattr(
                app, "posture_preview_service", None),
            posture_sub_rule_mgmt_service=getattr(
                app, "posture_sub_rule_mgmt_service", None),
        )
        from src.api.blueprints.chat.tool_router import ToolRouter, ToolEmbedder
        _emb_svc = getattr(app, "suggestion_service", None)
        _embedder = None
        if _emb_svc is not None and getattr(_emb_svc, "_embedding_service", None):
            _embedder = ToolEmbedder(_emb_svc._embedding_service)
        # top_k comes from the DB setting (the Chatbot → Behavior tab control),
        # NOT a config key — chat.tool_routing_top_k is the documented knob, and
        # CHAT_TOOL_ROUTING_TOP_K was never defined so the router was pinned to
        # the default 5 regardless of the setting. This init-time read seeds the
        # ToolRouter's default; service._stream_inner re-reads it live per turn
        # (like the enabled flag) and passes it into select(), so a Behavior-tab
        # change takes effect on the next turn without a restart. The 5 fallback
        # covers legacy / no-settings construction.
        _sm = getattr(app, "settings_manager", None)
        _routing_top_k = (int(_sm.get("chat", "tool_routing_top_k", default=5))
                          if _sm is not None else 5)
        app.chat_tool_router = ToolRouter(registry, embedder=_embedder,
                                          top_k=_routing_top_k)

        active = app.chat_providers_service.get_active_provider()
        effective = app.chat_providers_service.get_effective_config(active)
        app.chat_service = ChatService(
            db=app.db_adapter,
            llm=make_llm_client(effective),
            registry=registry,
            providers_service=app.chat_providers_service,
            settings_manager=getattr(app, "settings_manager", None),
            max_tool_iterations=app.config["CHAT_MAX_TOOL_ITERATIONS"],
            max_output_tokens=app.config["CHAT_MAX_OUTPUT_TOKENS"],
            turn_timeout_seconds=app.config["CHAT_TURN_TIMEOUT_SECONDS"],
            turn_total_timeout_seconds=app.config[
                "CHAT_TURN_TOTAL_TIMEOUT_SECONDS"],
            tool_router=app.chat_tool_router,
        )
        if should_log:
            logger.info(
                f"Chat service initialized "
                f"(provider={app.chat_service.llm.provider}, "
                f"model={app.chat_service.llm.model})"
            )
    except ProviderNotConfiguredError as e:
        # The normal state of a fresh install: nobody has entered an API key
        # yet, and they could not have before the install finished. Logging
        # that at ERROR with a traceback — once per gunicorn worker — trains
        # operators to ignore the level they alert on (#1104).
        if should_log:
            logger.warning("Chat disabled: %s", e)
        app.chat_service = None
    except Exception:
        # Anything else IS a fault worth a traceback: a malformed credential
        # blob, a missing table, a broken client constructor.
        logger.exception(
            "Chat service init failed; chat disabled. "
            "Configure the active provider under Settings → Chatbot."
        )
        app.chat_service = None


def init_chat_rate_limiter(app: Flask, should_log: bool = True) -> None:
    """Build the ChatRateLimiter and attach it to the app, if chat is up.

    Uses the configured Redis cache backend. Falls back to NullRateLimiter
    (permissive) when CACHE_TYPE != 'redis' so dev/test stays functional.
    Skipped entirely when chat_service is None (CHAT_ENABLED=false or init
    failed) since the route blueprint won't register.
    """
    if getattr(app, "chat_service", None) is None:
        app.chat_rate_limiter = None
        return

    from src.api.blueprints.chat.rate_limit import (ChatRateLimiter,
                                                    NullRateLimiter)
    per_day = app.config["CHAT_MAX_MESSAGES_PER_USER_PER_DAY"]

    if app.config.get("CACHE_TYPE") == "redis":
        try:
            from src.api.utils.caching import cache as _cache
            redis_client = _cache.cache._write_client
            app.chat_rate_limiter = ChatRateLimiter(redis_client,
                                                    per_day=per_day)
            if should_log:
                logger.info("Chat rate limiter initialized (Redis-backed, "
                            f"per_day={per_day})")
            return
        except Exception:
            logger.exception(
                "Chat rate limiter init failed; using NullRateLimiter")

    app.chat_rate_limiter = NullRateLimiter(per_day=per_day)
    if should_log:
        logger.info("Chat rate limiter using NullRateLimiter "
                    f"(no Redis; per_day={per_day})")


def _preload_suggestion_service(app: Flask, should_log: bool = True) -> None:
    """
    Eagerly initialize the SuggestionService in a background thread.

    The sentence-transformers model takes ~2-3s to load. By preloading at
    startup, the first Explorer suggestion request is fast instead of
    waiting for lazy initialization.
    """
    import threading

    def _load():
        try:
            from src.services.embedding import SuggestionService
            service = SuggestionService()
            service._ensure_initialized()
            # Warm up the model with a throwaway query so all lazy internals are ready
            if service.is_available:
                service.get_suggestions("hostname", limit=1)
                logger.info("Suggestion service preloaded (model warm)")
            else:
                logger.warning(f"Suggestion service not available: {service._unavailable_reason}")
            app.suggestion_service = service
        except Exception as e:
            logger.warning(f"Suggestion service preload failed (non-fatal): {e}")
            app.suggestion_service = None

    thread = threading.Thread(target=_load, name="suggestion-preload", daemon=True)
    thread.start()
    if should_log:
        logger.info("Suggestion service preload started (background thread)")


def _register_blueprints(app: Flask, should_log: bool = True) -> None:
    """Register all Flask blueprints"""
    if should_log:
        logger.info("Registering blueprints...")

    # Register core blueprints
    from src.api.blueprints import auth_bp, tag_rules_bp, assets_bp, dashboard_bp, health_bp, charts_bp, search_bp, suggestions_bp
    from src.api.blueprints.reports import reports_bp

    app.register_blueprint(auth_bp)
    if should_log:
        logger.info("Authentication blueprint registered")

    from src.api.blueprints.account import account_bp
    app.register_blueprint(account_bp)
    if should_log:
        logger.info("Account blueprint registered")

    from src.api.blueprints.teams_bot import teams_bot_bp
    app.register_blueprint(teams_bot_bp)
    if should_log:
        logger.info("Teams bot blueprint registered")

    app.register_blueprint(tag_rules_bp)
    if should_log:
        logger.info("Tag rules blueprint registered")

    app.register_blueprint(assets_bp)
    if should_log:
        logger.info("Assets blueprint registered")

    from src.api.blueprints.asset_edit import asset_edit_bp
    app.register_blueprint(asset_edit_bp)
    if should_log:
        logger.info("Asset edit blueprint registered")

    app.register_blueprint(dashboard_bp)
    if should_log:
        logger.info("Dashboard blueprint registered")

    app.register_blueprint(charts_bp)
    if should_log:
        logger.info("Charts blueprint registered")

    app.register_blueprint(reports_bp)
    if should_log:
        logger.info("Reports blueprint registered")

    app.register_blueprint(health_bp)
    if should_log:
        logger.info("Health blueprint registered")

    from src.api.blueprints.reference_data import reference_data_bp
    app.register_blueprint(reference_data_bp)
    if should_log:
        logger.info("Reference data blueprint registered")

    app.register_blueprint(search_bp)
    if should_log:
        logger.info("Search blueprint registered")

    app.register_blueprint(suggestions_bp)
    if should_log:
        logger.info("Suggestions blueprint registered")

    # Register settings blueprint
    from src.api.blueprints.settings import settings_bp
    app.register_blueprint(settings_bp)
    if should_log:
        logger.info("Settings blueprint registered")

    from src.api.blueprints.settings.teams_settings_routes import (
        teams_settings_bp,
    )
    app.register_blueprint(teams_settings_bp)
    if should_log:
        logger.info("Teams settings blueprint registered")

    # Register integration blueprints
    from src.api.blueprints.integrations import (
        connectors_bp,
        integrations_bp,
        schedules_bp,
        batches_bp,
        asset_generation_bp,
        preview_bp,
        integration_groups_bp
    )

    # All integration blueprints use /api/integrations as base URL
    app.register_blueprint(connectors_bp, url_prefix='/api/integrations')
    if should_log:
        logger.info("Connectors blueprint registered")

    app.register_blueprint(integrations_bp, url_prefix='/api/integrations')
    if should_log:
        logger.info("Integrations CRUD blueprint registered")

    app.register_blueprint(schedules_bp, url_prefix='/api/integrations')
    if should_log:
        logger.info("Schedules blueprint registered")

    app.register_blueprint(batches_bp, url_prefix='/api/integrations')
    if should_log:
        logger.info("Batches blueprint registered")

    app.register_blueprint(asset_generation_bp, url_prefix='/api/integrations')
    if should_log:
        logger.info("Asset generation blueprint registered")

    app.register_blueprint(preview_bp, url_prefix='/api/integrations')
    if should_log:
        logger.info("Preview cache blueprint registered")

    app.register_blueprint(integration_groups_bp, url_prefix='/api/integrations')
    if should_log:
        logger.info("Integration groups blueprint registered")

    # Register pipeline blueprint
    from src.api.blueprints.pipeline import pipeline_bp
    app.register_blueprint(pipeline_bp, url_prefix='/api/pipeline')
    if should_log:
        logger.info("Pipeline blueprint registered")

    # Register scheduler monitoring blueprint
    from src.api.blueprints.scheduler_monitoring import scheduler_monitoring_bp
    app.register_blueprint(scheduler_monitoring_bp, url_prefix='/api/scheduler')
    if should_log:
        logger.info("Scheduler monitoring blueprint registered")

    # Register log management blueprint
    from src.api.blueprints.log_management import log_management_bp
    app.register_blueprint(log_management_bp)
    if should_log:
        logger.info("Log management blueprint registered")

    # Register monitoring blueprint — always registered. MonitoringService is
    # always attached (above), and the proxy routes gate themselves on the runtime
    # `monitoring.dashboards` setting (404 when disabled, matching the old
    # "feature not enabled" contract), so the dashboards can be toggled from
    # Settings -> Monitoring without a restart.
    from src.api.blueprints.monitoring import monitoring_bp
    app.register_blueprint(monitoring_bp)
    if should_log:
        logger.info("Monitoring blueprint registered")

    # Register alerts blueprint
    from src.api.blueprints import alerts_bp
    app.register_blueprint(alerts_bp)
    if should_log:
        logger.info("Alerts blueprint registered")

    # Register alert rules blueprint (/api/alert-rules CRUD)
    from src.api.blueprints.alert_rules import alert_rules_bp
    app.register_blueprint(alert_rules_bp)
    if should_log:
        logger.info("Alert rules blueprint registered")

    # Register alert delivery channels blueprint (/api/alerts/channels + rule routing)
    from src.api.blueprints.alert_channels import alert_channels_bp
    app.register_blueprint(alert_channels_bp)
    if should_log:
        logger.info("Alert channels blueprint registered")

    # Register metrics forwarding config blueprint (#922 P2b — /api/metrics-forwarding)
    from src.api.blueprints.metrics_forwarding import metrics_forwarding_bp
    app.register_blueprint(metrics_forwarding_bp)
    if should_log:
        logger.info("Metrics forwarding blueprint registered")

    # Register audit trail blueprint
    from src.api.blueprints.audit_trail import audit_trail_bp
    app.register_blueprint(audit_trail_bp)
    if should_log:
        logger.info("Audit trail blueprint registered")

    # Register enrichment blueprint
    from src.api.blueprints.enrichment import enrichment_bp
    app.register_blueprint(enrichment_bp)
    if should_log:
        logger.info("Enrichment blueprint registered")

    # Admin blueprints
    from src.api.blueprints.admin_embedding import admin_embedding_bp
    app.register_blueprint(admin_embedding_bp)
    if should_log:
        logger.info("Admin embedding blueprint registered")

    # Field registry settings blueprint
    from src.api.blueprints.settings.field_registry_routes import field_registry_bp
    app.register_blueprint(field_registry_bp)
    if should_log:
        logger.info("Field registry blueprint registered")

    # API key management blueprint
    from src.api.blueprints.api_keys import api_keys_bp
    app.register_blueprint(api_keys_bp)
    if should_log:
        logger.info("API keys blueprint registered")

    # Unified fields blueprint
    from src.api.blueprints.unified_fields import unified_fields_bp
    app.register_blueprint(unified_fields_bp)
    if should_log:
        logger.info("Unified fields blueprint registered")

    # External query API blueprint
    from src.api.blueprints.external.assets import external_assets_bp
    app.register_blueprint(external_assets_bp)
    if should_log:
        logger.info("External assets query blueprint registered")

    # Posture rules blueprint
    from src.api.blueprints.posture_rules import posture_rules_bp
    app.register_blueprint(posture_rules_bp)
    if should_log:
        logger.info("Posture rules blueprint registered")

    # Chat blueprint is always registered. A blueprint-level before_request
    # 503s user-facing routes when chat_service is None so admins can still
    # reach /admin/config to configure a provider.
    from src.api.blueprints.chat.routes import chat_bp
    app.register_blueprint(chat_bp)
    if should_log:
        logger.info(
            "Chat blueprint registered (chat_service=%s)",
            "ready" if getattr(app, "chat_service", None) else "unconfigured",
        )

    # Saved Explorer filters blueprint (sets its own url_prefix)
    from src.api.blueprints.saved_filters.routes import saved_filters_bp
    app.register_blueprint(saved_filters_bp)
    if should_log:
        logger.info("Saved filters blueprint registered")

    # Merge denylist admin CRUD blueprint (sets its own url_prefix)
    from src.api.blueprints.merge_denylist import merge_denylist_bp
    app.register_blueprint(merge_denylist_bp)
    if should_log:
        logger.info("Merge denylist blueprint registered")


def _register_error_handlers(app: Flask, should_log: bool = True) -> None:
    """Register global error handlers"""

    @app.errorhandler(400)
    def bad_request(error):
        """Handle 400 Bad Request errors"""
        return {
            'error': 'Bad Request',
            'message': str(error)
        }, 400

    @app.errorhandler(404)
    def not_found(error):
        """Handle 404 Not Found errors"""
        return {
            'error': 'Not Found',
            'message': 'Resource not found'
        }, 404

    @app.errorhandler(500)
    def internal_error(error):
        """Handle 500 Internal Server Error"""
        logger.error(f"Internal server error: {error}", exc_info=True)
        return {
            'error': 'Internal Server Error',
            'message': 'An unexpected error occurred'
        }, 500

    if should_log:
        logger.info("Error handlers registered")


def _init_scheduler(app: Flask, should_log: bool = True) -> None:
    """
    Initialize and start scheduler service based on SCHEDULER_MODE.

    Modes:
        'disabled'  - Scheduler completely disabled
        'embedded'  - Scheduler runs in web process (development only)
        'worker'    - Should not be called - dedicated worker uses separate script
    """
    scheduler_mode = app.config.get('SCHEDULER_MODE', 'embedded')

    if scheduler_mode == 'disabled':
        if should_log:
            logger.info("Scheduler disabled (SCHEDULER_MODE=disabled)")
        return

    if scheduler_mode == 'worker':
        if should_log:
            logger.warning(
                "SCHEDULER_MODE=worker detected in web process. "
                "This mode should only be used in the dedicated scheduler worker script. "
                "Skipping scheduler initialization."
            )
        return

    if scheduler_mode == 'embedded':
        # Determine if we should start the scheduler in this process.
        # In debug mode with auto-reload, only start in the child process
        # (WERKZEUG_RUN_MAIN='true'). In non-debug mode, always start.
        # With --no-reload, WERKZEUG_RUN_MAIN is never set — single process, safe to start.
        reloader_disabled = '--no-reload' in sys.argv
        should_start = not app.debug or os.getenv('WERKZEUG_RUN_MAIN') == 'true' or reloader_disabled

        if should_start:
            app.scheduler_service.start()
            if should_log:
                logger.info("Scheduler service started in embedded mode (SCHEDULER_MODE=embedded)")
                logger.warning(
                    "⚠️  Embedded scheduler mode is for DEVELOPMENT ONLY. "
                    "In production, use separate scheduler worker container with SCHEDULER_MODE=worker"
                )

            # Sync pipeline schedule from settings and register periodic re-sync
            pipeline_service = getattr(app, 'pipeline_service', None)
            if pipeline_service:
                pipeline_service.sync_schedule(app.scheduler_service.scheduler)

                from apscheduler.triggers.interval import IntervalTrigger
                app.scheduler_service.scheduler.add_job(
                    func=lambda: pipeline_service.sync_schedule(app.scheduler_service.scheduler),
                    trigger=IntervalTrigger(minutes=5),
                    id='pipeline_schedule_resync',
                    name='Pipeline Schedule Re-sync',
                    replace_existing=True,
                    coalesce=True,
                    max_instances=1
                )
    else:
        if should_log:
            logger.error(f"Unknown SCHEDULER_MODE: {scheduler_mode}. Valid values: disabled, embedded, worker")
        return


def _init_swagger(app: Flask, should_log: bool = True) -> None:
    """
    Initialize Swagger/OpenAPI documentation.

    This provides interactive API documentation at /api/docs using Swagger UI.
    The OpenAPI spec is automatically generated from endpoint docstrings and decorators.
    """
    # --- Intentional design decision (#850): /api/docs is public ---
    # The Swagger UI (/api/docs) and the OpenAPI schema it serves
    # (/api/apispec.json) are deliberately exposed WITHOUT an auth guard.
    # This is a conscious, low-risk posture choice, not an oversight:
    # the endpoint publishes only the API *schema and UI* (route shapes
    # and parameters), never asset data. Every data-bearing route is
    # individually protected by @jwt_required (250+ decorators across
    # src/api/blueprints/), so exposing the schema aids documentation,
    # not unauthorized access. Deployments that must hide it can block
    # /api/docs and /api/apispec.json at the reverse proxy instead.
    swagger_config = {
        "headers": [],
        "specs": [
            {
                "endpoint": 'apispec',
                "route": '/api/apispec.json',
                "rule_filter": lambda rule: True,  # Include all endpoints
                "model_filter": lambda tag: True,  # Include all models
            }
        ],
        "static_url_path": "/flasgger_static",
        "swagger_ui": True,
        "specs_route": "/api/docs"
    }

    swagger_template = {
        "swagger": "2.0",  # Flasgger uses Swagger 2.0 by default
        "info": {
            "title": app.config.get('APP_NAME', 'AssetAtlas Backend API'),
            "description": "AssetAtlas Backend API for IT asset management and tracking. "
                          "Provides endpoints for managing assets, tags, integrations, and dashboards.",
            "version": app.config.get('APP_VERSION', '1.0.0'),
            "contact": {
                "name": "AssetAtlas Maintainers",
            },
            "license": {
                "name": "AGPL-3.0-or-later",
                "url": "https://www.gnu.org/licenses/agpl-3.0.html",
            },
        },
        "host": f"localhost:{app.config.get('PORT', 5001)}",  # Will be overridden in production
        "basePath": "/",
        "schemes": ["http", "https"],
        "tags": [
            {"name": "Health", "description": "Health check and monitoring endpoints"},
            {"name": "Authentication", "description": "User authentication and authorization endpoints"},
            {"name": "Assets", "description": "Asset management endpoints"},
            {"name": "Tag Rules", "description": "Tag rule management endpoints"},
            {"name": "Dashboard", "description": "Dashboard and analytics endpoints"},
            {"name": "Search", "description": "Explorer search functionality (synonyms, suggestions)"},
            {"name": "Integrations", "description": "Integration management endpoints"},
            {"name": "Connectors", "description": "Connector discovery and schema endpoints"},
            {"name": "Schedules", "description": "Schedule management endpoints"},
            {"name": "Batches", "description": "Batch execution and management endpoints"},
            {"name": "Asset Generation", "description": "Asset generation and processing endpoints"},
            {"name": "Pipeline", "description": "Pipeline execution and monitoring endpoints"}
        ],
        "securityDefinitions": {
            "BearerAuth": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": "JWT Bearer token authentication. Format: 'Bearer {token}'"
            },
            "ApiKeyAuth": {
                "type": "apiKey",
                "name": "X-API-Key",
                "in": "header",
                "description": "API key for authentication (legacy, deprecated)"
            }
        },
        "security": [
            {"BearerAuth": []}
        ]
    }

    Swagger(app, config=swagger_config, template=swagger_template)

    if should_log:
        logger.info("Swagger/OpenAPI documentation initialized at /api/docs")
