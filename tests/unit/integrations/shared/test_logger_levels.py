# SPDX-License-Identifier: AGPL-3.0-or-later
"""setup_logging()/configure_root_logger() resolve their level through the
guarded helper (call sites 3 and 4).

Both used to call ``getattr(logging, log_level.upper())`` with **no fallback at
all**, so a configured value could:

* raise ``AttributeError`` -- an unrecognised name ('INF0'), or a non-string,
  which fails on ``.upper()`` before getattr even runs;
* raise ``ValueError: Unknown level`` -- 'BASIC_FORMAT' resolves to a format
  *string*, which setLevel()/basicConfig() reject;
* quietly set level 0 -- 'NOTSET' resolves, and on a named logger that means
  "delegate to my parent", not the level the operator asked for.

Crashing a connector because someone typed 'INF0' helps nobody, so both now fall
back to INFO and log why. DB-free.

Both functions under test mutate global logging state, and pytest's own capture
machinery lives in that same state -- see ``_restored_handlers`` and
``_setup_probe`` for the two traps that makes.
"""

import contextlib
import logging

import pytest

from src.modules.integrations.shared.logger import configure_root_logger, setup_logging


_HELPER_LOGGER = 'src.modules.shared.log_levels'

#: Not a real level, so "still at the sentinel" distinguishes "the call set
#: INFO" from "the call never got as far as setting anything".
_SENTINEL_LEVEL = 43

#: Third-party loggers both functions clamp to WARNING as a side effect.
_NOISY_LOGGERS = ('urllib3', 'requests', 'connectionpool')

_PROBE_NAME = 'tests.integrations_shared_logger_probe'


@contextlib.contextmanager
def _restored_handlers(*loggers):
    """Restore each logger's handlers member by member, not by re-assigning.

    pytest attaches its LogCaptureHandlers at the start of every test *phase* and
    detaches them at the end, so a handler list snapshotted in one phase and
    assigned back in another leaves dead handlers on the logger for the rest of
    the session. Adding back only what is missing, and removing only what was
    added, restores the live set exactly. Levels are plain numbers, so those are
    safe to snapshot and assign.
    """
    saved = [(logger, list(logger.handlers), logger.level, logger.propagate)
             for logger in loggers]
    try:
        yield
    finally:
        for logger, handlers, level, propagate in saved:
            for added in [h for h in logger.handlers if h not in handlers]:
                logger.removeHandler(added)
            for missing in [h for h in handlers if h not in logger.handlers]:
                logger.addHandler(missing)
            logger.setLevel(level)
            logger.propagate = propagate


@pytest.fixture(autouse=True)
def _restore_noisy_logger_levels():
    saved = [(logging.getLogger(name), logging.getLogger(name).level)
             for name in _NOISY_LOGGERS]
    try:
        yield
    finally:
        for logger, level in saved:
            logger.setLevel(level)


@pytest.fixture
def probe_logger():
    """A throwaway logger for setup_logging() to configure, plus teardown."""
    probe = logging.getLogger(_PROBE_NAME)
    try:
        yield probe
    finally:
        for handler in list(probe.handlers):
            probe.removeHandler(handler)
        probe.setLevel(logging.NOTSET)
        probe.propagate = True
        logging.Logger.manager.loggerDict.pop(_PROBE_NAME, None)


def _setup_probe(log_level):
    """Call setup_logging() on a logger it will actually configure.

    setup_logging() returns the logger untouched when ``hasHandlers()`` is true,
    and under pytest that is the default state of *every* logger: the capture
    handlers sit on root and hasHandlers() walks up to them. ``propagate = False``
    alone does not help -- pytest also attaches those handlers directly to every
    non-propagating logger it can see when the phase starts
    (``catching_logs.__enter__`` in _pytest/logging.py), so a logger made
    non-propagating in a *fixture* comes back with handlers of its own. Both have
    to be undone here, in the test body, after that has happened.
    """
    probe = logging.getLogger(_PROBE_NAME)
    probe.handlers.clear()
    probe.propagate = False
    assert not probe.hasHandlers(), 'setup_logging() would no-op, test proves nothing'

    return setup_logging(name=_PROBE_NAME, log_level=log_level)


def _configure_root(log_level):
    """Run configure_root_logger() against a bare root logger and return the
    level it ended up at.

    ``logging.basicConfig()`` does nothing at all -- including not setting the
    level -- when root already has handlers, and under pytest it always does.
    The NullHandler keeps the helper's warning from falling through to
    ``lastResort`` and printing to stderr while root is bare.
    """
    root = logging.getLogger()
    helper = logging.getLogger(_HELPER_LOGGER)

    with _restored_handlers(root, helper):
        root.handlers.clear()
        root.setLevel(_SENTINEL_LEVEL)
        helper.addHandler(logging.NullHandler())

        configure_root_logger(log_level)
        return root.level


class TestSetupLogging:
    """Site 3: ``logger.setLevel(...)`` on a named connector logger."""

    @pytest.mark.parametrize('configured,expected', [
        ('DEBUG', logging.DEBUG),
        ('INFO', logging.INFO),
        ('warning', logging.WARNING),
        ('WARN', logging.WARNING),
        ('FATAL', logging.CRITICAL),
    ])
    def test_recognised_level_is_applied(self, configured, expected, probe_logger):
        assert _setup_probe(configured).level == expected

    @pytest.mark.parametrize('configured', ['INF0', 'NOTSET', 'BASIC_FORMAT', '', None, 123])
    def test_unusable_level_falls_back_to_info_without_raising(self, configured, probe_logger):
        assert _setup_probe(configured).level == logging.INFO

    def test_unusable_level_is_logged_with_the_offending_value(self, probe_logger, caplog):
        with caplog.at_level(logging.DEBUG, logger=_HELPER_LOGGER):
            _setup_probe('INF0')

        records = [r for r in caplog.records if r.name == _HELPER_LOGGER]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert 'INF0' in records[0].getMessage()


class TestConfigureRootLogger:
    """Site 4: ``basicConfig(level=...)`` on the root logger."""

    @pytest.mark.parametrize('configured,expected', [
        ('DEBUG', logging.DEBUG),
        ('warn', logging.WARNING),
    ])
    def test_recognised_level_is_applied(self, configured, expected):
        assert _configure_root(configured) == expected

    @pytest.mark.parametrize('configured', ['INF0', 'NOTSET', 'BASIC_FORMAT', '', None])
    def test_unusable_level_falls_back_to_info_without_raising(self, configured):
        assert _configure_root(configured) == logging.INFO

    def test_unusable_level_is_logged_with_the_offending_value(self, caplog):
        """Deliberately not via ``_configure_root``: the warning has to reach
        pytest's capture handler, which lives on root. basicConfig() then no-ops,
        which is fine -- the warning is all this asserts on."""
        with caplog.at_level(logging.DEBUG, logger=_HELPER_LOGGER):
            configure_root_logger('BASIC_FORMAT')

        records = [r for r in caplog.records if r.name == _HELPER_LOGGER]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert 'BASIC_FORMAT' in records[0].getMessage()
