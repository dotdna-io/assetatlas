# SPDX-License-Identifier: AGPL-3.0-or-later
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.modules.fields import catalog_version
from src.modules.fields.unified.materialize import outside_request_scope
from src.modules.posture_rules.models.rule import (
    PostureSubRuleCreate,
    PostureSubRuleUpdate,
    PostureCategoryUpdateRequest,
    SubRuleAction,
)
from src.modules.posture_rules.repositories.posture_repository import PostureRepository
from src.modules.shared import asset_data_version

if TYPE_CHECKING:
    from src.modules.posture_rules.services.posture_evaluation_service import (
        PostureEvaluationService,
    )

logger = logging.getLogger(__name__)


class CategoryNotFoundError(Exception):
    pass


class SubRuleNotFoundError(Exception):
    pass


def _serialise_output_value(payload: "PostureSubRuleCreate | PostureSubRuleUpdate") -> Optional[dict]:
    """Convert typed output fields to the JSONB dict stored in output_value column.

    field mode: {"mode": "field", "field_id": <FieldId dict>}
    text mode:  {"mode": "text", "text": <str>}
    """
    if payload.output_mode == "field" and payload.output_field_id is not None:
        return {"mode": "field", "field_id": payload.output_field_id.model_dump()}
    if payload.output_mode == "text" and payload.output_text:
        return {"mode": "text", "text": payload.output_text}
    return None


