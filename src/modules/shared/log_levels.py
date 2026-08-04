# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configured log level (a string) -> logging level number.

One guarded resolver for every place a level arrives as configuration: an
environment variable, a Flask config key, a ``system_settings`` row, a connector
argument.

With one deliberate exception. ``src/teams_bridge/__main__.py`` keeps its own
copy of the allow-list below, because ``Dockerfile.teams-bridge`` copies only
``src/teams_bridge/`` into the bridge image -- importing this module resolves in
dev and then raises ``ModuleNotFoundError`` at container start in production.
That duplication is across a deployment boundary, not drift; if the allow-list
changes here it has to change there too. The check that they agree lives in
``tests/unit/teams_bridge/test_log_level_guard.py``, which CI does not run.

``getattr(logging, value.upper(), default)`` is the obvious way to do this, and
it is a trap: the logging module carries public attributes that are not levels,
and getattr resolves those *successfully*, to something unusable.

* ``NOTSET`` -> ``0``. Below every threshold, and on a non-root logger it means
  "delegate to my parent" rather than "log everything". Either way the operator
  does not get the level they configured -- the invisible-log class of bug that
  #1176 fixed for health-check requests.
* ``BASIC_FORMAT`` -> ``'%(levelname)s:%(name)s:%(message)s'``, a *string*.
  ``logger.log()`` then raises ``TypeError: level must be an integer`` and
  ``setLevel()``/``basicConfig(level=...)`` raise ``ValueError: Unknown level``,
  so a configured value can take a process down -- in the per-request case, on
  every request.

Stdlib imports only, deliberately: this is imported from ``src/api/**`` and
``src/modules/**`` alike, including processes that never build a Flask app.
``src/api/utils/logging_config.py`` is the obvious-looking home and is the wrong
one -- it imports Flask at module scope, and so does ``src/api/utils/__init__.py``
by re-exporting three Flask/Pydantic modules, so anything under there costs every
importer a web framework.

Usage:
    from src.modules.shared.log_levels import resolve_log_level

    level = resolve_log_level(os.getenv('LOG_LEVEL'), warn=True)
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Level names an operator may configure, mapped to the logging module's numbers.
# An explicit allow-list rather than a getattr lookup -- see the module docstring
# for the two attributes getattr resolves wrongly.
#
# NOTSET is left out deliberately, not by omission. It is a real logging
# attribute and would resolve, but it means 0, which is not a threshold anything
# can be logging *at*. Someone who types NOTSET into a config field wants a
# level, and would get either everything (root logger) or their parent's level
# (any other logger) instead.
_LEVELS_BY_NAME = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARN': logging.WARNING,      # alias the stdlib keeps for compatibility
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'FATAL': logging.CRITICAL,    # ditto
    'CRITICAL': logging.CRITICAL,
}


def resolve_log_level(value: Any, default: int = logging.INFO, *, warn: bool = False) -> int:
    """Resolve a configured log-level name to its logging level number.

    Args:
        value: The configured value. A level name, matched case-insensitively
            and with surrounding whitespace stripped. Anything else -- an
            unrecognised name, 'NOTSET', 'BASIC_FORMAT', a non-string, None --
            yields ``default``.
        default: Level number to fall back to. INFO, so a bad setting leaves a
            service audible rather than silent.
        warn: Log a warning naming the rejected value and the level used
            instead. Keyword-only.

    Returns:
        A logging level number, always safe to hand to ``setLevel()``,
        ``basicConfig(level=...)`` or ``logger.log()``.

    ``warn`` is a flag rather than always-on because the callers differ in how
    often they run. Resolving once at startup (``LOG_LEVEL``, ``setup_logging()``)
    should say something: that warning is the only clue an operator gets that
    their .env has a typo in it. The request_id middleware resolves its
    health-check level on *every request*, and there an unconditional warning
    would turn one bad setting into a log line per request. Keep the asymmetry --
    do not "simplify" this into a warning that always fires.
    """
    if isinstance(value, str):
        level = _LEVELS_BY_NAME.get(value.strip().upper())
        if level is not None:
            return level

    if warn:
        logger.warning(
            "Unrecognised log level %r; using %s instead",
            value, logging.getLevelName(default)
        )

    return default
