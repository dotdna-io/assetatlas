# SPDX-License-Identifier: AGPL-3.0-or-later
"""No workflow may gate a value behind `<cond> && '' || <x>` — it always yields <x>.

GitHub Actions `&&` and `||` return operand VALUES, not booleans, and the empty
string is falsy, so `A && '' || B` can never produce `''`: the `&&` branch
evaluates to `''`, then the `||` falls through to `B`. Every `cache:` line in
tests.yml used exactly that form to mean "pip cache off on the fleet, on for
hosted" and instead resolved to 'pip' on every run — a 3.18 GB cache blob
restored over the fleet VM's ~10 MB/s uplink on every job (~250s median), while
the comment above the sites asserted caching was off (#1268).

The fix inverts the test (`!= 'fleet' && 'pip' || ''`) so the falsy operand
sits LAST, where `||` cannot swallow it; this guard exists so the broken idiom
cannot come back under any future wording.

Whole-line comments are stripped before scanning: tests.yml's own comment block
quotes the broken idiom to warn about it, and a naive substring scan would then
fail on the documentation of the bug rather than the bug. Only WHOLE-line
comments are stripped — an idiom on a real `key: value` line is exactly what
must be caught, whether or not a trailing inline comment follows it.

Runs without a database, network, or fixtures, and is included in the
"pipeline + delivery domain (hard gate)" required check — unlike plain
`pytest tests/unit`, which is warning-only in CI (#596 H7) and therefore cannot
enforce anything.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = PROJECT_ROOT / ".github" / "workflows"

# `<anything> && '' || <anything>` (single or double quotes). The trap is the
# empty string as the `&&` result; the CORRECT form, `!= 'fleet' && 'pip' ||
# ''`, puts a non-empty value there and must not match.
_BROKEN_IDIOM = re.compile(r"""&&\s*(?:""|'')\s*\|\|""")

# The historical form, pinned so a regex edit that stops matching reality fails
# here rather than silently disarming the scan below.
_HISTORICAL_EXAMPLE = "${{ vars.CI_RUNNER == 'fleet' && '' || 'pip' }}"


def _workflow_files():
    return sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(
        WORKFLOWS_DIR.glob("*.yaml")
    )


def _code_lines(path):
    """(lineno, line) pairs with whole-line YAML comments removed."""
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        yield lineno, line


def test_workflows_dir_is_not_empty():
    """Guards the guard: an empty glob would make the scan vacuously pass."""
    assert _workflow_files(), f"no *.yml/*.yaml workflows found in {WORKFLOWS_DIR}"


def test_pattern_matches_the_historical_idiom():
    """Guards the guard: the regex must still catch the exact form that shipped."""
    assert _BROKEN_IDIOM.search(_HISTORICAL_EXAMPLE)


def test_no_workflow_gates_a_value_behind_empty_string_and():
    offenders = []
    for path in _workflow_files():
        for lineno, line in _code_lines(path):
            if _BROKEN_IDIOM.search(line):
                offenders.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {line.strip()}"
                )
    assert not offenders, (
        "workflow expression(s) use the `<cond> && '' || <x>` idiom, which always "
        "yields <x> because GitHub Actions && and || return operand values and "
        "'' is falsy — invert the condition so the falsy value comes last "
        "(`<negated-cond> && <x> || ''`). Offenders: " + "; ".join(offenders)
    )
