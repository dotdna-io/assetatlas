# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Enrichment Service

Orchestrates CSV upload, CRUD operations, and enrichment application
between the API layer and the repository/matcher layers.
"""

import logging
from typing import List, Optional

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.enrichment.repositories.enrichment_repository import (
    EnrichmentRepository,
)
from src.modules.enrichment.services.csv_validator import validate_and_parse_csv
from src.modules.enrichment.domain.enrichment_matcher import (
    build_match_and_apply_query,
    build_preview_query,
    build_count_query,
)
from src.modules.enrichment.domain.match_field import match_field_to_field_id
from src.modules.fields.models import InvalidFieldError

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_ROWS = 100_000
MAX_COLUMNS = 50
MAX_CELL_LENGTH = 1_000


class EnrichmentService:
    """Service for managing enrichment datasets and applying them to assets."""

    def __init__(
        self,
        repository: EnrichmentRepository,
        db_adapter: PostgreSQLAdapter,
        field_resolver,
    ):
        """
        Initialize enrichment service.

        Args:
            repository: Enrichment repository for dataset/row CRUD
            db_adapter: PostgreSQL adapter for executing matcher queries
            field_resolver: FieldResolver used to turn a match_field into a SQL
                expression. Must be the app/pipeline resolver (built with a
                group_mapping_provider) so integration-group fields resolve.
        """
        self.repo = repository
        self.db = db_adapter
        self.field_resolver = field_resolver

    def _resolve_match(self, match_field: str):
        """Resolve a stored match_field to ``(sql_expr, value_kind)`` via the catalog.

        ``value_kind`` is how the matcher should read values out of the expression:
        ``array`` (native text[] column), ``jsonb_array`` (jsonb array), or
        ``scalar`` (single value). Raises ValueError (unparseable match_field) or
        InvalidFieldError (parses but not in the live catalog).
        """
        field_id = match_field_to_field_id(match_field)
        resolved = self.field_resolver.resolve(field_id)
        if resolved.is_native_array():
            value_kind = "array"
        elif resolved.is_array():
            value_kind = "jsonb_array"
        else:
            value_kind = "scalar"
        return resolved.where_expr(), value_kind

    def upload_csv(
        self,
        file,
        name: str,
        match_field: str,
        match_type: str,
        target_field: str,
        match_col: str,
        enrich_col: str,
        uploaded_by: str = None,
    ) -> dict:
        """
        Validate, parse, and store a CSV enrichment file.

        Args:
            file: Werkzeug FileStorage from the upload.
            name: Display name for the dataset.
            match_field: Asset field to match against (e.g. 'hostname', 'ip').
            match_type: Match strategy ('exact' or 'cidr').
            target_field: Asset field to enrich.
            match_col: Column name in the CSV containing match values.
            enrich_col: Column name in the CSV containing enrichment values.
            uploaded_by: Username of uploader.

        Returns:
            Dict with dataset_id, row_count, preview, columns on success,
            or dict with 'error' key on failure.
        """
        # Resolve the match field against the live catalog up front so an unknown
        # field fails clearly here instead of silently matching nothing at apply.
        try:
            self._resolve_match(match_field)
        except (ValueError, InvalidFieldError) as exc:
            return {"error": f"match_field '{match_field}' is not a known asset field ({exc})"}

        rows, error = validate_and_parse_csv(
            file, MAX_FILE_SIZE, MAX_ROWS, MAX_COLUMNS, MAX_CELL_LENGTH
        )
        if error:
            return {"error": error}

        # Validate that the specified columns exist in the CSV
        columns = list(rows[0].keys())
        if match_col not in columns:
            return {"error": f"Match column '{match_col}' not found in CSV columns: {columns}"}
        if enrich_col not in columns:
            return {"error": f"Enrich column '{enrich_col}' not found in CSV columns: {columns}"}

        dataset_id = self.repo.create_dataset(
            name=name,
            match_field=match_field,
            match_type=match_type,
            target_field=target_field,
            row_count=len(rows),
            uploaded_by=uploaded_by,
        )

        self.repo.insert_rows(dataset_id, rows, match_col, enrich_col)

        logger.info(
            "Uploaded enrichment dataset '%s' (%s): %d rows",
            name, dataset_id, len(rows),
        )

        return {
            "dataset_id": dataset_id,
            "row_count": len(rows),
            "preview": rows[:50],
            "columns": columns,
        }

    def list_datasets(self) -> List[dict]:
        """
        List all non-deleted enrichment datasets.

        Returns:
            List of dataset dicts, newest first.
        """
        return self.repo.list_datasets()

    def get_dataset(self, dataset_id: str) -> Optional[dict]:
        """
        Get a single dataset with sample rows.

        Args:
            dataset_id: Dataset UUID.

        Returns:
            Dataset dict with sample_rows, or None if not found.
        """
        dataset = self.repo.get_dataset(dataset_id)
        if dataset is None:
            return None

        dataset["sample_rows"] = self.repo.get_rows(dataset_id, limit=50)
        return dataset

    def delete_dataset(self, dataset_id: str) -> bool:
        """
        Soft-delete a dataset.

        Args:
            dataset_id: Dataset UUID.

        Returns:
            True if deleted, False if not found.
        """
        return self.repo.soft_delete_dataset(dataset_id)

    def update_status(self, dataset_id: str, status: str) -> bool:
        """
        Update the status of a dataset.

        Args:
            dataset_id: Dataset UUID.
            status: New status value.

        Returns:
            True if updated, False if not found.
        """
        return self.repo.update_dataset_status(dataset_id, status)

    def preview_dataset(self, dataset_id: str) -> Optional[dict]:
        """
        Preview how a dataset would match against current assets.

        Args:
            dataset_id: Dataset UUID.

        Returns:
            Dict with matched_count, total_rows, sample_matches,
            or None if dataset not found.
        """
        dataset = self.repo.get_dataset(dataset_id)
        if dataset is None:
            return None

        match_expr, value_kind = self._resolve_match(dataset["match_field"])
        count_sql, count_params = build_count_query(
            dataset_id, match_expr, value_kind, dataset["match_type"]
        )
        preview_sql, preview_params = build_preview_query(
            dataset_id, match_expr, value_kind, dataset["match_type"]
        )

        count_result = self.db.execute_query(count_sql, count_params)
        matched_count = count_result[0]["count"] if count_result else 0

        sample_matches = self.db.execute_query(preview_sql, preview_params)
        if not sample_matches:
            sample_matches = []

        return {
            "dataset_id": dataset_id,
            "dataset_name": dataset["name"],
            "matched_count": matched_count,
            "total_rows": dataset["row_count"],
            "sample_matches": sample_matches,
        }

    def apply_dataset(self, dataset_id: str) -> Optional[dict]:
        """
        Apply an enrichment dataset to matching assets.

        Executes the match-and-apply query that merges enrichment values
        into assets' extended_attributes JSONB.

        Args:
            dataset_id: Dataset UUID.

        Returns:
            Dict with dataset_id, dataset_name, matched_count,
            or None if dataset not found.
        """
        dataset = self.repo.get_dataset(dataset_id)
        if dataset is None:
            return None

        try:
            match_expr, value_kind = self._resolve_match(dataset["match_field"])
        except (ValueError, InvalidFieldError) as exc:
            logger.warning(
                "Skipping enrichment dataset '%s' (%s): unresolved match_field %r (%s)",
                dataset["name"], dataset_id, dataset["match_field"], exc,
            )
            return {
                "dataset_id": dataset_id,
                "dataset_name": dataset["name"],
                "matched_count": 0,
                "error": str(exc),
            }

        sql, params = build_match_and_apply_query(
            dataset_id,
            match_expr,
            value_kind,
            dataset["match_type"],
            dataset["target_field"],
        )

        result = self.db.execute_query(sql, params)
        matched_count = len(result) if result else 0

        self.repo.update_matched_count(dataset_id, matched_count)

        logger.info(
            "Applied enrichment dataset '%s' (%s): %d assets matched",
            dataset["name"], dataset_id, matched_count,
        )

        return {
            "dataset_id": dataset_id,
            "dataset_name": dataset["name"],
            "matched_count": matched_count,
        }

    def apply_all_active(self) -> List[dict]:
        """
        Apply all active enrichment datasets to assets.

        Used by the pipeline stage to re-apply enrichments after
        asset data is refreshed.

        Returns:
            List of result dicts, one per active dataset.
        """
        # The pipeline TRUNCATEs + rebuilds `assets` before this stage runs. The
        # FieldResolver's catalog discovers extended_attribute / integration_group
        # match fields by scanning assets.extended_attributes and caches that
        # snapshot (TTL), so it may still hold one taken mid-rebuild (assets
        # empty) — leaving a `group:`/`ext:` match_field unresolvable and the
        # dataset silently skipped. Drop the snapshot so resolution reflects the
        # rebuilt assets.
        self.field_resolver.catalog().invalidate()

        datasets = self.repo.get_active_datasets()
        results = []

        for dataset in datasets:
            result = self.apply_dataset(dataset["id"])
            if result:
                results.append(result)

        return results

    def list_ext_fields(self) -> List[dict]:
        """
        Discover available extended_attributes source/key paths from asset data.

        Returns:
            List of dicts with source, key, path (ext.<source>.<key>), and sample_value.
        """
        sql = """
            SELECT DISTINCT ON (src.key, leaf.key)
                src.key AS source,
                leaf.key AS key,
                leaf.value #>> '{}' AS sample_value
            FROM (SELECT * FROM assets WHERE extended_attributes IS NOT NULL LIMIT 200) a
                CROSS JOIN LATERAL jsonb_each(a.extended_attributes) AS src(key, value)
                CROSS JOIN LATERAL jsonb_each(src.value) AS leaf(key, value)
            WHERE jsonb_typeof(src.value) = 'object'
                AND jsonb_typeof(leaf.value) != 'object'
                AND jsonb_typeof(leaf.value) != 'array'
            ORDER BY src.key, leaf.key, a.id
        """
        rows = self.db.execute_query(sql)
        return [
            {
                "source": r["source"],
                "key": r["key"],
                "path": f"ext.{r['source']}.{r['key']}",
                "sample_value": r["sample_value"],
            }
            for r in (rows or [])
        ]
