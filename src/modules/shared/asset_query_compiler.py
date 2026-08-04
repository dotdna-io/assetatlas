# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Query Compiler

Shared SQL compiler: translates filter conditions into parameterized SQL
queries against the assets table. Used by tag_rules (compliance evaluation)
and alert_rules (filter-based user rule evaluation).

Pure compilation — no tag-rule-specific logic.
"""

import logging
from typing import List, Dict, Set, Tuple, Any, Optional

from src.modules.fields import (
    FieldResolver,
    InvalidFieldError,
    OperatorResolutionError,
    coerce_field_id,
)

logger = logging.getLogger(__name__)


class AssetQueryCompiler:
    """Translates filter conditions to parameterized SQL against assets."""

    def __init__(self, resolver: FieldResolver):
        self._resolver = resolver

    def build_where_clause(
        self,
        filters: List,
        logic: str = "AND"
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Build SQL WHERE clause from filter conditions.

        Args:
            filters: List of FilterCondition Pydantic models or dicts with
                     'field_id', 'operator', 'value'.
            logic: Logic operator between filters: "AND" or "OR" (default: "AND")

        Returns:
            Tuple of (where_clause_string, parameters_dict)
        """
        if not filters:
            return "", {}

        param_counter = [0]  # Mutable container for thread-safe local counter
        clauses = []
        params = {}

        for filter_condition in filters:
            if isinstance(filter_condition, dict):
                field_id_raw = filter_condition.get("field_id")
                operator = filter_condition.get("operator")
                value = filter_condition.get("value")
            else:  # Pydantic FilterCondition model
                field_id_raw = filter_condition.field_id
                operator = filter_condition.operator
                value = filter_condition.value

            field_id = coerce_field_id(field_id_raw)

            if not field_id or not operator:
                logger.warning(f"Invalid filter condition: {filter_condition}")
                continue

            # Normalise operator to string value
            op_str = operator.value if hasattr(operator, "value") else str(operator)

            try:
                clause, filter_params = self._build_single_condition(
                    field_id, op_str, value, param_counter
                )
            except OperatorResolutionError:
                # Loud failure (§6.2): an unmapped operator is a bug, not a
                # droppable clause. Re-raise so it surfaces in logs/4xx instead
                # of silently dropping the condition (which would widen results).
                logger.error("Unknown operator %r in filter %r", op_str, filter_condition)
                raise
            except InvalidFieldError as e:
                # An unresolvable field is the existing tolerated case: skip the
                # filter (a non-live FieldId), matching prior behaviour.
                logger.warning(f"Skipping filter with unresolvable field for {field_id}: {e}")
                continue
            if clause:
                clauses.append(clause)
                params.update(filter_params)

        if not clauses:
            return "", {}

        # Combine all clauses with the specified logic operator
        logic_operator = logic.upper()
        if logic_operator not in ("AND", "OR"):
            logic_operator = "AND"

        where_clause = f" {logic_operator} ".join(clauses)
        return where_clause, params

    @staticmethod
    def _parse_value_list(value: Any) -> List[str]:
        """Normalise a filter value for IN-style operators.

        Accepts a list (UI-submitted) or a comma-separated string (seed rules).
        Returns a clean list of non-empty trimmed strings.
        """
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return [v.strip() for v in str(value).split(",") if v.strip()]

    def _build_single_condition(
        self,
        field_id,
        operator: str,
        value,
        param_counter: list
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Build SQL condition for a single filter via the operator registry.

        Resolves the field itself, then delegates clause construction to the
        operator's ``build_sql``. Routing (scalar / native-array / jsonb-array /
        tag-boolean) is decided by the registry from the ResolvedField passed in
        via ``OperatorCtx.field`` — no per-type label branches here.

        Args:
            field_id: Typed FieldId (StaticField, TagField, ExtendedAttributeField, …)
            operator: Filter operator spelling (code, spaced label, or alias)
            value: Filter value
            param_counter: Mutable list containing current counter value

        Returns:
            Tuple of (sql_condition, parameters_dict)

        Raises:
            OperatorResolutionError: if ``operator`` maps to no registry operator
                (loud failure — §6.2; surfaced by ``build_where_clause``).
        """
        from src.modules.fields.operators import (
            resolve as resolve_operator, OperatorCtx,
        )

        resolved = self._resolver.resolve(field_id)
        db_column = resolved.where_expr()
        op = resolve_operator(operator)   # raises OperatorResolutionError (loud)

        # Binary/list ops carry a value; a missing value is a no-op clause.
        # Unary ops ("none" arity) never bind a value and are always emitted.
        if op.arity != "none" and (value is None or value == ""):
            return "", {}

        # Normalise list-arity values once (replaces the value.split(',') divergence).
        norm = self._parse_value_list(value) if op.arity == "list" else value

        param_key = self._get_param_name(param_counter)
        ctx = OperatorCtx(field=resolved, param_key=param_key, params={})
        frag, _ = op.build_sql(db_column, norm, ctx)
        return frag, ctx.params

    def _get_param_name(self, param_counter: list) -> str:
        """
        Generate a unique parameter name for query parameterization.

        Args:
            param_counter: Mutable list containing current counter value

        Returns:
            Parameter name like "param_0", "param_1", etc.
        """
        name = f"param_{param_counter[0]}"
        param_counter[0] += 1
        return name

    def asset_ids_matching(
        self,
        filters: List,
        db: Any,
        logic: str = "AND",
    ) -> Set[str]:
        """Return the set of asset IDs whose rows match the given filters.

        Args:
            filters: List of FilterCondition models or dicts with 'field_id', 'operator', 'value'.
            db: A db_adapter instance (PostgreSQLAdapter) used to run the query.
            logic: Logic operator joining filters — "AND" (default) or "OR".

        Returns:
            Set of asset ID strings.
        """
        where_sql, params = self.build_where_clause(filters, logic=logic)
        sql = "SELECT id FROM assets"
        if where_sql:
            sql += f" WHERE {where_sql}"
        rows = db.execute_query(sql, params)
        return {str(r["id"]) for r in rows}

    def build_asset_query(
        self,
        target_filters: List,
        select_columns: Optional[List[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Build complete SQL query for selecting assets based on filters.

        Args:
            target_filters: List of filter conditions
            select_columns: Columns to select (default: all with *)
            limit: Optional LIMIT clause
            offset: Optional OFFSET clause

        Returns:
            Tuple of (sql_query, parameters_dict)
        """
        # Build WHERE clause
        where_clause, params = self.build_where_clause(target_filters, logic="AND")

        # Build SELECT clause
        if select_columns:
            select_clause = ", ".join(select_columns)
        else:
            select_clause = "*"

        # Build complete query
        query = f"SELECT {select_clause} FROM assets"

        if where_clause:
            query += f" WHERE {where_clause}"

        if limit:
            query += f" LIMIT {limit}"

        if offset:
            query += f" OFFSET {offset}"

        return query, params

    def build_count_query(
        self,
        target_filters: List
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Build SQL COUNT query for counting assets matching filters.

        This is optimized for the preview endpoint - uses COUNT(*) only.

        Args:
            target_filters: List of filter conditions

        Returns:
            Tuple of (sql_query, parameters_dict)
        """
        where_clause, params = self.build_where_clause(target_filters, logic="AND")

        query = "SELECT COUNT(*) FROM assets"

        if where_clause:
            query += f" WHERE {where_clause}"

        return query, params
