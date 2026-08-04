# SPDX-License-Identifier: AGPL-3.0-or-later
"""The bridge's own log-level guard -- a duplicate of ``resolve_log_level``, on purpose.

``Dockerfile.teams-bridge:34`` copies only ``src/teams_bridge/`` into the image,
so ``from src.modules.shared.log_levels import resolve_log_level`` would resolve
here, in dev and in this test suite, and then raise ``ModuleNotFoundError`` at
container start in production. The duplication is across that image boundary and
is the correct trade; ``TestAgreesWithTheSharedResolver`` below is what keeps the
two copies from drifting.

What the guard is for -- ``getattr(logging, value.upper(), logging.INFO)``, the
previous implementation, resolves two public logging attributes that are not
levels, *successfully*, to something unusable:

* ``NOTSET`` -> ``0``. Below every threshold; the bridge's logs go silent.
* ``BASIC_FORMAT`` -> ``'%(levelname)s:%(name)s:%(message)s'``, a *string*.
  ``basicConfig(level=...)`` then raises ``ValueError: Unknown level`` from
  ``main()``, before the bridge binds its port -- the container does not start.

Both arrive the same way: ``LOG_LEVEL`` from the environment, which compose
passes through as ``${LOG_LEVEL:-INFO}``.

Note that CI does not run this file. `.github/workflows/tests.yml` skips
``tests/unit/teams_bridge`` because the package needs botbuilder, which the prod
image does not carry -- so a green CI says nothing about these tests. Run them in
the project virtualenv.
"""

import logging
from contextlib import contextmanager

import pytest

from src.modules.shared.log_levels import resolve_log_level
from src.teams_bridge.__main__ import _resolve_log_level, _setup_logging


#: Every value that must fall back to INFO. The first four are the ones that
#: motivated the guard: a plain getattr resolves them instead of rejecting them.
_UNUSABLE = [
    pytest.param('NOTSET', id='NOTSET-getattr-resolves-it-to-0'),
    pytest.param('notset', id='notset-lowercase'),
    pytest.param('  NOTSET  ', id='NOTSET-padded'),
    pytest.param('BASIC_FORMAT', id='BASIC_FORMAT-getattr-resolves-it-to-a-string'),
    pytest.param('basic_format', id='basic_format-lowercase'),
    pytest.param('', id='empty-string'),
    pytest.param('  ', id='whitespace-only'),
    pytest.param(None, id='None'),
    pytest.param(20, id='int'),
    pytest.param(logging.INFO, id='level-number-rather-than-a-name'),
    pytest.param(True, id='bool'),
    pytest.param(['INFO'], id='list'),
    pytest.param('VERBOSE', id='unknown-name'),
    pytest.param('TRACE', id='unknown-name-TRACE'),
    pytest.param('debugg', id='typo'),
    pytest.param('INF0', id='typo-with-a-zero'),
]

#: Names an operator may legitimately put in LOG_LEVEL, in the spellings the
#: guard has to accept.
_RECOGNISED = [
    'DEBUG', 'INFO', 'WARN', 'WARNING', 'ERROR', 'FATAL', 'CRITICAL',
    'debug', 'Info', 'wArN', '  ERROR  ', '\tfatal\n',
]


@contextmanager
def bare_root_logger():
    """The root logger with its handlers off, restored on the way out.

    ``_setup_logging`` calls ``basicConfig``, whose ``setLevel`` sits inside an
    ``if len(root.handlers) == 0`` -- so with any handler attached it silently
    does nothing and a level assertion passes vacuously. Under pytest there is
    always one: the capture plugin attaches a fresh handler to the root logger
    for *each* test phase, so a fixture that cleared them during setup would be
    undone before the test body ran. Hence a context manager used inline, not a
    fixture. Restoring the same handler objects matters too -- the plugin
    removes its own by identity when the phase ends.
    """
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    root.handlers.clear()
    try:
        yield root
    finally:
        root.handlers.clear()
        root.handlers.extend(saved_handlers)
        root.setLevel(saved_level)


