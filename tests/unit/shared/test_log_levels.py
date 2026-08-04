# SPDX-License-Identifier: AGPL-3.0-or-later
"""resolve_log_level(): the one guarded config-string -> logging-level mapping.

``getattr(logging, value.upper(), default)`` looks exactly like this function and
is a trap, because the logging module exposes public attributes that are not
levels and getattr resolves them *successfully*, to something unusable:

* ``NOTSET`` -> ``0``. Below every threshold, and on a non-root logger it means
  "delegate to my parent" rather than "log everything". Either way the operator
  does not get the level they configured -- the invisible-log class of bug that
  #1176 fixed for health-check requests.
* ``BASIC_FORMAT`` -> ``'%(levelname)s:%(name)s:%(message)s'``, a *string*.
  ``logger.log()`` then raises ``TypeError: level must be an integer`` and
  ``setLevel()``/``basicConfig(level=...)`` raise ``ValueError: Unknown level``,
  so a configured value can take a process down.

Both of those, and every unrecognised name, must land on the caller's default.
DB-free, stdlib-only.
"""

import ast
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from src.modules.shared.log_levels import resolve_log_level


_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODULE_PATH = _REPO_ROOT / 'src' / 'modules' / 'shared' / 'log_levels.py'

#: The helper's own logger, so an assertion about what it logged can ignore
#: whatever else the session happens to be logging.
_HELPER_LOGGER = 'src.modules.shared.log_levels'


#: Every value that must fall through to the caller's default. The first four
#: are the interesting ones: 'NOTSET' and 'BASIC_FORMAT' are the two names a
#: plain getattr resolves wrongly instead of rejecting.
_UNUSABLE = [
    pytest.param('NOTSET', id='NOTSET-getattr-resolves-it-to-0'),
    pytest.param('notset', id='notset-lowercase'),
    pytest.param('  NOTSET  ', id='NOTSET-padded'),
    pytest.param('BASIC_FORMAT', id='BASIC_FORMAT-getattr-resolves-it-to-a-string'),
    pytest.param('basic_format', id='basic_format-lowercase'),
    pytest.param('', id='empty-string'),
    pytest.param('  ', id='whitespace-only'),
    pytest.param(None, id='None'),
    pytest.param(123, id='int'),
    pytest.param(logging.INFO, id='level-number-rather-than-a-name'),
    pytest.param(True, id='bool'),
    pytest.param(['INFO'], id='list'),
    pytest.param('VERBOSE', id='unknown-name'),
    pytest.param('TRACE', id='unknown-name-TRACE'),
    pytest.param('debugg', id='typo'),
    pytest.param('INF0', id='typo-with-a-zero'),
]


@pytest.fixture
def probe_logger():
    """A throwaway logger for proving a resolved level is usable.

    The NullHandler is what keeps the probe's own records off stderr: with no
    handler anywhere on the chain, logging falls back to ``lastResort`` and
    prints WARNING and above.
    """
    probe = logging.getLogger('tests.log_levels_probe')
    probe.addHandler(logging.NullHandler())
    probe.propagate = False
    try:
        yield probe
    finally:
        probe.handlers.clear()
        probe.propagate = True
        probe.setLevel(logging.NOTSET)


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
        assert resolve_log_level(name) == expected

    @pytest.mark.parametrize('alias,canonical', [('WARN', 'WARNING'), ('FATAL', 'CRITICAL')])
    def test_stdlib_aliases_match_their_canonical_name(self, alias, canonical):
        """WARN/FATAL are the two aliases the stdlib keeps; dropping either from
        the allow-list would silently downgrade a working config to INFO."""
        assert resolve_log_level(alias) == resolve_log_level(canonical)

    @pytest.mark.parametrize('written', [
        'debug', 'Debug', 'dEbUg', '  DEBUG', 'DEBUG  ', '\tdebug\n', ' Debug ',
    ])
    def test_name_is_case_insensitive_and_whitespace_stripped(self, written):
        assert resolve_log_level(written) == logging.DEBUG

    def test_recognised_name_wins_over_the_default(self):
        assert resolve_log_level('DEBUG', logging.ERROR) == logging.DEBUG


