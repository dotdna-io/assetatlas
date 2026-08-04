# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Any, Dict, List, Optional

from src.modules.posture_rules.repositories.posture_repository import PostureRepository

_EMPTY_STATS = {"pass": 0, "fail": 0, "n_a": 0, "by_asset_category": {}}


class PostureCategoryReadService:
    def __init__(self, repository: PostureRepository):
        self.repo = repository

    def _enrich_with_last_edit(self, sub_rules: List[Dict[str, Any]]) -> None:
        """Attach `last_edit` metadata to each sub-rule in-place."""
        rule_ids = [str(sr["id"]) for sr in sub_rules]
        latest = self.repo.latest_history_per_sub_rule(rule_ids)
        for sr in sub_rules:
            entry = latest.get(str(sr["id"]))
            sr["last_edit"] = (
                {
                    "action": entry["action"],
                    "changed_by": entry["changed_by"],
                    "changed_by_username": entry.get("changed_by_username"),
                    "at": entry["created_at"],
                }
                if entry else None
            )

    def list_categories(self) -> Dict[str, Any]:
        categories = self.repo.list_categories()
        stats = self.repo.get_category_stats()
        all_sub_rules = self.repo.list_all_sub_rules()
        self._enrich_with_last_edit(all_sub_rules)

        by_category: Dict[str, list] = {}
        for sr in all_sub_rules:
            by_category.setdefault(sr["category_id"], []).append(sr)

        enriched = [
            {
                **cat,
                "sub_rules": by_category.get(cat["id"], []),
                "stats": stats.get(cat["id"], _EMPTY_STATS),
            }
            for cat in categories
        ]
        return {"categories": enriched}

    def get_category(self, category_id: str) -> Optional[Dict[str, Any]]:
        cat = self.repo.get_category(category_id)
        if cat is None:
            return None
        sub_rules = self.repo.list_sub_rules_by_category(category_id)
        self._enrich_with_last_edit(sub_rules)
        stats = self.repo.get_category_stats().get(category_id, _EMPTY_STATS)
        return {**cat, "sub_rules": sub_rules, "stats": stats}