class TestRecognisedLevelNames:
    """Every name an operator can legitimately type."""

    @pytest.mark.parametrize('name,expected', [
        ('DEBUG', logging.DEBUG),
        ('INFO', logging.INFO),
        ('WARN', logging.WARNING),
        ('WARNING', logging.WARNING),
        ('ERROR', logging.ERROR),
        ('FATAL', logging.CRITICAL),
        ('CRITICAL', logging.CRITICAL),
    ])
    def test_level_name_resolves_to_its_number(self, name, expected):
        assert _resolve_log_level(name) == expected

    @pytest.mark.parametrize('alias,canonical', [('WARN', 'WARNING'), ('FATAL', 'CRITICAL')])
    def test_stdlib_aliases_match_their_canonical_name(self, alias, canonical):
        """WARN/FATAL are the two aliases the stdlib keeps. The old getattr
        accepted both, so dropping either from the allow-list would take a
        working deployment's LOG_LEVEL and silently downgrade it to INFO."""
        assert _resolve_log_level(alias) == _resolve_log_level(canonical)

    @pytest.mark.parametrize('written', [
        'debug', 'Debug', 'dEbUg', '  DEBUG', 'DEBUG  ', '\tdebug\n', ' Debug ',
    ])
    def test_name_is_case_insensitive_and_whitespace_stripped(self, written):
        assert _resolve_log_level(written) == logging.DEBUG


class TestUnusableValuesFallBack:
    """Anything that is not a level name yields INFO -- audible, not silent."""

    @pytest.mark.parametrize('value', _UNUSABLE)
    def test_falls_back_to_info(self, value):
        assert _resolve_log_level(value) == logging.INFO

    def test_notset_does_not_resolve_to_zero(self):
        """Left out of the allow-list deliberately, not by omission. 0 is not a
        threshold anything can be logging *at*: on the root logger, which is
        what the bridge configures, it means every record gets through."""
        assert _resolve_log_level('NOTSET') != logging.NOTSET

    def test_basic_format_does_not_resolve_to_a_string(self):
        """The failure that stops the container: basicConfig(level=<str>) raises
        ValueError from main(), before the bridge binds its port."""
        assert _resolve_log_level('BASIC_FORMAT') == logging.INFO
        assert isinstance(_resolve_log_level('BASIC_FORMAT'), int)


class TestSetupLoggingAppliesTheGuardedLevel:
    """The guard has to be wired into the only caller, not just defined.

    These drive the real ``_setup_logging`` against the real root logger, which
    is what ``main()`` does with ``os.environ['LOG_LEVEL']`` at startup.
    """

    def test_recognised_name_reaches_the_root_logger(self):
        with bare_root_logger() as root:
            _setup_logging('debug')

            assert root.level == logging.DEBUG

    def test_basic_format_does_not_raise_and_leaves_the_bridge_at_info(self):
        """Pre-fix this raised ValueError: Unknown level -- the bridge did not
        start at all."""
        with bare_root_logger() as root:
            _setup_logging('BASIC_FORMAT')

            assert root.level == logging.INFO

    def test_notset_leaves_the_bridge_at_info_rather_than_zero(self):
        """Pre-fix this set the root logger to 0 and the bridge logged
        everything, which is not the level the operator configured."""
        with bare_root_logger() as root:
            _setup_logging('NOTSET')

            assert root.level == logging.INFO

    def test_the_level_it_resolves_is_usable(self):
        """Both failure modes surface as an exception at the point of use, so
        prove the configured level survives an actual log call too."""
        with bare_root_logger() as root:
            _setup_logging('BASIC_FORMAT')

            root.log(_resolve_log_level('BASIC_FORMAT'), 'probe')


class TestAgreesWithTheSharedResolver:
    """The anti-drift check on the duplication.

    ``src.modules.shared.log_levels`` is unreachable from inside the bridge's
    image, but this test suite is not shipped in any image, so it can hold both
    copies side by side and require the same answer from each. If the two ever
    disagree, they disagree here first rather than in a customer's container.
    """

    @pytest.mark.parametrize('value', _UNUSABLE + _RECOGNISED)
    def test_both_copies_resolve_the_value_identically(self, value):
        assert _resolve_log_level(value) == resolve_log_level(value)

    def test_the_two_allow_lists_are_the_same_mapping(self):
        """Names and numbers both -- a copy that accepted the same names and
        mapped FATAL somewhere else would pass a value-by-value sweep of the
        cases anyone thought to write down."""
        from src.modules.shared.log_levels import _LEVELS_BY_NAME as shared_levels
        from src.teams_bridge.__main__ import _LEVELS_BY_NAME as bridge_levels

        assert bridge_levels == shared_levels
