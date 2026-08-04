# SPDX-License-Identifier: AGPL-3.0-or-later
"""Health-check request logging in request_id middleware (#1140).

The middleware logs requests to HEALTH_CHECK_PATHS at a configurable level and
everything else at INFO. That level used to default to DEBUG, which is below the
app's normal INFO threshold — so on a fresh install health probes produced no
request-log line at all, exactly when an operator is trying to prove that probes
reach the API. The default is now INFO everywhere the middleware can fall back:
the module-level cache seed, the settings_manager fallback argument, and
`_resolve_health_check_level()`, which catches a stored value that is not a
usable logging level name.

DB-free: builds a bare Flask app and drives real requests through it.
"""

import importlib
import logging

import pytest
from flask import Flask

from src.api.middleware import request_id as request_id_module
from src.api.middleware.request_id import (
    _refresh_logging_settings,
    init_request_id_middleware,
)


_UNSET = object()


class _StubSettingsManager:
    """Stand-in for ``app.settings_manager``.

    ``stored`` is the value persisted for ``logging.health_check_log_level``.
    ``_UNSET`` means no value was ever stored, in which case the real manager
    hands the caller's own default back — the path the fallback argument in
    ``_refresh_logging_settings`` exists for.
    """

    def __init__(self, stored=_UNSET):
        self.stored = stored

    def get(self, category, key, default=None):
        if (category, key) == ('logging', 'health_check_log_level') and self.stored is not _UNSET:
            return self.stored
        if (category, key) == ('logging', 'slow_request_threshold_ms'):
            # Never let a slow test runner promote 'Request completed' to
            # WARNING — that path is not what these tests are asserting on.
            return 3_600_000
        return default


class _ListHandler(logging.Handler):
    """Collects LogRecords so a DEBUG record is *captured* rather than dropped —
    without that we could not tell 'logged at DEBUG' from 'not logged'."""

    def __init__(self, records):
        super().__init__(level=logging.DEBUG)
        self.records = records

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def captured_records():
    """Capture everything the middleware logs, independent of root/handler config."""
    records = []
    handler = _ListHandler(records)
    logger = logging.getLogger('src.api.middleware.request_id')
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


@pytest.fixture
def logging_cache():
    """Snapshot/restore the module-level settings cache — it is global state
    shared with every other test in the session."""
    cache = request_id_module._logging_settings_cache
    original = dict(cache)
    try:
        yield cache
    finally:
        cache.clear()
        cache.update(original)


def _make_app(stored_level=_UNSET):
    """Bare Flask app with the middleware installed. No DB, no JWT manager."""
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.settings_manager = _StubSettingsManager(stored_level)

    @app.route('/api/health')
    def health():
        return {'status': 'ok'}

    @app.route('/api/assets')
    def assets():
        return {'assets': []}

    init_request_id_middleware(app)
    return app


#: The two per-request lines. The middleware also logs its own init line at
#: INFO from the same logger, which is not part of what we assert on.
_REQUEST_LINES = ('Request started', 'Request completed')


def _levels(records):
    """Map log message -> level number for the two per-request lines."""
    return {
        record.getMessage(): record.levelno
        for record in records
        if record.getMessage() in _REQUEST_LINES
    }


def _get(app, path, cache):
    """Drive one request, forcing a settings refresh first (TTL is 60s)."""
    cache['last_refresh'] = 0
    with app.test_client() as client:
        return client.get(path)


class TestDefaultsWhenNothingIsStored:
    """Nothing stored anywhere -> INFO, so health probes show up in the log."""

    def test_module_cache_seed_is_info(self):
        """The seed is what every request uses until the first successful
        refresh — i.e. all requests a fresh worker serves before it has read
        settings. Reloaded so the assertion sees the literal in the source and
        not whatever an earlier test left in the shared cache."""
        module = importlib.reload(request_id_module)
        assert module._logging_settings_cache['health_check_log_level'] == 'INFO'

    def test_refresh_falls_back_to_info_when_no_value_is_stored(self, logging_cache):
        app = _make_app()  # settings_manager returns the caller's default
        logging_cache['health_check_log_level'] = 'sentinel'
        logging_cache['last_refresh'] = 0

        _refresh_logging_settings(app)

        assert logging_cache['health_check_log_level'] == 'INFO'

    def test_health_check_request_logs_at_info(self, logging_cache, captured_records):
        response = _get(_make_app(), '/api/health', logging_cache)

        assert response.status_code == 200
        assert _levels(captured_records) == {
            'Request started': logging.INFO,
            'Request completed': logging.INFO,
        }


class TestUnparseableStoredValue:
    """A stored value that is not a logging level name (corrupted row, hand-edited
    DB) must not silently re-introduce the invisible-probe bug."""

    # 'NOTSET' and 'BASIC_FORMAT' are the two that a plain
    # ``getattr(logging, value.upper(), logging.INFO)`` resolves *successfully*
    # and wrongly: to 0 (silently below every threshold — the bug this default
    # exists to prevent) and to a format string (logger.log then raises
    # "level must be an integer" on every request, not just health probes).
    @pytest.mark.parametrize(
        'stored',
        ['VERBOSE', '', 'debugg', 'TRACE', 'NOTSET', 'notset', 'BASIC_FORMAT', None],
    )
    def test_unparseable_value_resolves_to_info(self, stored, logging_cache, captured_records):
        _get(_make_app(stored), '/api/health', logging_cache)

        assert _levels(captured_records) == {
            'Request started': logging.INFO,
            'Request completed': logging.INFO,
        }


class TestOperatorChoiceWins:
    """The new default must not override a level an operator actually chose."""

    @pytest.mark.parametrize('stored,expected', [
        ('DEBUG', logging.DEBUG),
        ('WARNING', logging.WARNING),
        ('INFO', logging.INFO),
    ])
    def test_stored_level_is_honoured(self, stored, expected, logging_cache, captured_records):
        _get(_make_app(stored), '/api/health', logging_cache)

        assert _levels(captured_records) == {
            'Request started': expected,
            'Request completed': expected,
        }


class TestNonHealthPathsUnaffected:
    """Only the six exact HEALTH_CHECK_PATHS entries are configurable; every
    other route logs at INFO regardless of the setting."""

    @pytest.mark.parametrize('stored', [_UNSET, 'DEBUG', 'WARNING'])
    def test_regular_request_always_logs_at_info(self, stored, logging_cache, captured_records):
        _get(_make_app(stored), '/api/assets', logging_cache)

        assert _levels(captured_records) == {
            'Request started': logging.INFO,
            'Request completed': logging.INFO,
        }
