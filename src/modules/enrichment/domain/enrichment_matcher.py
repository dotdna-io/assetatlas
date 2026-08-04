# SPDX-License-Identifier: AGPL-3.0-or-later
"""Enrichment Matcher — SQL query builder for matching enrichment rows to assets.

The caller resolves the dataset's ``match_field`` to a SQL expression via the
canonical FieldResolver and passes it here as ``match_expr`` together with a
``value_kind`` describing how to read values out of that expression:

- ``array``       — a native ``text[]`` column → ``unnest(expr)`` (element-wise)
- ``jsonb_array`` — a jsonb array → ``jsonb_array_elements_text(expr)`` (element-wise)
- ``scalar``      — a single value (column, ``->>`` path, or ``COALESCE``) → cast ``::text``

Because ``match_expr`` is produced by FieldResolver from catalog-validated,
escaped strings, it is trusted and interpolated directly — no user string reaches
SQL here. Two match strategies are supported:
- exact: case-insensitive equality on any read value
- cidr:  PostgreSQL inet containment (``<<=``) for IP/subnet matching (ip only)
"""

from typing import Tuple

VALID_VALUE_KINDS = ("array", "jsonb_array", "scalar")


def _build_value_source(match_expr: str, value_kind: str) -> str:
    """Build the set/row source (used as ``CROSS JOIN LATERAL <source> AS av(val)``)."""
    if value_kind == "array":
        return f"unnest({match_expr})"
    if value_kind == "jsonb_array":
        return f"jsonb_array_elements_text({match_expr})"
    if value_kind == "scalar":
        # Subquery yields exactly one (possibly NULL) row; ::text keeps the
        # LOWER() comparison valid for non-text scalar columns.
        return f"(SELECT ({match_expr})::text)"
    raise ValueError(f"Unknown value_kind: {value_kind!r} (expected one of {VALID_VALUE_KINDS})")


def _build_exact_join_clause(match_expr: str, value_kind: str) -> str:
    """FROM/JOIN clause for exact, case-insensitive matching."""
    value_source = _build_value_source(match_expr, value_kind)
    return (
        f"FROM assets a\n"
        f"    CROSS JOIN LATERAL {value_source} AS av(val)\n"
        f"    JOIN enrichment_rows er ON er.dataset_id = %(dataset_id)s::uuid\n"
        f"        AND LOWER(av.val) = LOWER(er.match_value)"
    )


def _build_cidr_join_clause(match_expr: str) -> str:
    """FROM/JOIN clause for CIDR matching (ip column resolved to ``match_expr``)."""
    return (
        f"FROM assets a\n"
        f"    CROSS JOIN LATERAL unnest({match_expr}) AS av(val)\n"
        f"    JOIN enrichment_rows er ON er.dataset_id = %(dataset_id)s::uuid\n"
        f"        AND av.val::inet <<= er.match_value::inet"
    )


def _build_join_clause(match_expr: str, value_kind: str, match_type: str) -> str:
    """Select the correct join clause based on match type."""
    if match_type == "cidr":
        return _build_cidr_join_clause(match_expr)
    return _build_exact_join_clause(match_expr, value_kind)


def _build_order_clause(match_type: str) -> str:
    """Build ORDER BY for the ``DISTINCT ON (a.id)`` match.

    The trailing ``er.enrich_value, er.match_value`` is a deterministic
    tiebreaker (#801): when one asset matches multiple enrichment rows with
    different values (e.g. a device in two SentinelOne sites mapped to two
    legal entities), ``DISTINCT ON`` keeps the first row by this order. Without
    a value tiebreaker the surviving row is decided by physical scan order and
    flips between runs, producing spurious ``attribute_updated`` churn.
    """
    tiebreaker = "er.enrich_value, er.match_value"
    if match_type == "cidr":
        # Most-specific CIDR still wins first; ties below it stay deterministic.
        return f"ORDER BY a.id, masklen(er.match_value::inet) DESC, {tiebreaker}"
    return f"ORDER BY a.id, {tiebreaker}"


