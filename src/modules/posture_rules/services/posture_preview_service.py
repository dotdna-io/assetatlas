# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.fields.models import FieldId, InvalidFieldError
from src.modules.fields.resolver import FieldResolver
from src.modules.posture_rules.models.rule import criteria_logic_to_sql
from src.modules.tag_rules.services.query_engine_service import QueryEngineService

if TYPE_CHECKING:
    from src.modules.posture_rules.repositories.posture_repository import PostureRepository


class PosturePreviewService:
    def __init__(
        self,
        repository: "PostureRepository",
        query_engine: QueryEngineService,
        db_adapter: PostgreSQLAdapter,
        resolver: FieldResolver,
    ):
        self.repo = repository
        self.engine = query_engine
        self.db = db_adapter
        self.resolver = resolver

    def preview(
        self,
        target_filters: List[Dict[str, Any]],
        compliance_criteria: Dict[str, Any],
    ) -> Dict[str, int]:
        matched = self.engine.asset_ids_matching(
            target_filters, db=self.db, logic="AND",
        )
        if not matched:
            return {"matched": 0, "pass": 0, "fail": 0}

        criteria_matched = self.engine.asset_ids_matching(
            compliance_criteria.get("conditions", []),
            db=self.db,
            logic=criteria_logic_to_sql(compliance_criteria.get("logic", "all")),
        )
        passed = matched & criteria_matched
        return {
            "matched": len(matched),
            "pass": len(passed),
            "fail": len(matched) - len(passed),
        }

    def preview_output(
        self,
        target_filters: List[Dict[str, Any]],
        compliance_criteria: Dict[str, Any],
        output_mode: str,
        output_field_id: Optional[FieldId] = None,
        output_text: Optional[str] = None,
        sample_limit: int = 5,
    ) -> Dict[str, Any]:
        """Return sample output values the rule would produce for passing assets."""
        matched = self.engine.asset_ids_matching(
            target_filters, db=self.db, logic="AND",
        )
        if not matched:
            return {"matched": 0, "passed": 0, "samples": []}
        criteria_matched = self.engine.asset_ids_matching(
            compliance_criteria.get("conditions", []),
            db=self.db,
            logic=criteria_logic_to_sql(compliance_criteria.get("logic", "all")),
        )
        passed = matched & criteria_matched
        if not passed:
            return {"matched": len(matched), "passed": 0, "samples": []}

        sample_ids = list(passed)[:sample_limit]
        hostname_sql = (
            "SELECT id, array_to_string(hostname, ', ') AS hostname "
            "FROM assets WHERE id = ANY(%(ids)s::uuid[])"
        )
        host_rows = self.db.execute_query(hostname_sql, {"ids": sample_ids})
        hostnames = {str(r["id"]): r["hostname"] for r in host_rows}

        if output_mode == "text":
            samples = [
                {"hostname": hostnames.get(str(aid)), "output": output_text}
                for aid in sample_ids
            ]
        else:  # field mode
            outputs: Dict[str, Any] = {}
            if output_field_id is not None:
                try:
                    outputs = self.repo.fetch_asset_field_values(
                        sample_ids, output_field_id, self.resolver
                    )
                except InvalidFieldError:
                    pass  # outputs stays {}
            samples = [
                {"hostname": hostnames.get(str(aid)), "output": outputs.get(str(aid))}
                for aid in sample_ids
            ]

        return {
            "matched": len(matched),
            "passed": len(passed),
            "samples": samples,
        }
