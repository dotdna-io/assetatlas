# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import logging
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.fields.models import FieldId
from src.modules.fields.types import FieldDataType

if TYPE_CHECKING:
    from src.modules.fields.resolver import FieldResolver

logger = logging.getLogger(__name__)


class PostureRepository:
    """CRUD + batch writes for the posture_rules system.

    Uses named-placeholder idiom (%(name)s) consistent with execute_query().
    """

    def __init__(self, db_adapter: PostgreSQLAdapter):
        self.db = db_adapter

    def list_categories(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT id, name, description, icon, applicable_categories,
                   display_order, created_at, last_evaluated_at
            FROM posture_categories
            ORDER BY display_order ASC
        """
        return self.db.execute_query(sql, {})

    def get_category(self, category_id: str) -> Optional[Dict[str, Any]]:
        sql = """
            SELECT id, name, description, icon, applicable_categories,
                   display_order, created_at, last_evaluated_at
            FROM posture_categories
            WHERE id = %(id)s
        """
        rows = self.db.execute_query(sql, {"id": category_id})
        return rows[0] if rows else None

    def mark_all_evaluated(self) -> None:
        """Stamp posture_categories.last_evaluated_at = now() for every category.

        Called by PostureEvaluationService at the end of a successful evaluate_all.
        """
        self.db.execute_query(
            "UPDATE posture_categories SET last_evaluated_at = now()",
            {},
            fetch=False,
        )

    def update_category_applicable_categories(
        self, category_id: str, applicable_categories: List[str],
    ) -> Optional[Dict[str, Any]]:
        """Replace the applicable_categories JSONB for a single category."""
        sql = """
            UPDATE posture_categories
            SET applicable_categories = %(applicable)s::jsonb
            WHERE id = %(id)s
            RETURNING id, name, description, icon, applicable_categories,
                      display_order, created_at, last_evaluated_at
        """
        rows = self.db.execute_query(sql, {
            "id": category_id,
            "applicable": json.dumps(applicable_categories),
        })
        return rows[0] if rows else None

    def fetch_asset_categories(self, asset_ids: List[str]) -> Dict[str, str]:
        """Return {asset_id: lowercase_category} for the given asset ids.

        Empty/NULL category is returned as '' (not 'unknown') — the evaluator's
        membership test against applicable_categories will then exclude it.
        """
        if not asset_ids:
            return {}
        sql = """
            SELECT id, COALESCE(LOWER(NULLIF(TRIM(category), '')), '') AS category
            FROM assets
            WHERE id = ANY(%(ids)s::uuid[])
        """
        rows = self.db.execute_query(sql, {"ids": list(asset_ids)})
        return {str(r["id"]): r["category"] for r in rows}

    def list_sub_rules_by_category(self, category_id: str) -> List[Dict[str, Any]]:
        sql = """
            SELECT id, category_id, name, description, enabled,
                   target_filters, compliance_criteria, output_mode, output_value,
                   display_order, created_at, updated_at
            FROM posture_sub_rules
            WHERE category_id = %(category_id)s
            ORDER BY display_order ASC, created_at ASC
        """
        return self.db.execute_query(sql, {"category_id": category_id})

    def list_all_sub_rules(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT id, category_id, name, description, enabled,
                   target_filters, compliance_criteria, output_mode, output_value,
                   display_order, created_at, updated_at
            FROM posture_sub_rules
            ORDER BY category_id ASC, display_order ASC, created_at ASC
        """
        return self.db.execute_query(sql, {})

    def list_enabled_sub_rules(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT id, category_id, name, target_filters, compliance_criteria,
                   output_mode, output_value, display_order, created_at
            FROM posture_sub_rules
            WHERE enabled = true
            ORDER BY display_order ASC, created_at ASC
        """
        return self.db.execute_query(sql, {})

    def get_sub_rule(self, sub_rule_id: str) -> Optional[Dict[str, Any]]:
        sql = """
            SELECT id, category_id, name, description, enabled,
                   target_filters, compliance_criteria, output_mode, output_value,
                   display_order, created_at, updated_at
            FROM posture_sub_rules
            WHERE id = %(id)s::uuid
        """
        rows = self.db.execute_query(sql, {"id": sub_rule_id})
        return rows[0] if rows else None

    def create_sub_rule(
        self,
        category_id: str,
        name: str,
        description: Optional[str],
        enabled: bool,
        target_filters: List[Dict[str, Any]],
        compliance_criteria: Dict[str, Any],
        output_mode: Optional[str] = None,
        output_value: Optional[dict] = None,
    ) -> Dict[str, Any]:
        sql = """
            INSERT INTO posture_sub_rules
                (category_id, name, description, enabled, target_filters, compliance_criteria,
                 output_mode, output_value, display_order)
            VALUES
                (%(category_id)s, %(name)s, %(description)s, %(enabled)s,
                 %(target_filters)s::jsonb, %(compliance_criteria)s::jsonb,
                 %(output_mode)s, %(output_value)s::jsonb,
                 COALESCE(
                    (SELECT MAX(display_order) + 1
                     FROM posture_sub_rules
                     WHERE category_id = %(category_id)s),
                    0
                 ))
            RETURNING id, category_id, name, description, enabled,
                      target_filters, compliance_criteria, output_mode, output_value,
                      display_order, created_at, updated_at
        """
        rows = self.db.execute_query(sql, {
            "category_id": category_id,
            "name": name,
            "description": description,
            "enabled": enabled,
            "target_filters": json.dumps(target_filters),
            "compliance_criteria": json.dumps(compliance_criteria),
            "output_mode": output_mode,
            "output_value": json.dumps(output_value) if output_value is not None else None,
        })
        return rows[0]

    def update_sub_rule(
        self,
        sub_rule_id: str,
        name: str,
        description: Optional[str],
        enabled: bool,
        target_filters: List[Dict[str, Any]],
        compliance_criteria: Dict[str, Any],
        output_mode: Optional[str] = None,
        output_value: Optional[dict] = None,
    ) -> Optional[Dict[str, Any]]:
        sql = """
            UPDATE posture_sub_rules
            SET name = %(name)s,
                description = %(description)s,
                enabled = %(enabled)s,
                target_filters = %(target_filters)s::jsonb,
                compliance_criteria = %(compliance_criteria)s::jsonb,
                output_mode = %(output_mode)s,
                output_value = %(output_value)s::jsonb
            WHERE id = %(id)s::uuid
            RETURNING id, category_id, name, description, enabled,
                      target_filters, compliance_criteria, output_mode, output_value,
                      display_order, created_at, updated_at
        """
        rows = self.db.execute_query(sql, {
            "id": sub_rule_id,
            "name": name,
            "description": description,
            "enabled": enabled,
            "target_filters": json.dumps(target_filters),
            "compliance_criteria": json.dumps(compliance_criteria),
            "output_mode": output_mode,
            "output_value": json.dumps(output_value) if output_value is not None else None,
        })
        return rows[0] if rows else None

    def toggle_sub_rule(self, sub_rule_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
        sql = """
            UPDATE posture_sub_rules
            SET enabled = %(enabled)s
            WHERE id = %(id)s::uuid
            RETURNING id, category_id, name, description, enabled,
                      target_filters, compliance_criteria, output_mode, output_value,
                      display_order, created_at, updated_at
        """
        rows = self.db.execute_query(sql, {"id": sub_rule_id, "enabled": enabled})
        return rows[0] if rows else None

    def delete_sub_rule(self, sub_rule_id: str) -> bool:
        sql = "DELETE FROM posture_sub_rules WHERE id = %(id)s::uuid RETURNING id"
        rows = self.db.execute_query(sql, {"id": sub_rule_id})
        return bool(rows)

    def count_enabled_sub_rules(self, category_id: str) -> int:
        rows = self.db.execute_query(
            "SELECT COUNT(*) AS n FROM posture_sub_rules "
            "WHERE category_id = %(cid)s AND enabled = true",
            {"cid": category_id},
        )
        return int(rows[0]["n"]) if rows else 0

    def clear_category_state(self, category_id: str) -> None:
        """Strip a category's key from every asset's posture_rules JSONB.

        Called when a category drops to zero enabled sub-rules — otherwise the
        last evaluation's pass/fail states linger as phantom coverage in the
        stats query and distort any chart/filter keyed on `posture_state:<cat>`
        until the next scheduled evaluation runs.
        """
        self.db.execute_query(
            "UPDATE assets SET posture_rules = posture_rules - %(cat)s "
            "WHERE posture_rules ? %(cat)s",
            {"cat": category_id},
            fetch=False,
        )

    def insert_sub_rule_history(
        self,
        sub_rule_id: str,
        category_id: str,
        action: str,
        before_state: Optional[Dict[str, Any]],
        after_state: Optional[Dict[str, Any]],
        changed_by: str,
        changed_by_username: Optional[str],
    ) -> None:
        sql = """
            INSERT INTO posture_sub_rule_history
                (sub_rule_id, category_id, action, before_state, after_state,
                 changed_by, changed_by_username)
            VALUES
                (%(sub_rule_id)s::uuid, %(category_id)s, %(action)s,
                 %(before)s::jsonb, %(after)s::jsonb,
                 %(changed_by)s, %(changed_by_username)s)
        """
        self.db.execute_query(sql, {
            "sub_rule_id": sub_rule_id,
            "category_id": category_id,
            "action": action,
            "before": json.dumps(before_state, default=str) if before_state else None,
            "after": json.dumps(after_state, default=str) if after_state else None,
            "changed_by": changed_by,
            "changed_by_username": changed_by_username,
        }, fetch=False)

    def list_sub_rule_history(self, sub_rule_id: str) -> List[Dict[str, Any]]:
        sql = """
            SELECT id, sub_rule_id, category_id, action, before_state, after_state,
                   changed_by, changed_by_username, created_at
            FROM posture_sub_rule_history
            WHERE sub_rule_id = %(id)s::uuid
            ORDER BY created_at DESC
        """
        return self.db.execute_query(sql, {"id": sub_rule_id})

    def latest_history_per_sub_rule(self, sub_rule_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Return {sub_rule_id: latest_entry} for each id that has history."""
        if not sub_rule_ids:
            return {}
        sql = """
            SELECT DISTINCT ON (sub_rule_id)
                   sub_rule_id, action, changed_by, changed_by_username, created_at
            FROM posture_sub_rule_history
            WHERE sub_rule_id = ANY(%(ids)s::uuid[])
            ORDER BY sub_rule_id, created_at DESC
        """
        rows = self.db.execute_query(sql, {"ids": sub_rule_ids})
        return {str(r["sub_rule_id"]): r for r in rows}

    def reorder_sub_rules(self, category_id: str, ordered_ids: List[str]) -> bool:
        """Set display_order for each id in ordered_ids to its position (0-indexed).

        Returns False if any id is not a sub-rule in the given category. Uses a
        single statement with unnest + generate_series to avoid a round-trip per row.
        """
        if not ordered_ids:
            return True

        check_sql = """
            SELECT COUNT(*) AS n
            FROM posture_sub_rules
            WHERE category_id = %(cid)s AND id = ANY(%(ids)s::uuid[])
        """
        row = self.db.execute_query(check_sql, {"cid": category_id, "ids": ordered_ids})
        if not row or row[0]["n"] != len(ordered_ids):
            return False

        update_sql = """
            UPDATE posture_sub_rules AS p
            SET display_order = t.ord
            FROM (
                SELECT UNNEST(%(ids)s::uuid[]) AS id,
                       GENERATE_SERIES(0, %(count)s - 1) AS ord
            ) AS t
            WHERE p.id = t.id AND p.category_id = %(cid)s
        """
        self.db.execute_query(update_sql, {
            "cid": category_id,
            "ids": ordered_ids,
            "count": len(ordered_ids),
        }, fetch=False)
        return True

    def fetch_asset_field_values(
        self,
        asset_ids: List[str],
        field_id: FieldId,
        resolver: "FieldResolver",
    ) -> Dict[str, Any]:
        """Return {asset_id: field_value} for the given asset IDs.

        Resolves field_id through FieldResolver — the SQL expression comes from the
        trusted catalog, not from any user-supplied string. Raises InvalidFieldError
        if field_id is not in the live catalog (caller should handle and skip).
        Array fields (STRING_ARRAY and JSONB_ARRAY) are joined with ', ' for display.
        """
        if not asset_ids or field_id is None:
            return {}

        # resolve() raises InvalidFieldError if field_id is not in live catalog.
        resolved = resolver.resolve(field_id)
        db_column = resolved.where_expr()

        if resolved.data_type == FieldDataType.JSONB_ARRAY:
            expr = (
                f"(SELECT string_agg(value, ', ') "
                f"FROM jsonb_array_elements_text({db_column}))"
            )
        elif resolved.data_type == FieldDataType.STRING_ARRAY:
            expr = f"array_to_string({db_column}, ', ')"
        else:
            expr = db_column

        sql = f"SELECT id, {expr} AS v FROM assets WHERE id = ANY(%(ids)s::uuid[])"
        rows = self.db.execute_query(sql, {"ids": asset_ids})
        return {str(r["id"]): r["v"] for r in rows}

    _WRITE_POSTURE_CHUNK_SIZE = 5000

    def write_asset_posture_rules(
        self, rows: Iterable[Tuple[str, Dict[str, Any]]]
    ) -> int:
        """Batch UPDATE assets.posture_rules from (asset_id, json) tuples.

        Chunked to bound SQL string size + params dict memory on large pipelines.
        Returns the number of input tuples (not the server-reported row count).
        """
        materialised = list(rows)
        if not materialised:
            return 0

        for start in range(0, len(materialised), self._WRITE_POSTURE_CHUNK_SIZE):
            chunk = materialised[start:start + self._WRITE_POSTURE_CHUNK_SIZE]
            values_sql = ", ".join(
                f"(%(id_{i})s::uuid, %(pr_{i})s::jsonb)"
                for i in range(len(chunk))
            )
            params: Dict[str, Any] = {}
            for i, (asset_id, payload) in enumerate(chunk):
                params[f"id_{i}"] = asset_id
                params[f"pr_{i}"] = json.dumps(payload)

            sql = f"""
                UPDATE assets
                SET posture_rules = src.posture_rules
                FROM (VALUES {values_sql}) AS src(asset_id, posture_rules)
                WHERE assets.id = src.asset_id
            """
            self.db.execute_query(sql, params, fetch=False)
        return len(materialised)

    def patch_asset_category_state(
        self,
        category_id: str,
        rows: Iterable[Tuple[str, Dict[str, Any]]],
    ) -> int:
        """Merge a single category's per-asset state into assets.posture_rules.

        Unlike write_asset_posture_rules, this preserves the states of other
        categories via the JSONB `||` operator. Used by post-mutation single-
        category re-evaluation so a CUD on one category doesn't blow away
        states computed by the scheduled evaluate_all for sibling categories.
        """
        materialised = list(rows)
        if not materialised:
            return 0

        for start in range(0, len(materialised), self._WRITE_POSTURE_CHUNK_SIZE):
            chunk = materialised[start:start + self._WRITE_POSTURE_CHUNK_SIZE]
            values_sql = ", ".join(
                f"(%(id_{i})s::uuid, %(state_{i})s::jsonb)"
                for i in range(len(chunk))
            )
            params: Dict[str, Any] = {"cat": category_id}
            for i, (asset_id, state) in enumerate(chunk):
                params[f"id_{i}"] = asset_id
                params[f"state_{i}"] = json.dumps(state)

            sql = f"""
                UPDATE assets
                SET posture_rules =
                    posture_rules || jsonb_build_object(%(cat)s, src.state)
                FROM (VALUES {values_sql}) AS src(asset_id, state)
                WHERE assets.id = src.asset_id
            """
            self.db.execute_query(sql, params, fetch=False)
        return len(materialised)

    def write_tier_snapshot(self) -> None:
        """Insert one row of current (secured, monitored, discovered) totals.

        IS DISTINCT FROM is required (not !=): a missing JSONB key returns
        NULL via ->> and NULL != 'true' is NULL under three-valued logic,
        which would silently drop the asset from the count.
        """
        sql = """
            INSERT INTO posture_tier_snapshots (secured_count, monitored_count, discovered_count)
            SELECT
                COUNT(*) FILTER (WHERE posture_rules -> 'secured' ->> 'state' = 'true'),
                COUNT(*) FILTER (
                    WHERE posture_rules -> 'secured'   ->> 'state' IS DISTINCT FROM 'true'
                      AND posture_rules -> 'monitored' ->> 'state' = 'true'
                ),
                COUNT(*) FILTER (
                    WHERE posture_rules -> 'secured'   ->> 'state' IS DISTINCT FROM 'true'
                      AND posture_rules -> 'monitored' ->> 'state' IS DISTINCT FROM 'true'
                )
            FROM assets
        """
        self.db.execute_query(sql, {}, fetch=False)

    def write_coverage_snapshot(self) -> None:
        """Insert one row per (posture control × asset category) of current
        pass/fail/n_a counts, read from the freshly-evaluated assets.posture_rules.

        The covered/total semantics match get_category_stats: covered = pass,
        total = pass + fail + n_a (the full asset-category population). Stored at
        per-(control, asset_category) granularity so the dashboard coverage-trend
        card can historize coverage. Mirrors write_tier_snapshot()'s once-per-run,
        best-effort contract.
        """
        sql = """
            INSERT INTO posture_coverage_snapshots
                (control_id, asset_category, pass_count, fail_count, na_count)
            SELECT
                c.id,
                COALESCE(NULLIF(LOWER(TRIM(a.category)), ''), 'miscellaneous') AS asset_category,
                COUNT(*) FILTER (WHERE a.posture_rules -> c.id ->> 'state' = 'true')  AS pass_count,
                COUNT(*) FILTER (WHERE a.posture_rules -> c.id ->> 'state' = 'false') AS fail_count,
                COUNT(*) FILTER (WHERE a.posture_rules -> c.id ->> 'state' = 'n/a'
                                    OR a.posture_rules -> c.id IS NULL)               AS na_count
            FROM posture_categories c
            CROSS JOIN assets a
            GROUP BY c.id, asset_category
        """
        self.db.execute_query(sql, {}, fetch=False)

    def get_category_stats(self) -> Dict[str, Dict[str, Any]]:
        """Per-category pass/fail/n_a counts + coverage breakdown by asset category.

        Single CROSS JOIN scan grouped by (posture_category, asset_category);
        aggregate totals rolled up in Python.

        Coverage semantics: `covered` = assets matching the criteria (compliant,
        state 'true' = pass); `total` = every asset in that asset category
        (pass + fail + n/a). So the breakdown reads "compliant / category total",
        not "evaluated / total".
        """
        sql = """
            SELECT c.id AS category_id,
                   COALESCE(NULLIF(TRIM(a.category), ''), 'Unknown') AS asset_category,
                   COUNT(*) FILTER (WHERE a.posture_rules -> c.id ->> 'state' = 'true')  AS pass,
                   COUNT(*) FILTER (WHERE a.posture_rules -> c.id ->> 'state' = 'false') AS fail,
                   COUNT(*) FILTER (WHERE a.posture_rules -> c.id ->> 'state' = 'n/a'
                                       OR a.posture_rules -> c.id IS NULL)               AS n_a
            FROM posture_categories c
            CROSS JOIN assets a
            GROUP BY c.id, asset_category
            ORDER BY c.id, asset_category
        """
        stats: Dict[str, Dict[str, Any]] = {}
        for r in self.db.execute_query(sql, {}):
            cat_id = r["category_id"]
            p, f, n = int(r["pass"]), int(r["fail"]), int(r["n_a"])
            entry = stats.setdefault(cat_id, {
                "pass": 0, "fail": 0, "n_a": 0, "by_asset_category": {},
            })
            entry["pass"] += p
            entry["fail"] += f
            entry["n_a"] += n
            entry["by_asset_category"][r["asset_category"]] = {
                # covered = compliant (pass); total = full category population.
                "covered": p,
                "total": p + f + n,
            }
        return stats
