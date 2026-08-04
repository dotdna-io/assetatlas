# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-surface drift guards: every place that re-states the operator vocabulary
must stay in lockstep with the registry (src/modules/fields/operators.py).

The unification centralised operator logic, but a few surfaces still hand-maintain
a copy of the vocabulary in another language/file: migration 109's alias map (SQL),
the Explorer parser's static table (TS), and the FilterRow VALUELESS set (TS). Each
caused (or nearly caused) a production bug when it silently fell behind. These tests
parse those files and assert parity so a future operator addition fails loudly here
instead of in a customer's wizard or upgrade.
"""

import re
from pathlib import Path

from src.modules.fields.operators import (
    OPERATOR_REGISTRY,
    OperatorResolutionError,
    _ALIAS_INDEX,
    _ORDER_BY_TYPE,
    resolve,
)

_ROOT = Path(__file__).resolve().parents[4]


def _read(rel):
    return (_ROOT / rel).read_text()


# --- I-3: migration 109 alias map == operators._ALIAS_INDEX ------------------
def test_migration_109_alias_map_matches_registry():
    sql = _read("src/database/migrations/109_normalize_operator_codes.sql")
    # Strip SQL line comments first so a stray ';'/punctuation in a comment can't
    # truncate the VALUES block we slice out below.
    sql = re.sub(r"--[^\n]*", "", sql)
    block = re.search(
        r"INSERT INTO _op_alias \(spelling, code\) VALUES(.*?);",
        sql, re.S,
    )
    assert block, "could not locate the _op_alias VALUES block"
    pairs = re.findall(r"\('([^']*)',\s*'([^']*)'\)", block.group(1))
    migration_map = {sp: code for sp, code in pairs}
    registry_map = {sp: op.code for sp, op in _ALIAS_INDEX.items()}
    missing = set(registry_map) - set(migration_map)
    extra = set(migration_map) - set(registry_map)
    assert not missing, f"migration 109 alias map is missing spellings: {sorted(missing)}"
    assert not extra, f"migration 109 alias map has unknown spellings: {sorted(extra)}"
    mismatched = {sp: (migration_map[sp], registry_map[sp])
                  for sp in registry_map if migration_map[sp] != registry_map[sp]}
    assert not mismatched, f"migration 109 maps spellings to the wrong code: {mismatched}"


# --- I-4: Explorer parser static table == dropdown-exposed registry codes -----
def test_explorer_parser_codes_match_dropdown_exposed_registry():
    ts = _read("ui/prod/src/pages/explorer/explorer-query-parser.ts")
    block = re.search(r"OPERATOR_SPECS:\s*OperatorSpec\[\]\s*=\s*\[(.*?)\];", ts, re.S)
    assert block, "could not locate OPERATOR_SPECS in the parser"
    parser_codes = set(re.findall(r"code:\s*'([^']+)'", block.group(1)))
    # The bar offers exactly the operators that appear in some type's dropdown
    # order (is_true/is_false are reachable only via API/MCP, never the bar).
    exposed = set().union(*[set(v) for v in _ORDER_BY_TYPE.values()])
    assert parser_codes == exposed, (
        f"Explorer parser drifted from the registry: "
        f"missing {sorted(exposed - parser_codes)}, extra {sorted(parser_codes - exposed)}"
    )


# --- I-6: every Explorer-bar operator's SYMBOL is a resolvable spelling --------
# The search bar inserts an operator's symbol (e.g. '!EMPTY', 'WITHIN') when it is
# picked from the dropdown, so symbol.lower() must be one of that operator's
# aliases or the inserted token is unparseable. This is the invariant !EMPTY broke
# (and 'within' too, latently). Scope: operators reachable in some type's dropdown
# order — is_true/is_false are API/MCP-only, their TRUE/FALSE symbols never typed.
def test_bar_inserted_symbols_resolve_on_backend():
    exposed = set().union(*[set(v) for v in _ORDER_BY_TYPE.values()])
    offenders = []
    for code in sorted(exposed):
        op = OPERATOR_REGISTRY[code]
        assert op.symbol, f"{code} has no symbol"
        try:
            resolved = resolve(op.symbol)
        except OperatorResolutionError:
            offenders.append((code, op.symbol))
            continue
        if resolved.code != code:
            offenders.append((code, op.symbol, resolved.code))
    assert not offenders, (
        f"bar-exposed operators whose symbol is not a resolvable alias "
        f"(the inserted token won't parse): {offenders}"
    )


# --- I-7: the Explorer parser's static OPERATOR_SPECS lists each symbol as an
# alias, so a bar-inserted symbol round-trips through parseSearchQuery. This is the
# TS-side guard for the exact !EMPTY bug ('!EMPTY' -> '!empty' was absent from
# notempty's aliases). test_explorer_parser_codes_match_dropdown_exposed_registry
# above only checks codes, so it could not have caught an aliases-array gap.
def test_explorer_parser_symbols_are_aliases():
    ts = _read("ui/prod/src/pages/explorer/explorer-query-parser.ts")
    block = re.search(r"OPERATOR_SPECS:\s*OperatorSpec\[\]\s*=\s*\[(.*?)\];", ts, re.S)
    assert block, "could not locate OPERATOR_SPECS in the parser"
    rows = re.findall(
        r"code:\s*'([^']+)'.*?symbol:\s*'([^']+)'.*?aliases:\s*\[([^\]]*)\]",
        block.group(1), re.S,
    )
    assert rows, "could not parse OPERATOR_SPECS rows"
    offenders = []
    for code, symbol, alias_blob in rows:
        aliases = {a.lower() for a in re.findall(r"'([^']*)'", alias_blob)}
        if symbol.lower() not in aliases:
            offenders.append((code, symbol))
    assert not offenders, (
        f"Explorer parser operators whose symbol is not a typeable alias "
        f"(the inserted token won't re-parse): {offenders}"
    )


# --- I-5: FilterRow VALUELESS set covers the backend arity:'none' operators ---
def test_filterrow_valueless_set_matches_backend_arity_none():
    ts = _read("ui/prod/src/components/ui/FilterRow/operator-specs.ts")
    block = re.search(r"VALUELESS_OPERATORS = new Set\(\[(.*?)\]\)", ts, re.S)
    assert block, "could not locate VALUELESS_OPERATORS"
    entries = set(re.findall(r"'([^']+)'", block.group(1)))
    backend_none = {op.code for op in OPERATOR_REGISTRY.values() if op.arity == "none"}
    missing = backend_none - entries
    assert not missing, (
        f"frontend VALUELESS_OPERATORS is missing backend arity:'none' codes: {sorted(missing)} "
        f"(the readiness gate would strip those filters on save)"
    )
    # Every listed spelling must resolve to a real operator (no typos).
    for e in entries:
        resolve(e)  # raises OperatorResolutionError on an unknown spelling
