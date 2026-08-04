# SPDX-License-Identifier: AGPL-3.0-or-later
import logging
import time
from typing import Any, Dict, List, Optional, Set

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.fields import InvalidFieldError, coerce_field_id
from src.modules.fields.resolver import FieldResolver
from src.modules.posture_rules.models.rule import PostureState, criteria_logic_to_sql
from src.modules.posture_rules.repositories.posture_repository import PostureRepository
from src.modules.tag_rules.services.query_engine_service import QueryEngineService

logger = logging.getLogger(__name__)


class PostureEvaluationService:
    """Computes assets.posture_rules values by evaluating enabled sub-rules.

    Per-asset per-category storage shape: {"state": "true"|"false"|"n/a", "output": str|None}

    Evaluation semantics (per category, OR across sub-rules; sub-rules ordered
    by created_at ascending):
    - PASS  — asset matched target_filters of at least one sub-rule AND
              satisfied that sub-rule's compliance_criteria.
    - FAIL  — asset matched target_filters of at least one sub-rule but
              never satisfied any sub-rule's criteria.
    - NA    — no enabled sub-rules for this category, OR asset matched no
              sub-rule's target_filters, OR asset's category is not in the
              category's applicable_categories (when that list is non-empty).

    Output: set by the first passing sub-rule that has `output_field` configured.
            Null on FAIL/NA.
    """

    POSTURE_EVAL_LOCK_ID = 48577  # Distinct from asset_generation's 48576

    def __init__(
        self,
        repository: PostureRepository,
        query_engine: QueryEngineService,
        db_adapter: PostgreSQLAdapter,
        resolver: FieldResolver,
    ):
        self.repo = repository
        self.engine = query_engine
        self.db = db_adapter
        self.resolver = resolver

    def evaluate_all(self) -> Dict[str, Any]:
        t0 = time.time()

        lock_row = self.db.execute_query(
            "SELECT pg_try_advisory_lock(%s)", (self.POSTURE_EVAL_LOCK_ID,), fetch=True,
        )
        if not lock_row or not lock_row[0].get("pg_try_advisory_lock"):
            duration_ms = int((time.time() - t0) * 1000)
            logger.warning(
                "Posture evaluation skipped: another evaluation is in progress",
            )
            return {"updated_count": 0, "duration_ms": duration_ms, "skipped": True}

        try:
            asset_ids = self.engine.asset_ids_matching([], db=self.db, logic="AND")
            categories = self.repo.list_categories()
            enabled = self.repo.list_enabled_sub_rules()

            has_constraints = any(c.get("applicable_categories") for c in categories)
            asset_categories = (
                self.repo.fetch_asset_categories(asset_ids) if has_constraints else {}
            )

            per_category: Dict[str, List[Dict[str, Any]]] = {c["id"]: [] for c in categories}
            for sr in enabled:
                per_category.setdefault(sr["category_id"], []).append(sr)

            result: Dict[str, Dict[str, Dict[str, Any]]] = {aid: {} for aid in asset_ids}

            for cat in categories:
                cat_id = cat["id"]
                applicable = {a.lower() for a in (cat.get("applicable_categories") or [])}
                if applicable:
                    applicable_ids = {
                        aid for aid in asset_ids
                        if asset_categories.get(aid, "") in applicable
                    }
                else:
                    applicable_ids = set(asset_ids)

                sub_rules = per_category.get(cat_id, [])
                cat_result = self._evaluate_category(sub_rules, applicable_ids)

                for aid in asset_ids:
                    if aid in applicable_ids:
                        result[aid][cat_id] = cat_result[aid]
                    else:
                        result[aid][cat_id] = {"state": PostureState.NA.value, "output": None}

            updated = self.repo.write_asset_posture_rules(list(result.items()))
            self.repo.mark_all_evaluated()
            try:
                self.repo.write_tier_snapshot()
            except Exception as e:
                logger.warning("Failed to write posture tier snapshot: %s", e)
            try:
                self.repo.write_coverage_snapshot()
            except Exception as e:
                logger.warning("Failed to write posture coverage snapshot: %s", e)
        finally:
            try:
                self.db.execute_query(
                    "SELECT pg_advisory_unlock(%s)", (self.POSTURE_EVAL_LOCK_ID,),
                )
            except Exception as e:
                logger.warning("Failed to release posture evaluation lock: %s", e)

        duration_ms = int((time.time() - t0) * 1000)
        logger.info(
            "Posture evaluation: assets=%d categories=%d sub_rules=%d duration_ms=%d",
            len(asset_ids), len(categories), len(enabled), duration_ms,
        )
        return {"updated_count": updated, "duration_ms": duration_ms, "skipped": False}

    def evaluate_category(self, category_id: str) -> Dict[str, Any]:
        """Evaluate one posture category and merge its per-asset state into
        `assets.posture_rules` via the JSONB `||` operator.

        Called synchronously after sub-rule CUD mutations so the stats query
        + explorer filters reflect the change immediately, without waiting
        for the next scheduled `evaluate_all`. Shares the advisory lock with
        `evaluate_all` — if a full evaluation is in flight, skip; the running
        pass will incorporate the latest rule state anyway.
        """
        t0 = time.time()

        lock_row = self.db.execute_query(
            "SELECT pg_try_advisory_lock(%s)", (self.POSTURE_EVAL_LOCK_ID,), fetch=True,
        )
        if not lock_row or not lock_row[0].get("pg_try_advisory_lock"):
            return {
                "updated_count": 0,
                "duration_ms": int((time.time() - t0) * 1000),
                "skipped": True,
            }

        try:
            cat = self.repo.get_category(category_id)
            if cat is None:
                return {
                    "updated_count": 0,
                    "duration_ms": int((time.time() - t0) * 1000),
                    "skipped": False,
                }

            sub_rules = [
                sr for sr in self.repo.list_enabled_sub_rules()
                if sr["category_id"] == category_id
            ]
            asset_ids = self.engine.asset_ids_matching([], db=self.db, logic="AND")

            applicable = {a.lower() for a in (cat.get("applicable_categories") or [])}
            if applicable:
                asset_cats = self.repo.fetch_asset_categories(asset_ids)
                applicable_ids = {
                    aid for aid in asset_ids
                    if asset_cats.get(aid, "") in applicable
                }
            else:
                applicable_ids = set(asset_ids)

            cat_result = self._evaluate_category(sub_rules, applicable_ids)
            na = {"state": PostureState.NA.value, "output": None}
            rows = [
                (aid, cat_result[aid] if aid in applicable_ids else na)
                for aid in asset_ids
            ]
            updated = self.repo.patch_asset_category_state(category_id, rows)
        finally:
            try:
                self.db.execute_query(
                    "SELECT pg_advisory_unlock(%s)", (self.POSTURE_EVAL_LOCK_ID,),
                )
            except Exception as e:
                logger.warning("Failed to release posture evaluation lock: %s", e)

        duration_ms = int((time.time() - t0) * 1000)
        logger.info(
            "Posture category eval: category=%s sub_rules=%d assets=%d duration_ms=%d",
            category_id, len(sub_rules), len(asset_ids), duration_ms,
        )
        return {"updated_count": updated, "duration_ms": duration_ms, "skipped": False}

    def _evaluate_category(
        self,
        sub_rules: List[Dict[str, Any]],
        asset_ids: Set[str],
    ) -> Dict[str, Dict[str, Any]]:
        if not sub_rules:
            return {aid: {"state": PostureState.NA.value, "output": None} for aid in asset_ids}

        any_match: Set[str] = set()
        any_pass: Set[str] = set()
        # Maps asset_id to output value from the first passing sub-rule (with output_field)
        output_map: Dict[str, Any] = {}

        for sr in sub_rules:
            matched = self.engine.asset_ids_matching(
                sr["target_filters"], db=self.db, logic="AND"
            )
            if not matched:
                continue
            any_match |= matched

            criteria = sr["compliance_criteria"]
            criteria_matched = self.engine.asset_ids_matching(
                criteria.get("conditions", []),
                db=self.db,
                logic=criteria_logic_to_sql(criteria.get("logic", "all")),
            )
            passed = matched & criteria_matched
            any_pass |= passed

            output_mode: Optional[str] = sr.get("output_mode")
            # output_value from DB is a JSONB dict: {"mode": "field", "field_id": {...}}
            # or {"mode": "text", "text": "..."}.  Legacy rows may be absent/None.
            raw_output: Optional[dict] = sr.get("output_value")
            if output_mode and raw_output and passed:
                # First-wins: only set output for assets without one yet
                pending = [aid for aid in passed if aid not in output_map]
                if pending:
                    if output_mode == "text":
                        text_val = raw_output.get("text") if isinstance(raw_output, dict) else None
                        if text_val:
                            for aid in pending:
                                output_map[aid] = text_val
                    elif output_mode == "field":
                        field_id_raw = (
                            raw_output.get("field_id")
                            if isinstance(raw_output, dict)
                            else None
                        )
                        if field_id_raw:
                            try:
                                field_id = coerce_field_id(field_id_raw)
                                output_map.update(
                                    self.repo.fetch_asset_field_values(
                                        pending, field_id, self.resolver
                                    )
                                )
                            except InvalidFieldError as e:
                                logger.warning(
                                    "Cannot resolve output field for sub_rule %s: %s",
                                    sr.get("id"), e,
                                )

        out: Dict[str, Dict[str, Any]] = {}
        for aid in asset_ids:
            if aid in any_pass:
                out[aid] = {"state": PostureState.PASS.value, "output": output_map.get(aid)}
            elif aid in any_match:
                out[aid] = {"state": PostureState.FAIL.value, "output": None}
            else:
                out[aid] = {"state": PostureState.NA.value, "output": None}
        return out
