# SPDX-License-Identifier: AGPL-3.0-or-later
"""Enrichment apply_dataset bumps the asset-data version stamp (#1410).

The apply rewrites extended_attributes on every matched asset, so the
distinct values of enrichment-sourced fields change and the Explorer
field-values cache must invalidate. The bump fires on the API path
(refresh_materialized=True) and on the pipeline path alike — there the run's
own completion bump covers it too, and a second INCR is harmless.

Harness mirrors TestApplyDataset in test_enrichment_service.py.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.modules.enrichment.services.enrichment_service import EnrichmentService
from src.modules.fields.models import StaticField
from src.modules.fields.resolver import ResolvedField
from src.modules.fields.types import FieldDataType

SAMPLE_DATASET_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

HOSTNAME_RESOLVED = ResolvedField(
    field_id=StaticField(kind="static", name="Hostname"),
    display_name="Hostname",
    data_type=FieldDataType.STRING_ARRAY,
    operators=frozenset(),
    sql_kind="column",
    sql_expr="hostname",
)

SAMPLE_DATASET = {
    "id": SAMPLE_DATASET_ID,
    "name": "Locations",
    "match_field": "hostname",
    "match_type": "exact",
    "target_field": "location",
    "status": "active",
    "row_count": 3,
    "matched_count": 0,
    "uploaded_by": "admin",
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-01-01T00:00:00",
}

_BUMP = "src.modules.enrichment.services.enrichment_service.asset_data_version.bump"


@pytest.fixture
def service():
    repo = MagicMock()
    db = MagicMock()
    resolver = MagicMock()
    resolver.resolve.return_value = HOSTNAME_RESOLVED
    repo.get_dataset.return_value = dict(SAMPLE_DATASET)
    db.execute_query.return_value = [{"id": "uuid1"}, {"id": "uuid2"}]
    return EnrichmentService(repository=repo, db_adapter=db, field_resolver=resolver)


@patch("src.modules.enrichment.services.enrichment_service.refresh_for_sources")
@patch("src.modules.enrichment.services.enrichment_service.UnifiedFieldsRepository")
@patch("src.modules.enrichment.services.enrichment_service.build_match_and_apply_query")
def test_api_apply_bumps_the_data_stamp(
    mock_build_query, mock_repo_cls, mock_refresh, service,
):
    mock_build_query.return_value = ("UPDATE ...", {"dataset_id": SAMPLE_DATASET_ID})
    mock_repo_cls.return_value.list_for_catalog.return_value = []

    with patch(_BUMP) as mock_bump:
        result = service.apply_dataset(SAMPLE_DATASET_ID)

    assert result["matched_count"] == 2
    mock_bump.assert_called_once_with()


@patch("src.modules.enrichment.services.enrichment_service.build_match_and_apply_query")
def test_pipeline_path_apply_also_bumps(mock_build_query, service):
    """refresh_materialized=False (apply_all_active) still rewrites values."""
    mock_build_query.return_value = ("UPDATE ...", {"dataset_id": SAMPLE_DATASET_ID})

    with patch(_BUMP) as mock_bump:
        result = service.apply_dataset(SAMPLE_DATASET_ID, refresh_materialized=False)

    assert result["matched_count"] == 2
    mock_bump.assert_called_once_with()


def test_unknown_dataset_writes_nothing_and_does_not_bump(service):
    service.repo.get_dataset.return_value = None

    with patch(_BUMP) as mock_bump:
        assert service.apply_dataset(SAMPLE_DATASET_ID) is None

    mock_bump.assert_not_called()
