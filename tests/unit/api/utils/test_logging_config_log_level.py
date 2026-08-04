# SPDX-License-Identifier: AGPL-3.0-or-later
"""LOG_LEVEL is resolved through the guarded helper (call site 2).

``configure_structured_logging()`` feeds the configured LOG_LEVEL straight into
``root_logger.setLevel()``, and compose passes ``LOG_LEVEL: ${LOG_LEVEL:-WARNING}``
to three services -- so whatever an operator types into ``.env`` lands here.
Under the old ``getattr(logging, value.upper(), logging.INFO)``:

* ``BASIC_FORMAT`` resolved to a format *string*, and ``setLevel()`` raises
  ``ValueError: Unknown level`` on it -- i.e. the app does not start;
* a non-string raised ``AttributeError`` on ``.upper()`` -- likewise;
* ``NOTSET`` resolved to 0, which on the *root* logger means no threshold at
  all: every DEBUG line from every dependency, from a value an operator could
  reasonably read as "leave it alone";
* a plain typo did fall back to INFO, but silently -- nothing told the operator
  their setting was being ignored.

DB-free: builds a bare Flask app, no factory and no services.
"""

import contextlib
import logging

import pytest
from flask import Flask

from src.api.utils.logging_config import configure_structured_logging


_HELPER_LOGGER = 'src.modules.shared.log_levels'

#: Not a real level, so "still at the sentinel" distinguishes "the call set
#: INFO" from "the call raised before it set anything".
_SENTINEL_LEVEL = 43

#: Third-party loggers configure_structured_logging() clamps as a side effect.
_CLAMPED_LOGGERS = ('werkzeug', 'anthropic', 'anthropic._base_client', 'httpcore', 'httpx')

#: Fixed app name, so the ``app.logger`` this re-levels and empties is a
#: dedicated throwaway rather than the logger named after this test module.
_APP_NAME = 'logging_config_probe'


@pytest.fixture(autouse=True)
def quiet_logging_env(monkeypatch):
    """No log directories and no console handler: this is about the level, not
    about handler plumbing, and neither should touch the disk."""
    monkeypatch.setenv('LOG_TO_FILES', 'false')
    monkeypatch.setenv('LOG_CONSOLE_ENABLED', 'false')


@contextlib.contextmanager
def _restored_handlers(*loggers):
    """Restore each logger's handlers member by member, not by re-assigning.

    ``configure_structured_logging()`` does ``root_logger.handlers = []``, which
    takes pytest's capture handlers with it -- put them back or logging capture
    is broken for the rest of the session. Restoring by assigning a snapshotted
    list would work here but leaks: pytest attaches those handlers at the start
    of every test *phase* and detaches them at the end, so a list captured in one
    phase and assigned back in another leaves dead handlers on root. Adding back
    only what is missing, and removing only what was added, restores the live set
    exactly.
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


def _configure(log_level):
    """Run the function under test against a bare app and return root's level.

    Read inside the restore, since leaving the block puts root's level back.
    """
    app = Flask(_APP_NAME)
    app.config['TESTING'] = True
    app.config['LOG_LEVEL'] = log_level

    root = logging.getLogger()
    touched = [root, logging.getLogger(_APP_NAME),
               *(logging.getLogger(name) for name in _CLAMPED_LOGGERS)]

    with _restored_handlers(*touched):
        root.setLevel(_SENTINEL_LEVEL)
        configure_structured_logging(app)
        return root.level


class TestConfiguredLevelIsHonoured:
    """An operator's LOG_LEVEL still wins; this refactor changes nothing here."""

    @pytest.mark.parametrize('configured,expected', [
        ('WARNING', logging.WARNING),   # the compose default
        ('warning', logging.WARNING),
        ('DEBUG', logging.DEBUG),
        ('WARN', logging.WARNING),
        ('FATAL', logging.CRITICAL),
        ('  INFO  ', logging.INFO),
    ])
    def test_root_level_matches_the_configured_value(self, configured, expected):
        assert _configure(configured) == expected


class TestUnusableConfiguredValue:
    """A typo in .env must not decide whether the app boots."""

    @pytest.mark.parametrize('configured', ['BASIC_FORMAT', 'NOTSET', 'WARNIGN', '', None, 20])
    def test_falls_back_to_info_without_raising(self, configured):
        assert _configure(configured) == logging.INFO

    def test_notset_does_not_remove_the_root_threshold(self):
        """0 on the root logger is not "unset", it is "log absolutely everything"."""
        assert _configure('NOTSET') != logging.NOTSET

    def test_unusable_value_is_logged_once_with_the_offending_value(self, caplog):
        """This site resolves once at startup, so it opts into the warning -- it
        is the only clue an operator gets that .env has a typo in it."""
        with caplog.at_level(logging.DEBUG, logger=_HELPER_LOGGER):
            _configure('WARNIGN')

        records = [r for r in caplog.records if r.name == _HELPER_LOGGER]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert 'WARNIGN' in records[0].getMessage()
        assert 'INFO' in records[0].getMessage()

    def test_recognised_value_is_not_warned_about(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=_HELPER_LOGGER):
            _configure('WARNING')

        assert [r for r in caplog.records if r.name == _HELPER_LOGGER] == []