class TestUnusableValuesFallBack:
    """Anything that is not a level name yields the caller's default."""

    @pytest.mark.parametrize('value', _UNUSABLE)
    def test_falls_back_to_the_implicit_info_default(self, value):
        assert resolve_log_level(value) == logging.INFO

    @pytest.mark.parametrize('value', _UNUSABLE)
    def test_falls_back_to_the_callers_default(self, value):
        assert resolve_log_level(value, logging.ERROR) == logging.ERROR

    def test_notset_does_not_resolve_to_zero(self):
        """Excluded deliberately, not by omission. 0 is not a threshold anyone
        can be logging *at*: on the root logger it lets everything through, on
        any other it delegates to the parent. Neither is the level that was
        asked for."""
        assert resolve_log_level('NOTSET') != logging.NOTSET
        assert resolve_log_level('NOTSET', logging.DEBUG) == logging.DEBUG

    @pytest.mark.parametrize('value', _UNUSABLE)
    def test_result_is_usable_where_the_call_sites_use_it(self, value, probe_logger):
        """Both failure modes surface as an exception at the point of use, so
        prove the result survives the two calls the four call sites make."""
        level = resolve_log_level(value)

        probe_logger.setLevel(level)      # ValueError: Unknown level
        probe_logger.log(level, 'probe')  # TypeError: level must be an integer

        assert probe_logger.level == logging.INFO


class TestWarnFlag:
    """The flag exists because the callers differ in how often they run.

    Startup callers want the warning -- it is the only clue an operator gets
    that their .env has a typo in it. The request_id middleware resolves its
    health-check level on *every request*, where an unconditional warning would
    turn one bad setting into a log line per request. The asymmetry is the whole
    point of the flag.
    """

    def test_warn_true_logs_one_warning_naming_the_value(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=_HELPER_LOGGER):
            resolve_log_level('NOTSET', warn=True)

        records = [r for r in caplog.records if r.name == _HELPER_LOGGER]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        message = records[0].getMessage()
        assert 'NOTSET' in message
        assert 'INFO' in message

    def test_warn_true_names_the_fallback_level_it_used(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=_HELPER_LOGGER):
            resolve_log_level('nope', logging.ERROR, warn=True)

        records = [r for r in caplog.records if r.name == _HELPER_LOGGER]
        assert len(records) == 1
        assert 'nope' in records[0].getMessage()
        assert 'ERROR' in records[0].getMessage()

    def test_warn_true_is_silent_for_a_recognised_name(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=_HELPER_LOGGER):
            resolve_log_level('warning', warn=True)

        assert [r for r in caplog.records if r.name == _HELPER_LOGGER] == []

    def test_warn_defaults_to_false_and_logs_nothing(self, caplog):
        """What the per-request call site in request_id middleware depends on.
        The logger is forced to DEBUG so this cannot pass by level filtering."""
        with caplog.at_level(logging.DEBUG, logger=_HELPER_LOGGER):
            for _ in range(3):
                resolve_log_level('BASIC_FORMAT')

        assert [r for r in caplog.records if r.name == _HELPER_LOGGER] == []

    def test_warn_is_keyword_only(self):
        """So a positional third argument can't quietly mean ``default=True``."""
        with pytest.raises(TypeError):
            resolve_log_level('nope', logging.INFO, True)


class TestStdlibOnly:
    """The helper is imported from src/api/** and src/modules/** alike, including
    processes that never build a Flask app. Its import graph must stay
    stdlib-only so importing it can never cost a web framework."""

    def test_source_imports_only_stdlib(self):
        imported = set()
        for node in ast.walk(ast.parse(_MODULE_PATH.read_text())):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add('.' if node.level else (node.module or '').split('.')[0])

        assert imported <= set(sys.stdlib_module_names), (
            f"non-stdlib imports in {_MODULE_PATH.name}: "
            f"{sorted(imported - set(sys.stdlib_module_names))}"
        )

    def test_importing_it_does_not_import_flask(self):
        """Fresh interpreter, real dotted path -- so the package __init__ chain
        counts too, not just the leaf module's own imports."""
        probe = (
            'import sys\n'
            'import src.modules.shared.log_levels as m\n'
            'assert m.resolve_log_level("warn") == 30\n'
            'heavy = {"flask", "pydantic", "pandas", "psycopg2", "werkzeug", "redis"}\n'
            'print(",".join(sorted(n for n in sys.modules if n.split(".")[0] in heavy)))\n'
        )

        result = subprocess.run(
            [sys.executable, '-c', probe],
            cwd=_REPO_ROOT, capture_output=True, text=True,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == '', f"heavy imports pulled in: {result.stdout.strip()}"
