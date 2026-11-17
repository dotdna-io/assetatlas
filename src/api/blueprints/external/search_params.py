# SPDX-License-Identifier: AGPL-3.0-or-later
"""Request parsing and validation for GET /api/v1/assets/search.

The route runs the Explorer/chat query path (``list_assets_query``) unchanged.
This module makes that path LOUD for an API caller: the engine quietly drops a
clause it cannot build — a binary operator with no value, a confidence level it
does not know, a ninth keyword, a non-dict filter entry — and a dropped clause
WIDENS the result while still answering 200. The caller here is an LLM that
reports what it gets back, so every such input is refused with a 400 whose
message names the fix (the valid operators, the nearest field names).

Pure functions over the request strings plus the live field resolver; nothing
here reads or writes the database.
"""

import difflib
import json
import math
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from src.api.middleware.error_handler import ValidationError
from src.mcp.tools._common import parse_filters_arg
from src.mcp.tools._query import CHAT_LIST_FIELDS_ENCODED, _normalize_field_name
from src.modules.assets.repositories.asset_repository import SEARCH_MAX_TOKENS
from src.modules.fields.catalog import (
    STATIC_FIELDS, field_id_for_plain_id, operator_codes_for_entry,
)
from src.modules.fields.encoding import decode, encode
from src.modules.fields.models import StaticField
from src.modules.fields.operators import (
    _RELATIVE_DURATION_RE, OperatorResolutionError, resolve as resolve_operator,
)
from src.modules.fields.types import FieldDataType

SEARCH_MAX_CHARS = 256
SEARCH_MAX_TERMS = SEARCH_MAX_TOKENS
FILTERS_MAX_COUNT = 20
FILTERS_MAX_BYTES = 8 * 1024
FIELDS_MAX_COUNT = 50
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
# Deep OFFSET paging is the inventory route's job; 1000 pages of 100 already
# reaches 100k rows.
MAX_PAGE = 1000

_USER_FIELD = "static:User%20Field"
# The chat list columns, plus the user field: "which asset belongs to <person>"
# is answered by Owner OR User Field, so a default row must carry both. Appended
# here rather than to CHAT_LIST_FIELDS_ENCODED, which also fixes the chat table
# and the CSV export's column set.
DEFAULT_FIELDS: Tuple[str, ...] = tuple(
    CHAT_LIST_FIELDS_ENCODED.split(",")
    + ([] if _USER_FIELD in CHAT_LIST_FIELDS_ENCODED.split(",") else [_USER_FIELD])
)

# Every static field's row key is its snake_case column name. list_assets_query
# snake-cases 16 of them (_ENCODED_TO_SNAKE, same names); the route finishes the
# job for the rest (User Field, MAC Vendor, OS EOL Status, ...) so the contract
# is uniform: static -> column name, every other kind -> its encoded FieldId.
STATIC_ROW_KEYS: Dict[str, str] = {
    encode(StaticField(kind="static", name=name)): column
    for name, column in STATIC_FIELDS.items()
}

# ...and the reverse, so any row key a response carries is also accepted as a
# field name ("ip", "sn", "user_field"). list_assets_query's own aliases are the
# lowercased display names ("ip address"), which an agent reading rows keyed
# "ip" has no way to guess.
_ROW_KEY_ALIASES: Dict[str, str] = {column: enc for enc, column in STATIC_ROW_KEYS.items()}

_FILTER_KEYS = frozenset({"field", "field_id", "op", "value"})
_TEXT_TYPES = frozenset({
    FieldDataType.STRING, FieldDataType.STRING_ARRAY,
    FieldDataType.JSONB_ARRAY, FieldDataType.JSONB,
})
_NUMBER_TYPES = frozenset({FieldDataType.INTEGER, FieldDataType.FLOAT})
_DATE_TYPES = frozenset({FieldDataType.DATE, FieldDataType.TIMESTAMP})
_BOOL_STRINGS = {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False}
# ISO-8601 calendar date, optionally with a time and offset — the subset both
# Python and Postgres read the same way (fromisoformat also takes week dates
# like 2026-W45-1, which Postgres rejects).
_ISO_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"([T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}(:?\d{2})?)?)?$"
)
# Operators whose SQL builder resolves a relative duration ("30d") to a cutoff;
# eq/neq bind the value as-is, where "30d" is not a timestamp.
_RELATIVE_OK_OPS = frozenset({"gt", "lt", "gte", "lte", "between", "within_last"})