def build_match_and_apply_query(
    dataset_id: str,
    match_expr: str,
    value_kind: str,
    match_type: str,
    target_field: str,
) -> Tuple[str, dict]:
    """
    Build an UPDATE query that matches enrichment rows to assets and merges
    the enrich_value into ``extended_attributes -> 'enrichment' -> target_field``.

    Args:
        dataset_id: UUID of the enrichment dataset.
        match_expr: Resolved SQL expression for the match field (from FieldResolver).
        value_kind: 'array' | 'jsonb_array' | 'scalar' — how to read match_expr.
        match_type: 'exact' or 'cidr'.
        target_field: Key name written under the enrichment JSONB namespace.

    Returns:
        (sql, params) tuple ready for ``execute_query()``.
    """
    join_clause = _build_join_clause(match_expr, value_kind, match_type)
    order_clause = _build_order_clause(match_type)

    sql = (
        f"WITH matches AS (\n"
        f"    SELECT DISTINCT ON (a.id)\n"
        f"        a.id,\n"
        f"        er.enrich_value\n"
        f"    {join_clause}\n"
        f"    {order_clause}\n"
        f")\n"
        f"UPDATE assets a\n"
        f"SET extended_attributes = COALESCE(a.extended_attributes, '{{}}'::jsonb)\n"
        f"    || jsonb_build_object(\n"
        f"        'enrichment',\n"
        f"        COALESCE(a.extended_attributes->'enrichment', '{{}}'::jsonb)\n"
        f"        || jsonb_build_object('{target_field}', m.enrich_value)\n"
        f"    )\n"
        f"FROM matches m\n"
        f"WHERE a.id = m.id\n"
        f"RETURNING a.id::text"
    )

    return sql, {"dataset_id": dataset_id}


def build_preview_query(
    dataset_id: str,
    match_expr: str,
    value_kind: str,
    match_type: str,
) -> Tuple[str, dict]:
    """
    Build a SELECT that returns a sample of matched assets (up to 50 rows).

    Columns returned: asset id, first hostname, matched value,
    the enrichment match_value it matched against, and the enrich_value.

    Args:
        dataset_id: UUID of the enrichment dataset.
        match_expr: Resolved SQL expression for the match field (from FieldResolver).
        value_kind: 'array' | 'jsonb_array' | 'scalar' — how to read match_expr.
        match_type: 'exact' or 'cidr'.

    Returns:
        (sql, params) tuple.
    """
    join_clause = _build_join_clause(match_expr, value_kind, match_type)
    order_clause = _build_order_clause(match_type)

    sql = (
        f"SELECT DISTINCT ON (a.id)\n"
        f"    a.id::text,\n"
        f"    a.hostname[1] AS hostname,\n"
        f"    av.val AS matched_value,\n"
        f"    er.match_value AS matched_against,\n"
        f"    er.enrich_value\n"
        f"{join_clause}\n"
        f"    {order_clause}\n"
        f"LIMIT 50"
    )

    return sql, {"dataset_id": dataset_id}


def build_count_query(
    dataset_id: str,
    match_expr: str,
    value_kind: str,
    match_type: str,
) -> Tuple[str, dict]:
    """
    Build a SELECT that returns the number of distinct assets matched.

    Args:
        dataset_id: UUID of the enrichment dataset.
        match_expr: Resolved SQL expression for the match field (from FieldResolver).
        value_kind: 'array' | 'jsonb_array' | 'scalar' — how to read match_expr.
        match_type: 'exact' or 'cidr'.

    Returns:
        (sql, params) tuple.
    """
    join_clause = _build_join_clause(match_expr, value_kind, match_type)

    sql = (
        f"SELECT COUNT(DISTINCT a.id) AS count\n"
        f"{join_clause}"
    )

    return sql, {"dataset_id": dataset_id}