class PostureSubRuleManagementService:
    def __init__(
        self,
        repository: PostureRepository,
        evaluator: "Optional[PostureEvaluationService]" = None,
        auto_execute: bool = True,
    ):
        self.repo = repository
        # Optional so unit tests that mock the repo don't have to wire a full
        # evaluator stack. When set (production path), CUD mutations trigger
        # a single-category re-eval so the UI reflects the change without
        # waiting for the scheduled evaluate_all.
        self.evaluator = evaluator
        self.auto_execute = auto_execute

    def _maybe_reeval(self, category_id: str) -> None:
        """Best-effort single-category re-eval after a mutation.

        Swallow-and-log on failure: the primary write already succeeded; a
        transient eval failure must not surface as a 500 for the CUD call.
        """
        if not (self.auto_execute and self.evaluator is not None):
            return
        try:
            self.evaluator.evaluate_category(category_id)
        except Exception as e:
            logger.warning(
                "Post-mutation posture re-eval failed for category '%s': %s",
                category_id, e,
            )

    def _bump_data_stamp_post_commit(self) -> None:
        """Bump the asset-data stamp AFTER the clear is durable (#1410).

        clear_category_state drops a category from assets.posture_rules
        fleet-wide, so the posture_state:/posture_output: distinct values
        change and the Explorer field-values cache must invalidate. Entering
        outside_request_scope ends the request scope — the commit — first, so
        a concurrent /values request can never read the new stamp against the
        pre-clear snapshot and cache stale posture values under the new key.
        This service only runs on the request path (sub-rule CUD routes).
        """
        with outside_request_scope(self.repo.db.conn_manager):
            asset_data_version.bump()

    def _record_history(
        self,
        before_state: Optional[Dict[str, Any]],
        after_state: Optional[Dict[str, Any]],
        action: SubRuleAction,
        user_id: str,
        username: Optional[str],
    ) -> None:
        """Write an audit entry. Best-effort — log-and-swallow on failure so
        a broken audit table doesn't block the primary write.
        """
        reference = after_state or before_state  # one is always populated
        try:
            self.repo.insert_sub_rule_history(
                sub_rule_id=str(reference["id"]),
                category_id=reference["category_id"],
                action=action.value,
                before_state=before_state,
                after_state=after_state,
                changed_by=user_id,
                changed_by_username=username,
            )
        except Exception as e:
            logger.warning(
                "Failed to record posture sub-rule history (sub_rule_id=%s action=%s): %s",
                reference.get("id"), action.value, e,
            )

    def create(
        self,
        category_id: str,
        payload: PostureSubRuleCreate,
        user_id: str,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self.repo.get_category(category_id) is None:
            raise CategoryNotFoundError(f"Unknown posture category '{category_id}'")
        row = self.repo.create_sub_rule(
            category_id=category_id,
            name=payload.name,
            description=payload.description,
            enabled=payload.enabled,
            target_filters=[f.model_dump() for f in payload.target_filters],
            compliance_criteria=payload.compliance_criteria.model_dump(),
            output_mode=payload.output_mode,
            output_value=_serialise_output_value(payload),
        )
        self._record_history(None, row, SubRuleAction.CREATE, user_id, username)
        self._maybe_reeval(category_id)
        return row

    def update(
        self,
        sub_rule_id: str,
        payload: PostureSubRuleUpdate,
        user_id: str,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        before = self.repo.get_sub_rule(sub_rule_id)
        result = self.repo.update_sub_rule(
            sub_rule_id=sub_rule_id,
            name=payload.name,
            description=payload.description,
            enabled=payload.enabled,
            target_filters=[f.model_dump() for f in payload.target_filters],
            compliance_criteria=payload.compliance_criteria.model_dump(),
            output_mode=payload.output_mode,
            output_value=_serialise_output_value(payload),
        )
        if result is None:
            raise SubRuleNotFoundError(f"Posture sub-rule '{sub_rule_id}' not found")
        self._record_history(before, result, SubRuleAction.UPDATE, user_id, username)
        self._maybe_reeval(result["category_id"])
        return result

    def toggle(
        self,
        sub_rule_id: str,
        enabled: bool,
        user_id: str,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        before = self.repo.get_sub_rule(sub_rule_id)
        result = self.repo.toggle_sub_rule(sub_rule_id, enabled)
        if result is None:
            raise SubRuleNotFoundError(f"Posture sub-rule '{sub_rule_id}' not found")
        # Disabling the last enabled rule in a category makes it effectively
        # rule-less; clear prior evaluation state so the UI + charts + filters
        # reflect that immediately instead of waiting for the scheduled eval.
        # Otherwise re-eval so the remaining rule set is reflected.
        category_id = result["category_id"]
        if not enabled and self.repo.count_enabled_sub_rules(category_id) == 0:
            self.repo.clear_category_state(category_id)
            self._bump_data_stamp_post_commit()
        else:
            self._maybe_reeval(category_id)
        self._record_history(before, result, SubRuleAction.TOGGLE, user_id, username)
        return result

    def delete(
        self,
        sub_rule_id: str,
        user_id: str,
        username: Optional[str] = None,
    ) -> None:
        before = self.repo.get_sub_rule(sub_rule_id)
        if not self.repo.delete_sub_rule(sub_rule_id):
            raise SubRuleNotFoundError(f"Posture sub-rule '{sub_rule_id}' not found")
        # Same rationale as toggle: if the category has no enabled rules left,
        # drop its stale per-asset state now. Otherwise re-eval the remaining set.
        category_id = before["category_id"]
        if self.repo.count_enabled_sub_rules(category_id) == 0:
            self.repo.clear_category_state(category_id)
            self._bump_data_stamp_post_commit()
        else:
            self._maybe_reeval(category_id)
        self._record_history(before, None, SubRuleAction.DELETE, user_id, username)

    def get_history(self, sub_rule_id: str) -> List[Dict[str, Any]]:
        return self.repo.list_sub_rule_history(sub_rule_id)

    def update_applicable_categories(
        self,
        category_id: str,
        payload: PostureCategoryUpdateRequest,
    ) -> Dict[str, Any]:
        """Replace applicable_categories for a posture category.

        Raises CategoryNotFoundError if the category id does not exist.
        Validation of the input list (lowercase, known tokens) happens in the
        Pydantic model.
        """
        result = self.repo.update_category_applicable_categories(
            category_id, payload.applicable_categories,
        )
        if result is None:
            raise CategoryNotFoundError(f"Unknown posture category '{category_id}'")
        # The catalog's posture entries derive from posture_categories rows;
        # bump the shared stamp so every worker's FieldCatalog picks up the
        # category change within its check interval (#1413). Never raises.
        catalog_version.bump()
        # Applicable_categories change flips which assets are in-scope vs N/A.
        self._maybe_reeval(category_id)
        return result

    def reorder_sub_rules(self, category_id: str, ordered_ids: List[str]) -> None:
        """Reorder sub-rules within a category by the given id sequence.

        Raises CategoryNotFoundError if category doesn't exist.
        Raises SubRuleNotFoundError if any id doesn't belong to the category.
        """
        if self.repo.get_category(category_id) is None:
            raise CategoryNotFoundError(f"Unknown posture category '{category_id}'")
        ok = self.repo.reorder_sub_rules(category_id, ordered_ids)
        if not ok:
            raise SubRuleNotFoundError(
                "One or more sub-rules not found in this category"
            )
        # Reorder changes first-wins output semantics; re-eval for correct output.
        self._maybe_reeval(category_id)