# Every query parameter the route reads. Anything else is a 400: a misspelt
# "filter" or "per_page" would otherwise be ignored and widen the answer.
SEARCH_PARAMS = (
    "search", "filters", "logic", "fields", "sort_by", "sort_dir", "page", "limit",
)
_CONFIDENCE_LEVELS = ("high", "medium", "low")


# ---------------------------------------------------------------------------
# scalar params
# ---------------------------------------------------------------------------

def parse_search(raw: Optional[str]) -> Optional[str]:
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    if len(text) > SEARCH_MAX_CHARS:
        raise ValidationError(
            f'"search" is {len(text)} characters; the maximum is {SEARCH_MAX_CHARS}.'
        )
    terms = text.split()
    if len(terms) > SEARCH_MAX_TERMS:
        raise ValidationError(
            f'"search" has {len(terms)} terms; at most {SEARCH_MAX_TERMS} are '
            'allowed (every term must match, so drop the least specific ones, '
            'or move exact conditions into "filters").'
        )
    return text


def escape_search(text: str) -> str:
    """Make every keyword term a LITERAL substring: ``%``, ``_`` and ``\\``
    escaped for ILIKE's default escape character, as the filter operators
    already do. Unescaped, ``search=%`` would match every asset (a full dump
    through the search route) and ``srv_web`` would also match ``srv-web``."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def check_params(args) -> None:
    unknown = sorted(set(args) - set(SEARCH_PARAMS))
    if unknown:
        raise ValidationError(
            f"Unknown parameter(s) {', '.join(map(repr, unknown))}. Valid: "
            f"{', '.join(SEARCH_PARAMS)}."
        )


def parse_logic(raw: Optional[str]) -> str:
    logic = (raw or "and").strip().lower()
    if logic not in ("and", "or"):
        raise ValidationError(f'"logic" must be "and" or "or", got {raw!r}.')
    return logic


def parse_sort_dir(raw: Optional[str]) -> str:
    sort_dir = (raw or "asc").strip().lower()
    if sort_dir not in ("asc", "desc"):
        raise ValidationError(f'"sort_dir" must be "asc" or "desc", got {raw!r}.')
    return sort_dir


def _int_param(name: str, raw: Optional[str], default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        raise ValidationError(f'"{name}" must be an integer, got {raw!r}.')


def parse_paging(raw_page: Optional[str], raw_limit: Optional[str]) -> Tuple[int, int]:
    """Page is 1-based and capped (400 above MAX_PAGE); limit is clamped to
    1..MAX_LIMIT, like the sibling routes — the effective value is echoed back
    in ``pagination.limit``. A non-integer is a 400, not a silent page 1."""
    page = max(1, _int_param("page", raw_page, 1))
    limit = _int_param("limit", raw_limit, DEFAULT_LIMIT)
    if page > MAX_PAGE:
        raise ValidationError(
            f'"page" must be at most {MAX_PAGE}. Narrow the search, or use '
            'GET /api/v1/assets/inventory for a full export.'
        )
    limit = max(1, min(limit, MAX_LIMIT))
    return page, limit


# ---------------------------------------------------------------------------
# field resolution
# ---------------------------------------------------------------------------

def _nearest_field_names(raw: str, resolver, n: int = 5) -> List[str]:
    """Display names closest to ``raw``: substring hits first, then fuzzy ones."""
    needle = raw.rsplit(":", 1)[-1].replace("%20", " ").strip().lower()
    if not needle:
        return []
    names = {e.display_name.lower(): e.display_name for e in resolver.catalog().entries()}
    hits = [names[k] for k in sorted(names) if needle in k]
    close = [names[k] for k in difflib.get_close_matches(needle, list(names), n=n, cutoff=0.6)]
    out: List[str] = []
    for name in close + hits:
        if name not in out:
            out.append(name)
    return out[:n]


def _unknown_field(param: str, raw: str, resolver) -> ValidationError:
    msg = (
        f"Unknown {param} {raw!r}. Pass a field_id or display_name from "
        "GET /api/v1/fields (its ?q= parameter narrows the list)."
    )
    nearest = _nearest_field_names(raw, resolver)
    if nearest:
        msg += " Nearest fields: " + ", ".join(repr(n) for n in nearest) + "."
    return ValidationError(msg)


def resolve_field(raw: Any, resolver, *, param: str):
    """Resolve a row key / display name / plain FieldId / encoded FieldId to
    ``(field_id, encoded_key, catalog_entry)`` the same way list_assets_query's
    normaliser does, raising a 400 that names the nearest fields when it
    resolves to nothing live."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError(f"Each {param} must be a non-empty string.")
    raw = raw.strip()
    field_id = None
    encoded = _ROW_KEY_ALIASES.get(raw.lower()) or _normalize_field_name(raw, resolver)
    if encoded is not None:
        try:
            field_id = decode(encoded)
        except Exception:
            field_id = None
    entry = resolver.catalog_entry(field_id) if field_id is not None else None
    if entry is None and ":" in raw:
        # _normalize_field_name reads any ':' as an encoded id, so a display
        # name or plain field_id whose own text holds a ':' ('Tags - env:prod',
        # 'tag:env:prod') needs the catalog's renderings matched directly.
        field_id = (resolver.catalog().field_id_for_display_name(raw)
                    or field_id_for_plain_id(resolver.catalog(), raw))
        entry = resolver.catalog_entry(field_id) if field_id is not None else None
    if entry is None:
        raise _unknown_field(param, raw, resolver)
    return field_id, encode(field_id), entry


