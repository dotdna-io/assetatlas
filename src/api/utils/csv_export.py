# SPDX-License-Identifier: AGPL-3.0-or-later
"""Streaming CSV serialization for asset exports.

Mirrors the frontend CSV contract
(ui/prod/src/pages/explorer/explorer-utils.ts:generateCSV): RFC-4180 quoting,
list values joined with "; ", CRLF line endings — so chat / Explorer / API
exports produce byte-identical CSVs. Uses csv.writer (default dialect: comma
delimiter, CRLF terminator, QUOTE_MINIMAL) whose quoting rules match the
frontend's escapeCSVValue (quote on comma / quote / CR / LF, double inner quotes).
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, Iterable, Iterator, List, Tuple


# Leading characters that make a spreadsheet treat a cell as a formula.
# '-' is handled separately (a numeric exception) so negative numbers survive.
_FORMULA_LEAD = ("=", "+", "@", "\t", "\r")


def _neutralize_formula(s: str) -> str:
    """Prefix a single quote when ``s`` could be read as a spreadsheet formula,
    so opening the CSV in Excel / Sheets / LibreOffice can't execute it (CSV /
    formula injection — OWASP). Triggers on a leading =, +, @, TAB or CR; a
    leading '-' triggers ONLY when it is not a negative number, i.e. not
    followed by an ASCII digit or '.', so -5 / -5.3 / -.5 stay numeric while
    -cmd / -=1+1 are defused. A lone '-' is left as-is (not a formula). Known
    accepted residual: a formula that starts minus-then-digit (e.g. -2+3) reads
    as numeric and is NOT defused — the price of keeping negative numbers intact.

    MUST stay byte-identical to the frontend escapeCSVValue
    (ui/prod/src/pages/explorer/explorer-utils.ts). The digit test is ASCII-only
    (not str.isdigit(), which accepts Unicode digits the TS /[0-9]/ would not)
    so the two languages agree on the -<digit> case."""
    if not s:
        return s
    c0 = s[0]
    if c0 in _FORMULA_LEAD:
        return "'" + s
    if c0 == "-":
        nxt = s[1] if len(s) > 1 else ""
        if nxt == "" or nxt in "0123456789" or nxt == ".":
            return s
        return "'" + s
    return s


def _cell(value: Any) -> str:
    """Coerce one cell to its pre-quoting string form. None -> "" ;
    list/tuple -> '; '-joined (matches the frontend's array rendering) ;
    everything else -> str(value). The result is run through
    _neutralize_formula before csv.writer adds RFC-4180 quoting on top."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return _neutralize_formula(
            "; ".join("" if v is None else str(v) for v in value)
        )
    return _neutralize_formula(str(value))


def stream_csv(
    rows: Iterable[Dict[str, Any]],
    columns: List[Tuple[str, str]],
) -> Iterator[str]:
    """Yield CSV text: a header row of column labels, then one line per row
    projecting each (key, label) in ``columns`` out of the row dict. Streams
    row-by-row so a large export never materializes the whole file in memory."""
    buf = io.StringIO()
    writer = csv.writer(buf)

    def _flush() -> str:
        out = buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        return out

    # Header labels go through the same neutralizer: the frontend runs column
    # names through escapeCSVValue too, so a custom-field display name like
    # "=Total" must be defused on both sides to keep the outputs identical.
    writer.writerow([_neutralize_formula(label) for _key, label in columns])
    yield _flush()

    for row in rows:
        writer.writerow([_cell(row.get(key)) for key, _label in columns])
        yield _flush()
