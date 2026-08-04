# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Enrichment Repository

Handles database queries for enrichment datasets and rows.
"""

import logging
from typing import Optional, List

from src.database.postgres_adapter import PostgreSQLAdapter

logger = logging.getLogger(__name__)


class EnrichmentRepository:
    """Repository for enrichment dataset data access."""

    def __init__(self, db_adapter: PostgreSQLAdapter):
        """
        Initialize repository with database adapter.

        Args:
            db_adapter: PostgreSQL database adapter
        """
        self.db = db_adapter

    def create_dataset(
        self,
        name: str,
        match_field: str,
        match_type: str,
        target_field: str,
        row_count: int,
        uploaded_by: str = None,
    ) -> str:
        """
        Create a new enrichment dataset.

        Args:
            name: Dataset display name
            match_field: Asset field to match against (e.g. 'hostname', 'ip')
            match_type: Match strategy ('exact' or 'cidr')
            target_field: Asset field to enrich
            row_count: Number of rows in the CSV
            uploaded_by: Username of uploader

        Returns:
            ID of the created dataset as a string
        """
        query = """
            INSERT INTO enrichment_datasets
                (name, match_field, match_type,
                 target_field, row_count, uploaded_by)
            VALUES
                (%(name)s, %(match_field)s, %(match_type)s,
                 %(target_field)s, %(row_count)s, %(uploaded_by)s)
            RETURNING id::text
        """
        params = {
            "name": name,
            "match_field": match_field,
            "match_type": match_type,
            "target_field": target_field,
            "row_count": row_count,
            "uploaded_by": uploaded_by,
        }
        result = self.db.execute_query(query, params)
        return result[0]["id"]

    def insert_rows(
        self,
        dataset_id: str,
        rows: List[dict],
        match_col: str,
        enrich_col: str,
    ) -> int:
        """
        Bulk-insert enrichment rows for a dataset.

        Args:
            dataset_id: Parent dataset ID
            rows: List of row dicts from the CSV
            match_col: Key in each row dict for the match value
            enrich_col: Key in each row dict for the enrich value

        Returns:
            Number of rows inserted
        """
        if not rows:
            return 0

        values_fragments = []
        params = {"dataset_id": dataset_id}

        for i, row in enumerate(rows):
            values_fragments.append(
                f"(%(dataset_id)s::uuid, %(match_{i})s, %(enrich_{i})s)"
            )
            params[f"match_{i}"] = row[match_col]
            params[f"enrich_{i}"] = row[enrich_col]

        query = """
            INSERT INTO enrichment_rows (dataset_id, match_value, enrich_value)
            VALUES {values}
        """.format(values=", ".join(values_fragments))

        self.db.execute_query(query, params, fetch=False)
        return len(rows)

    def list_datasets(self) -> List[dict]:
        """
        List all non-deleted enrichment datasets.

        Returns:
            List of dataset dicts, newest first
        """
        query = """
            SELECT
                id::text, name, match_field, match_type, target_field,
                status, row_count, matched_count, uploaded_by,
                created_at, updated_at
            FROM enrichment_datasets
            WHERE status != %(excluded_status)s
            ORDER BY created_at DESC
        """
        result = self.db.execute_query(query, {"excluded_status": "deleted"})
        return result if result else []

    def get_dataset(self, dataset_id: str) -> Optional[dict]:
        """
        Get a single enrichment dataset by ID.

        Args:
            dataset_id: Dataset UUID

        Returns:
            Dataset dict or None if not found / deleted
        """
        query = """
            SELECT
                id::text, name, match_field, match_type, target_field,
                status, row_count, matched_count, uploaded_by,
                created_at, updated_at
            FROM enrichment_datasets
            WHERE id = %(dataset_id)s::uuid
              AND status != %(excluded_status)s
        """
        result = self.db.execute_query(
            query, {"dataset_id": dataset_id, "excluded_status": "deleted"}
        )
        if result and len(result) > 0:
            return result[0]
        return None

    def get_rows(self, dataset_id: str, limit: int = None) -> List[dict]:
        """
        Get enrichment rows for a dataset.

        Args:
            dataset_id: Dataset UUID
            limit: Optional max rows to return

        Returns:
            List of row dicts with match_value and enrich_value
        """
        params = {"dataset_id": dataset_id}

        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT %(limit)s"
            params["limit"] = limit

        query = """
            SELECT match_value, enrich_value
            FROM enrichment_rows
            WHERE dataset_id = %(dataset_id)s::uuid
            {limit_clause}
        """.format(limit_clause=limit_clause)

        result = self.db.execute_query(query, params)
        return result if result else []

    def soft_delete_dataset(self, dataset_id: str) -> bool:
        """
        Soft-delete a dataset by setting status to 'deleted'.

        Args:
            dataset_id: Dataset UUID

        Returns:
            True if a row was updated, False otherwise
        """
        return self.update_dataset_status(dataset_id, "deleted")

    def update_dataset_status(self, dataset_id: str, status: str) -> bool:
        """
        Update the status of a dataset.

        Args:
            dataset_id: Dataset UUID
            status: New status value

        Returns:
            True if a row was updated, False otherwise
        """
        query = """
            UPDATE enrichment_datasets
            SET status = %(status)s, updated_at = NOW()
            WHERE id = %(dataset_id)s::uuid
            RETURNING id::text
        """
        result = self.db.execute_query(
            query, {"dataset_id": dataset_id, "status": status}
        )
        return bool(result and len(result) > 0)

    def update_matched_count(self, dataset_id: str, count: int) -> None:
        """
        Update the matched_count for a dataset.

        Args:
            dataset_id: Dataset UUID
            count: Number of assets matched
        """
        query = """
            UPDATE enrichment_datasets
            SET matched_count = %(matched_count)s, updated_at = NOW()
            WHERE id = %(dataset_id)s::uuid
        """
        self.db.execute_query(
            query, {"dataset_id": dataset_id, "matched_count": count},
            fetch=False,
        )

    def get_active_datasets(self) -> List[dict]:
        """
        Get all datasets with 'active' status.

        Returns:
            List of active dataset dicts, oldest first
        """
        query = """
            SELECT
                id::text, name, match_field, match_type, target_field,
                status, row_count, matched_count, uploaded_by,
                created_at, updated_at
            FROM enrichment_datasets
            WHERE status = %(status)s
            ORDER BY created_at ASC
        """
        result = self.db.execute_query(query, {"status": "active"})
        return result if result else []

    def delete_rows_for_dataset(self, dataset_id: str) -> None:
        """
        Delete all enrichment rows for a dataset.

        Args:
            dataset_id: Dataset UUID
        """
        query = """
            DELETE FROM enrichment_rows
            WHERE dataset_id = %(dataset_id)s::uuid
        """
        self.db.execute_query(query, {"dataset_id": dataset_id}, fetch=False)