def parse_fields(raw: Optional[str], resolver) -> List[str]:
    """Fields to project -> canonical encoded FieldIds (deduped, in order).

    A CSV, or a JSON array of names for a name that itself holds a comma.
    Absent -> DEFAULT_FIELDS; ``id`` alone -> ``[]`` (id only). Unknown or
    filter-only names are a 400, not a silent drop, so the caller never gets
    a narrower row than it asked for."""
    if raw is None or not raw.strip():
        return list(DEFAULT_FIELDS)
    if raw.lstrip().startswith("["):
        try:
            names = json.loads(raw)
        except ValueError as exc:
            raise ValidationError(f'"fields" is not a valid JSON array: {exc}')
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValidationError('"fields" as JSON must be an array of strings.')
        names = [n.strip() for n in names if n.strip()]
    else:
        names = [n.strip() for n in raw.split(",") if n.strip()]
    if len(names) > FIELDS_MAX_COUNT:
        raise ValidationError(
            f'"fields" names {len(names)} fields; the maximum is {FIELDS_MAX_COUNT}.'
        )
    out: List[str] = []
    for name in names:
        if name == "id":  # always returned
            continue
        _, encoded, entry = resolve_field(name, resolver, param='"fields" entry')
        if entry.filter_only:
            raise ValidationError(
                f"{name!r} is filter-only: it can be used in \"filters\" but "
                "cannot be returned as a column."
            )
        if encoded not in out:
            out.append(encoded)
    return out


def parse_sort_by(raw: Optional[str], resolver) -> Optional[str]:
    if raw is None or not raw.strip():
        return None
    _, encoded, entry = resolve_field(raw, resolver, param='"sort_by" field')
    if entry.filter_only:
        raise ValidationError(f"{raw.strip()!r} is filter-only and cannot be sorted on.")
    return encoded


