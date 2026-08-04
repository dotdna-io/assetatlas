# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Application Configuration Classes

Defines configuration hierarchy for different environments (development, production, testing).
Each configuration class inherits from BaseConfig and overrides settings as needed.

Usage:
    from src.api.config import DevelopmentConfig, ProductionConfig, TestConfig

    config = DevelopmentConfig()
    app.config.from_object(config)
"""

import os
from datetime import timedelta
from pathlib import Path
from typing import Optional


def _read_version() -> str:
    """Read app version: APP_VERSION env var > VERSION file > fallback."""
    env_ver = os.getenv('APP_VERSION')
    if env_ver:
        return env_ver
    version_file = Path(__file__).resolve().parent.parent.parent / 'VERSION'
    try:
        return version_file.read_text().strip()
    except FileNotFoundError:
        return '0.0.0-dev'


class BaseConfig:
    """Base configuration with common settings"""

    # Reported by /api/health as `environment`. Declared on the config CLASSES
    # rather than set from the FLASK_ENV env var, because Flask only copies
    # UPPERCASE class attributes in `app.config.from_object(...)` — nothing was
    # putting FLASK_ENV into app.config at all, so health.py's
    # `current_app.config.get('FLASK_ENV', 'unknown')` fell through to 'unknown'
    # in every deployment, however the environment was configured.
    #
    # Sourcing it from the class means it always names the config ACTUALLY in
    # force. Reading os.getenv('FLASK_ENV') instead would report the *variable*,
    # and the two disagree in two real cases: create_app('test') overrides the
    # env var (how the test suite runs), and get_config() silently falls back to
    # the default class for an unrecognised name — so a typo'd FLASK_ENV=prodction
    # would claim 'prodction' while actually running DevelopmentConfig.
    FLASK_ENV = 'development'

    # Application settings
    APP_NAME = "AssetAtlas Backend API"
    APP_VERSION = _read_version()

    # Flask settings — SECRET_KEY must be supplied via env. No default: a
    # known value would be reused across forks and grant any holder the
    # ability to forge session cookies. Per-env classes validate at init.
    SECRET_KEY = os.getenv('SECRET_KEY')
    JSON_SORT_KEYS = False  # Preserve JSON key order

    # Database settings
    # ⚠️ DATABASE_URL must be provided via environment variable
    # No default credentials are provided for security reasons
    # Set via: export DATABASE_URL="postgresql://user:password@host:port/database"
    DATABASE_URL = os.getenv('DATABASE_URL')

    # Database connection pooling settings
    # Reduced from 50+50 to prevent connection exhaustion
    DB_POOL_SIZE = int(os.getenv('DB_POOL_SIZE', '10'))  # Base pool size (production default)
    DB_MAX_OVERFLOW = int(os.getenv('DB_MAX_OVERFLOW', '10'))  # Additional connections beyond pool
    DB_POOL_TIMEOUT = int(os.getenv('DB_POOL_TIMEOUT', '30'))  # Seconds to wait for connection
    DB_POOL_RECYCLE = int(os.getenv('DB_POOL_RECYCLE', '3600'))  # Recycle connections after 1 hour

    # CORS settings
    CORS_ORIGINS = os.getenv('CORS_ORIGINS', 'http://localhost:3000,http://localhost:5002').split(',')

    # Logging settings
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Tag rules settings
    TAG_RULES_AUTO_EXECUTE = os.getenv('TAG_RULES_AUTO_EXECUTE', 'true').lower() == 'true'

    # Posture rules settings — when true, CUD on a sub-rule triggers a
    # single-category re-evaluation so the UI stats update without waiting
    # for the scheduled evaluate_all. Scoped eval is cheap; disable only if
    # you want mutations to be strictly write-only.
    POSTURE_RULES_AUTO_EXECUTE = os.getenv('POSTURE_RULES_AUTO_EXECUTE', 'true').lower() == 'true'

    # Pagination settings
    DEFAULT_PAGE_SIZE = int(os.getenv('DEFAULT_PAGE_SIZE', '50'))
    MAX_PAGE_SIZE = int(os.getenv('MAX_PAGE_SIZE', '200'))

    # API settings
    API_PREFIX = '/api'
    API_TITLE = 'AssetAtlas Backend API'
    API_DESCRIPTION = 'REST API for AssetAtlas IT asset management platform'

    # Redis settings
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

    # Cache settings
    CACHE_TYPE = os.getenv('CACHE_TYPE', 'redis')  # Options: redis, simple, null
    CACHE_REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    CACHE_DEFAULT_TIMEOUT = int(os.getenv('CACHE_DEFAULT_TIMEOUT', '300'))  # 5 minutes default
    CACHE_KEY_PREFIX = 'assetatlas:'

    # JWT Authentication settings
    # JWT_SECRET_KEY should be different from Flask SECRET_KEY for security
    # isolation. Like SECRET_KEY, no default — a known value would let anyone
    # forge auth tokens. Per-env classes validate at init.
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY')

    # Token expiration times
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=int(os.getenv('JWT_ACCESS_TOKEN_EXPIRES_MINUTES', '60')))
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=int(os.getenv('JWT_REFRESH_TOKEN_EXPIRES_DAYS', '7')))

    # Token location — headers only. The cookie path is intentionally disabled:
    # the API only ever sets bearer tokens in the Authorization header, so
    # accepting cookie-based JWTs would only widen the attack surface (e.g.
    # CSRF on a future subdomain misconfig, or an XSS that stuffs a cookie).
    JWT_TOKEN_LOCATION = ['headers']
    JWT_HEADER_NAME = 'Authorization'
    JWT_HEADER_TYPE = 'Bearer'

    # Token blacklist/revocation settings
    JWT_BLACKLIST_ENABLED = True
    JWT_BLACKLIST_TOKEN_CHECKS = ['access', 'refresh']

    # Error messages
    JWT_ERROR_MESSAGE_KEY = 'message'

    # Embedding system
    EMBEDDING_AUTO_UPDATE = os.getenv('EMBEDDING_AUTO_UPDATE', 'true').lower() == 'true'
    EMBEDDING_UPDATE_TIMEOUT = int(os.getenv('EMBEDDING_UPDATE_TIMEOUT', '120'))

    # Chat feature
    # Provider/model/api_key/base_url/prompt_cache/thinking_enabled live in
    # the chat_providers table per issue #636 — there is no env fallback for
    # those. The feature toggle and per-turn limits below remain ops-driven.
    CHAT_ENABLED = os.getenv('CHAT_ENABLED', 'true').lower() == 'true'
    CHAT_MAX_MESSAGES_PER_USER_PER_DAY = int(os.getenv('CHAT_MAX_MESSAGES_PER_USER_PER_DAY', '200'))
    CHAT_MAX_TOOL_ITERATIONS = int(os.getenv('CHAT_MAX_TOOL_ITERATIONS', '10'))
    CHAT_TURN_TIMEOUT_SECONDS = int(os.getenv('CHAT_TURN_TIMEOUT_SECONDS', '60'))
    # Total wall-clock budget for one chat turn (all tool iterations + the final
    # answer). Distinct from CHAT_TURN_TIMEOUT_SECONDS, which only bounds the gap
    # between consecutive stream events: a long tool fan-out emits events
    # steadily, so the per-event timeout never fires while the total runs for
    # minutes and overruns a public gateway's request timeout (e.g. Cloudflare
    # ~100s), 504-ing. Keep this BELOW that gateway timeout so a slow turn
    # self-aborts with a graceful "turn_timeout" message instead. 0 disables it.
    CHAT_TURN_TOTAL_TIMEOUT_SECONDS = int(
        os.getenv('CHAT_TURN_TOTAL_TIMEOUT_SECONDS', '90'))
    CHAT_MAX_INPUT_TOKENS = int(os.getenv('CHAT_MAX_INPUT_TOKENS', '100000'))
    CHAT_MAX_OUTPUT_TOKENS = int(os.getenv('CHAT_MAX_OUTPUT_TOKENS', '8192'))
    CHAT_RETENTION_DAYS = int(os.getenv('CHAT_RETENTION_DAYS', '90'))

    # Teams bridge — link tokens are signed with a key distinct from
    # JWT_SECRET_KEY so a leak of one doesn't compromise the other. Empty
    # disables the Teams bot endpoints; install.sh generates the value.
    TEAMS_LINK_SECRET_KEY = os.environ.get("TEAMS_LINK_SECRET_KEY", "")
    ASSETATLAS_PUBLIC_BASE_URL = os.environ.get(
        "ASSETATLAS_PUBLIC_BASE_URL", "http://localhost:5001",
    )

    # External API rate limit (requests per minute per API key)
    EXTERNAL_API_RATE_LIMIT = int(os.getenv('EXTERNAL_API_RATE_LIMIT', '60'))

    # Higher per-key budget for the full-inventory pull (GET /api/v1/assets/inventory).
    # The risk-app connector pages quickly (~5 req/s); ~600/min (~10/s) keeps normal
    # full syncs from tripping 429 while retaining an abuse cap.
    EXTERNAL_API_INVENTORY_RATE_LIMIT = int(
        os.getenv('EXTERNAL_API_INVENTORY_RATE_LIMIT', '600')
    )

    # Monitoring overlay — opt-in
    ALERTMANAGER_WEBHOOK_SECRET = os.getenv('ALERTMANAGER_WEBHOOK_SECRET', '')
    MONITORING_ENABLED = os.getenv('MONITORING_ENABLED', 'false').lower() == 'true'
    MONITORING_RULES_DIR = os.getenv('MONITORING_RULES_DIR', 'monitoring/alerts')
    PROMETHEUS_URL = os.getenv('PROMETHEUS_URL', 'http://prometheus:9090')

    # Trusted reverse-proxy configuration
    # When > 0, wraps the WSGI app with werkzeug.middleware.proxy_fix.ProxyFix
    # so that request.remote_addr / request.scheme / request.host reflect the
    # real client values from X-Forwarded-* headers. Set to the number of
    # trusted proxies between the client and Flask (0 = no proxy, 1 = nginx in
    # front of the API, etc.). Default 0 in dev; ProductionConfig overrides.
    TRUSTED_PROXY_HOPS = int(os.getenv('TRUSTED_PROXY_HOPS', '0'))

    # Integration logo uploads — directory where user-uploaded logos are
    # stored and served from by GET /api/integrations/logos/file/<name>.
    # Bundled logos live in ui/prod/public/logos/ (image-baked) and are
    # served by the UI container at /logos/<name>; uploads have to live
    # somewhere persistent that survives container rebuilds. Default points
    # at a project-relative path for local dev; in production this is
    # mounted as a docker volume (see docker-compose.yml).
    UPLOADED_LOGOS_DIR = os.getenv(
        'UPLOADED_LOGOS_DIR',
        str(Path(__file__).resolve().parent.parent.parent / 'data' / 'uploaded-logos'),
    )
    UPLOADED_LOGO_MAX_BYTES = int(os.getenv('UPLOADED_LOGO_MAX_BYTES', str(1024 * 1024)))

    # Scheduler settings
    # SCHEDULER_MODE controls how the scheduler operates:
    #   'disabled'  - Scheduler is completely disabled (e.g., during migrations)
    #   'embedded'  - Scheduler runs inside the web process (development only)
    #   'worker'    - Dedicated scheduler worker mode (production - separate container)
    SCHEDULER_MODE = os.getenv('SCHEDULER_MODE', 'embedded')

    # Legacy compatibility - if SCHEDULER_ENABLED=false, set mode to disabled
    if os.getenv('SCHEDULER_ENABLED', 'true').lower() == 'false':
        SCHEDULER_MODE = 'disabled'

    @classmethod
    def init_app(cls, app):
        """Initialize application with this configuration"""
        pass


class DevelopmentConfig(BaseConfig):
    """Development environment configuration"""

    FLASK_ENV = 'development'
    DEBUG = True
    TESTING = False

    # Enable Flask debug toolbar
    DEBUG_TB_ENABLED = True
    DEBUG_TB_INTERCEPT_REDIRECTS = False

    # More verbose logging in development
    LOG_LEVEL = 'DEBUG'

    # Connection pool for development (reduced to prevent exhaustion on single worker)
    # Development typically runs single Flask worker, so smaller pool is sufficient
    DB_POOL_SIZE = int(os.getenv('DB_POOL_SIZE', '5'))
    DB_MAX_OVERFLOW = int(os.getenv('DB_MAX_OVERFLOW', '5'))

    @classmethod
    def init_app(cls, app):
        """Development-specific initialization"""
        import logging
        logging.getLogger('werkzeug').setLevel(logging.INFO)

        # Validate required env vars — same set as ProductionConfig minus
        # CORS_ORIGINS (which has a usable dev default in BaseConfig).
        # Dev failing loud here matches prod behavior so misconfigurations
        # surface immediately instead of silently using a known-public secret.
        if not app.config.get('DATABASE_URL'):
            raise ValueError(
                "DATABASE_URL environment variable must be set.\n"
                "Example: export DATABASE_URL='postgresql://user:password@host:port/database'"
            )

        if not app.config.get('SECRET_KEY'):
            raise ValueError(
                "SECRET_KEY environment variable must be set.\n"
                "Generate with: openssl rand -hex 32"
            )

        if not app.config.get('JWT_SECRET_KEY'):
            raise ValueError(
                "JWT_SECRET_KEY environment variable must be set.\n"
                "Generate with: openssl rand -hex 32"
            )

        if not os.getenv('ASSETATLAS_ENCRYPTION_KEY'):
            raise ValueError(
                "ASSETATLAS_ENCRYPTION_KEY environment variable must be set.\n"
                "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )


class ProductionConfig(BaseConfig):
    """Production environment configuration"""

    FLASK_ENV = 'production'
    DEBUG = False
    TESTING = False

    # Production logging
    LOG_LEVEL = 'WARNING'

    # Production: Scheduler runs in separate worker container by default
    # Web containers should set SCHEDULER_MODE='disabled'
    # Scheduler container should set SCHEDULER_MODE='worker'
    SCHEDULER_MODE = os.getenv('SCHEDULER_MODE', 'disabled')

    # Production assumes the API runs behind one reverse-proxy hop (nginx).
    # Override TRUSTED_PROXY_HOPS env var to match your topology.
    TRUSTED_PROXY_HOPS = int(os.getenv('TRUSTED_PROXY_HOPS', '1'))

    @classmethod
    def init_app(cls, app):
        """Production-specific initialization with validation"""
        import logging
        from logging.handlers import RotatingFileHandler

        # Security validation - must happen at init time, not import time
        secret_key = os.getenv('SECRET_KEY')
        if not secret_key:
            raise ValueError("SECRET_KEY environment variable must be set in production")
        app.config['SECRET_KEY'] = secret_key

        jwt_secret_key = os.getenv('JWT_SECRET_KEY')
        if not jwt_secret_key:
            raise ValueError("JWT_SECRET_KEY environment variable must be set in production")
        app.config['JWT_SECRET_KEY'] = jwt_secret_key

        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            raise ValueError("DATABASE_URL environment variable must be set in production")
        app.config['DATABASE_URL'] = database_url

        cors_origins = os.getenv('CORS_ORIGINS', '').split(',')
        if not cors_origins or cors_origins == ['']:
            raise ValueError("CORS_ORIGINS must be explicitly set in production")
        app.config['CORS_ORIGINS'] = cors_origins

        encryption_key = os.getenv('ASSETATLAS_ENCRYPTION_KEY')
        if not encryption_key:
            raise ValueError(
                "ASSETATLAS_ENCRYPTION_KEY must be set in production. "
                "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        app.config['ASSETATLAS_ENCRYPTION_KEY'] = encryption_key

        # Monitoring (opt-in): if enabled, ALERTMANAGER_WEBHOOK_SECRET is required
        if os.getenv('MONITORING_ENABLED', 'false').lower() == 'true':
            webhook_secret = os.getenv('ALERTMANAGER_WEBHOOK_SECRET', '')
            if not webhook_secret:
                raise ValueError(
                    "ALERTMANAGER_WEBHOOK_SECRET must be set when MONITORING_ENABLED=true "
                    "in production. Generate with: openssl rand -hex 32"
                )
            app.config['ALERTMANAGER_WEBHOOK_SECRET'] = webhook_secret

        # Create logs directory if it doesn't exist
        log_dir = os.path.join(os.path.dirname(__file__), '../../logs')
        os.makedirs(log_dir, exist_ok=True)

        # Set up file handler with rotation
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, 'assetatlas-api.log'),
            maxBytes=10485760,  # 10MB
            backupCount=10
        )
        file_handler.setLevel(logging.WARNING)
        file_handler.setFormatter(logging.Formatter(cls.LOG_FORMAT))

        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.WARNING)


class TestConfig(BaseConfig):
    """Testing environment configuration"""

    FLASK_ENV = 'test'
    DEBUG = False
    TESTING = True

    # Use test database - must be set via TEST_DATABASE_URL environment variable
    # Falls back to DATABASE_URL with _test suffix if not set
    DATABASE_URL = os.getenv('TEST_DATABASE_URL') or os.getenv('DATABASE_URL')

    # Minimal connection pool for tests
    DB_POOL_SIZE = int(os.getenv('DB_POOL_SIZE', '2'))
    DB_MAX_OVERFLOW = int(os.getenv('DB_MAX_OVERFLOW', '2'))

    # Use simple in-memory cache for tests (no Redis required)
    CACHE_TYPE = 'simple'

    # Disable auto-execution in tests
    TAG_RULES_AUTO_EXECUTE = False
    POSTURE_RULES_AUTO_EXECUTE = False

    # Disable scheduler in tests
    SCHEDULER_MODE = 'disabled'

    # Disable embedding auto-update in tests
    EMBEDDING_AUTO_UPDATE = False

    # Always register the monitoring blueprint in tests (MonitoringService
    # itself is provided as a MagicMock via _SERVICE_ATTRS).
    MONITORING_ENABLED = True

    # Disable chat in tests — provider config lives in the chat_providers
    # table now (issue #636), not in env. Tests that exercise chat-dependent
    # code construct their own ChatProvidersService stubs.
    CHAT_ENABLED = False

    # Low rate limit for testing
    EXTERNAL_API_RATE_LIMIT = 5

    # Test-specific settings
    WTF_CSRF_ENABLED = False

    # Literals (not os.getenv) so the dev shell SECRET_KEY can't bleed into
    # the test JWT-signing surface — CI and dev exercise identical secrets.
    SECRET_KEY = 'test-secret-key-not-for-production'
    JWT_SECRET_KEY = 'test-jwt-secret-not-for-production'

    @classmethod
    def init_app(cls, app):
        """Test-specific initialization"""
        import logging
        logging.getLogger().setLevel(logging.ERROR)  # Quiet logs in tests


# Configuration mapping
config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'test': TestConfig,
    'default': DevelopmentConfig
}


def get_config(config_name: Optional[str] = None) -> BaseConfig:
    """
    Get configuration class by name.

    Args:
        config_name: Name of configuration ('development', 'production', 'test')
                    If None, uses FLASK_ENV environment variable

    Returns:
        Configuration class instance

    Example:
        config = get_config('development')
        app.config.from_object(config)
    """
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'development')

    return config_map.get(config_name, config_map['default'])
