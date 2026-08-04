# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Query Engine Service for Tag Rules

Compliance evaluation layer. SQL compilation is inherited from
AssetQueryCompiler in src/modules/shared/.
"""

import logging
from typing import Any, Dict, List

from src.modules.shared.asset_query_compiler import AssetQueryCompiler
from src.modules.fields import FieldResolver, coerce_field_id
from src.modules.fields.types import FieldDataType

logger = logging.getLogger(__name__)


class QueryEngineService(AssetQueryCompiler):
    """Tag-rule compliance evaluator. Inherits SQL compilation."""

    def evaluate_compliance(
        self,
        asset_rows: List[Dict[str, Any]],
        compliance_criteria: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        Evaluate compliance criteria against asset rows.

        This is done in-memory after fetching assets, to avoid complex subqueries.

        Args:
            asset_rows: List of asset dictionaries (from database query)
            compliance_criteria: Compliance criteria dict with 'logic' and 'conditions'.
                Conditions should have 'field_id' (typed FieldId or dict) and 'operator'/'value'.

        Returns:
            Dictionary mapping asset_id to 'pass' or 'fail'
        """
        results = {}
        logic = compliance_criteria.get("logic", "all")
        conditions = compliance_criteria.get("conditions", [])

        if not conditions:
            # No conditions means all assets pass
            return {str(asset["id"]): "pass" for asset in asset_rows}

        # Resolve each condition once up-front. Conditions are constant for the
        # whole asset set; resolving per-asset fires N×M catalog lookups.
        resolved_conditions = []
        for condition in conditions:
            if isinstance(condition, dict):
                field_id_raw = condition.get("field_id")
                operator = condition.get("operator")
                value = condition.get("value")
            else:
                field_id_raw = condition.field_id
                operator = condition.operator
                value = condition.value
            field_id = coerce_field_id(field_id_raw)
            op_str = operator.value if hasattr(operator, "value") else str(operator)
            db_column = self._resolver.resolve(field_id).where_expr()
            resolved_conditions.append((field_id, op_str, value, db_column))

        for asset in asset_rows:
            asset_id = str(asset["id"])
            condition_results = [
                self._evaluate_condition(
                    self._extract_asset_value(asset, db_column), op_str, value, field_id
                )
                for (field_id, op_str, value, db_column) in resolved_conditions
            ]

            passed = all(condition_results) if logic == "all" else any(condition_results)
            results[asset_id] = "pass" if passed else "fail"

        return results

    def _get_asset_field_value(self, asset: Dict[str, Any], field_id) -> Any:
        """Extract an asset's value for a given FieldId via the resolver.

        Convenience wrapper around :meth:`_extract_asset_value` that does the
        resolver lookup. Prefer :meth:`_extract_asset_value` in hot paths that
        iterate the same field across many assets.
        """
        db_column = self._resolver.resolve(field_id).where_expr()
        return self._extract_asset_value(asset, db_column)

    def _extract_asset_value(self, asset: Dict[str, Any], db_column: str) -> Any:
        """Extract a value from an asset dict given a pre-resolved SQL expression.

        Handles flat, nested, and COALESCE JSONB paths.
        """

        # Handle COALESCE: try each path, return first non-null
        if db_column.upper().startswith("COALESCE(") and db_column.endswith(")"):
            inner = db_column[9:-1]
            for path in inner.split(","):
                val = self._navigate_jsonb_path(asset, path.strip())
                if val is not None:
                    return val
            return None

        # Handle JSONB fields (both flat and nested)
        if "->" in db_column:
            return self._navigate_jsonb_path(asset, db_column)
        else:
            # Direct column access
            return asset.get(db_column)

    @staticmethod
    def _navigate_jsonb_path(asset: Dict[str, Any], db_column: str) -> Any:
        """Navigate a JSONB path expression against an asset dictionary.

        Handles both ``->`` (jsonb) and ``->>`` (text) operators. Un-doubles
        any doubled single quotes produced by _escape_literal() so the parsed
        dict keys match the original JSON keys (e.g. ``O''Brien`` → ``O'Brien``).
        """
        # Normalise ->> and -> to the same delimiter, then split.
        normalised = db_column.replace("->>'", "->'").replace("->'", "->")
        parts = normalised.split("->")
        if len(parts) < 2:
            return None
        cleaned = [parts[0]]  # root column — no quoting
        for p in parts[1:]:
            p = p.rstrip("'")       # strip the closing quote of the key literal
            p = p.replace("''", "'")  # un-double escaped quotes from _escape_literal
            cleaned.append(p)
        json_data = asset.get(cleaned[0], {})
        if not isinstance(json_data, dict):
            return None
        for key in cleaned[1:]:
            if isinstance(json_data, dict):
                json_data = json_data.get(key)
            else:
                return None
        return json_data

    def _evaluate_condition(
        self,
        asset_value: Any,
        operator: str,
        filter_value,
        field_id
    ) -> bool:
        """Evaluate one condition in-memory via the operator registry (superset).

        ``match`` infers list-vs-scalar from ``asset_value``'s runtime type, so
        ``field_id`` is no longer needed for routing (kept in the signature
        because the caller passes it positionally). LOUD (§6.2): an unresolved
        operator raises rather than silently returning False.

        Args:
            asset_value: Actual value from asset
            operator: Filter operator string
            filter_value: Expected value from filter
            field_id: Typed FieldId (unused — retained for caller compatibility)

        Returns:
            True if condition matches, False otherwise
        """
        from src.modules.fields.operators import (
            resolve as resolve_operator, OperatorResolutionError,
        )
        try:
            op = resolve_operator(operator)
        except OperatorResolutionError:
            logger.error("Unknown operator %r in compliance condition", operator)
            raise
        return op.match(asset_value, filter_value, None)