# ---------------------------------------------------------------------------
# filters
# ---------------------------------------------------------------------------

def _is_scalar(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool)) and not (
        isinstance(v, float) and not math.isfinite(v)
    )


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _check_value_type(v: Any, entry, op_code: str, where: str) -> Any:
    """Validate (and for text fields, stringify) one filter value against the
    field's data type, so a mistyped value is a 400 rather than a Postgres
    cast error (500) or a clause the engine drops."""
    dt = entry.data_type
    if entry.kind_name == "virtual" and entry.virtual_name == "confidence_level":
        if not isinstance(v, str) or v.strip().lower() not in _CONFIDENCE_LEVELS:
            raise ValidationError(
                f"{where}: Confidence Level values are 'high', 'medium' or 'low', got {v!r}."
            )
        return v
    if dt in _TEXT_TYPES:
        if isinstance(v, bool):
            return "true" if v else "false"
        return v if isinstance(v, str) else str(v)
    if dt in _NUMBER_TYPES:
        if isinstance(v, bool):
            raise ValidationError(f"{where}: expected a number, got {v!r}.")
        if isinstance(v, (int, float)):
            return v
        try:
            float(v)
        except (TypeError, ValueError):
            raise ValidationError(f"{where}: expected a number, got {v!r}.")
        return v
    if dt in _DATE_TYPES:
        relative_ok = op_code in _RELATIVE_OK_OPS
        if isinstance(v, str):
            s = v.strip()
            if relative_ok and _RELATIVE_DURATION_RE.match(s):
                return s
            if _ISO_DATE_RE.match(s):
                try:
                    datetime.fromisoformat(s)
                    return s
                except ValueError:
                    pass
        raise ValidationError(
            f"{where}: expected an ISO-8601 date/time (e.g. '2026-11-01' or "
            "'2026-11-01T08:00:00Z')"
            + (" or a relative duration (e.g. '30d', '12h')" if relative_ok else "")
            + f", got {v!r}."
        )
    if dt == FieldDataType.BOOLEAN:
        # Bind a real bool: an int 1 against a boolean column is
        # `boolean = integer`, which Postgres refuses (a 500).
        if isinstance(v, bool):
            return v
        if isinstance(v, int) and v in (0, 1):
            return bool(v)
        if isinstance(v, str) and v.strip().lower() in _BOOL_STRINGS:
            return _BOOL_STRINGS[v.strip().lower()]
        raise ValidationError(f"{where}: expected true or false, got {v!r}.")
    return v


def _check_filter_value(op, value: Any, entry, where: str) -> Any:
    if op.arity == "none":
        return None
    if op.arity == "one":
        if isinstance(value, (list, dict)):
            hint = " Use 'any' for a list of values." if op.code in ("eq", "neq") else ""
            raise ValidationError(f"{where}: operator '{op.code}' takes a single value.{hint}")
        if _is_blank(value):
            raise ValidationError(
                f"{where}: operator '{op.code}' needs a non-empty \"value\" "
                "(to match a missing value use 'empty')."
            )
        if not _is_scalar(value):
            raise ValidationError(f"{where}: unsupported value {value!r}.")
        return _check_value_type(value, entry, op.code, where)
    # arity == "list": a JSON array, or a comma-separated string (the Explorer's
    # serialisation). Blank members are dropped, as the engine drops them.
    if isinstance(value, list):
        members = value
    elif isinstance(value, str):
        members = value.split(",")
    else:
        raise ValidationError(
            f"{where}: operator '{op.code}' takes a list of values, e.g. [\"a\", \"b\"]."
        )
    if any(not (m is None or _is_scalar(m)) for m in members):
        raise ValidationError(f"{where}: list members must be strings, numbers or booleans.")
    members = [m.strip() if isinstance(m, str) else m for m in members if not _is_blank(m)]
    if not members:
        raise ValidationError(f"{where}: operator '{op.code}' needs at least one value.")
    if op.code == "between" and len(members) != 2:
        raise ValidationError(
            f"{where}: 'between' takes exactly two values [low, high], got {len(members)}."
        )
    return [_check_value_type(m, entry, op.code, where) for m in members]


def parse_filters(raw: Optional[str], resolver) -> Tuple[Optional[List[dict]], List[dict]]:
    """Parse + validate the ``filters`` JSON.

    Returns ``(engine_filters, echo)``: the normalised list handed to
    list_assets_query (``{field_id, op, value}`` with a canonical operator
    code) and its caller-facing echo (``{field, display_name, op, value}``).
    ``(None, [])`` when absent or an empty array.
    """
    if raw is None or not raw.strip():
        return None, []
    size = len(raw.encode("utf-8"))
    if size > FILTERS_MAX_BYTES:
        raise ValidationError(
            f'"filters" is {size} bytes; the maximum is {FILTERS_MAX_BYTES}.'
        )
    parsed, err = parse_filters_arg(raw)
    if err:
        raise ValidationError(err.removeprefix("Error: "))
    if not parsed:
        return None, []
    if len(parsed) > FILTERS_MAX_COUNT:
        raise ValidationError(
            f'"filters" has {len(parsed)} entries; the maximum is {FILTERS_MAX_COUNT}.'
        )

    engine: List[dict] = []
    echo: List[dict] = []
    for i, f in enumerate(parsed):
        where = f"filters[{i}]"
        if not isinstance(f, dict):
            raise ValidationError(f'{where} must be an object {{"field", "op", "value"}}.')
        unknown = sorted(set(f) - _FILTER_KEYS)
        if unknown:
            raise ValidationError(
                f"{where} has unknown key(s) {', '.join(map(repr, unknown))}; "
                "each filter is {\"field\", \"op\", \"value\"}."
            )
        raw_field = f.get("field", f.get("field_id"))
        if _is_blank(raw_field):
            raise ValidationError(f'{where} is missing its "field".')
        field_id, encoded, entry = resolve_field(raw_field, resolver, param=f'{where} field')
        if not isinstance(f.get("op"), str) or _is_blank(f.get("op")):
            raise ValidationError(f'{where} needs an "op" string (e.g. "contains").')

        valid = operator_codes_for_entry(entry)
        try:
            op = resolve_operator(f["op"])
        except OperatorResolutionError:
            raise ValidationError(
                f"{where}: unknown operator {f['op']!r}. Operators valid for "
                f"{entry.display_name!r}: {', '.join(valid)}."
            )
        if op.code not in valid:
            spelled = repr(f["op"]) if f["op"].strip().lower() == op.code else (
                f"{f['op']!r} ({op.code})"
            )
            raise ValidationError(
                f"{where}: operator {spelled} is not valid for "
                f"{entry.display_name!r} ({entry.data_type.value}). Valid: "
                f"{', '.join(valid)}."
            )
        value = _check_filter_value(op, f.get("value"), entry, where)

        engine.append({"field_id": field_id.model_dump(), "op": op.code, "value": value})
        item = {"field": encoded, "display_name": entry.display_name, "op": op.code}
        if op.arity != "none":
            item["value"] = value
        echo.append(item)
    return engine, echo


# ---------------------------------------------------------------------------
# response rows
# ---------------------------------------------------------------------------

def _json_value(v: Any) -> Any:
    if isinstance(v, datetime) or isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [_json_value(x) for x in v]
    return v


def row_key(encoded: str) -> str:
    return STATIC_ROW_KEYS.get(encoded, encoded)


def project_row(row: Dict[str, Any], keep=None) -> Dict[str, Any]:
    """Final row shape: ``id``; static fields under their snake_case column
    name; every other kind under its canonical encoded FieldId. Timestamps are
    ISO-8601 strings. ``keep`` (a set of row keys) drops anything else."""
    out = {row_key(k): _json_value(v) for k, v in row.items()}
    return out if keep is None else {k: v for k, v in out.items() if k in keep}
