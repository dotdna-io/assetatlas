# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the enrichment service."""

import pytest
from unittest.mock import MagicMock, patch

from src.modules.enrichment.services.enrichment_service import (
    EnrichmentService,
    MAX_FILE_SIZE,
    MAX_ROWS,
    MAX_COLUMNS,
    MAX_CELL_LENGTH,
)
from src.modules.fields.models import (
    StaticField,
    CoreEnrichmentField,
    ExtendedAttributeField,
    IntegrationGroupField,
)
from src.modules.fields.resolver import ResolvedField
from src.modules.fields.types import FieldDataType
from src.modules.fields import FieldCatalog, FieldResolver


SAMPLE_DATASET_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

# The ResolvedField the FieldResolver returns for the SAMPLE_DATASET match_field
# ("hostname"): a native text[] column → match_expr "hostname", value_kind "array".
HOSTNAME_RESOLVED = ResolvedField(
    field_id=StaticField(kind="static", name="Hostname"),
    display_name="Hostname",
    data_type=FieldDataType.STRING_ARRAY,
    operators=frozenset(),
    sql_kind="column",
    sql_expr="hostname",
)

SAMPLE_ROWS = [
    {"hostname": "server01", "location": "NYC"},
    {"hostname": "server02", "location": "LAX"},
    {"hostname": "server03", "location": "ORD"},
]

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


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_resolver():
    resolver = MagicMock()
    resolver.resolve.return_value = HOSTNAME_RESOLVED
    return resolver


@pytest.fixture
def service(mock_repo, mock_db, mock_resolver):
    return EnrichmentService(
        repository=mock_repo, db_adapter=mock_db, field_resolver=mock_resolver
    )


class TestUploadCsv:
    """Tests for upload_csv method."""

    @patch("src.modules.enrichment.services.enrichment_service.validate_and_parse_csv")
    def test_successful_upload(self, mock_validate, service, mock_repo):
        mock_validate.return_value = (SAMPLE_ROWS, None)
        mock_repo.create_dataset.return_value = SAMPLE_DATASET_ID
        mock_repo.insert_rows.return_value = 3

        file = MagicMock()
        result = service.upload_csv(
            file=file,
            name="Locations",
            match_field="hostname",
            match_type="exact",
            target_field="location",
            match_col="hostname",
            enrich_col="location",
            uploaded_by="admin",
        )

        assert result["dataset_id"] == SAMPLE_DATASET_ID
        assert result["row_count"] == 3
        assert result["preview"] == SAMPLE_ROWS[:50]
        assert result["columns"] == ["hostname", "location"]

        mock_validate.assert_called_once_with(
            file, MAX_FILE_SIZE, MAX_ROWS, MAX_COLUMNS, MAX_CELL_LENGTH
        )
        mock_repo.create_dataset.assert_called_once_with(
            name="Locations",
            match_field="hostname",
            match_type="exact",
            target_field="location",
            row_count=3,
            uploaded_by="admin",
        )
        mock_repo.insert_rows.assert_called_once_with(
            SAMPLE_DATASET_ID, SAMPLE_ROWS, "hostname", "location"
        )

    @patch("src.modules.enrichment.services.enrichment_service.validate_and_parse_csv")
    def test_upload_rejects_invalid_csv(self, mock_validate, service):
        mock_validate.return_value = (None, "File is empty")

        file = MagicMock()
        result = service.upload_csv(
            file=file,
            name="Bad",
            match_field="hostname",
            match_type="exact",
            target_field="location",
            match_col="hostname",
            enrich_col="location",
        )

        assert result == {"error": "File is empty"}

    @patch("src.modules.enrichment.services.enrichment_service.validate_and_parse_csv")
    def test_upload_rejects_missing_match_col(self, mock_validate, service):
        mock_validate.return_value = (SAMPLE_ROWS, None)

        file = MagicMock()
        result = service.upload_csv(
            file=file,
            name="Locations",
            match_field="hostname",
            match_type="exact",
            target_field="location",
            match_col="nonexistent",
            enrich_col="location",
        )

        assert "error" in result
        assert "nonexistent" in result["error"]

    @patch("src.modules.enrichment.services.enrichment_service.validate_and_parse_csv")
    def test_upload_rejects_missing_enrich_col(self, mock_validate, service):
        mock_validate.return_value = (SAMPLE_ROWS, None)

        file = MagicMock()
        result = service.upload_csv(
            file=file,
            name="Locations",
            match_field="hostname",
            match_type="exact",
            target_field="location",
            match_col="hostname",
            enrich_col="nonexistent",
        )

        assert "error" in result
        assert "nonexistent" in result["error"]

    @patch("src.modules.enrichment.services.enrichment_service.validate_and_parse_csv")
    def test_upload_default_uploaded_by_is_none(self, mock_validate, service, mock_repo):
        mock_validate.return_value = (SAMPLE_ROWS, None)
        mock_repo.create_dataset.return_value = SAMPLE_DATASET_ID

        file = MagicMock()
        service.upload_csv(
            file=file,
            name="Locations",
            match_field="hostname",
            match_type="exact",
            target_field="location",
            match_col="hostname",
            enrich_col="location",
        )

        mock_repo.create_dataset.assert_called_once_with(
            name="Locations",
            match_field="hostname",
            match_type="exact",
            target_field="location",
            row_count=3,
            uploaded_by=None,
        )


class TestListDatasets:
    """Tests for list_datasets method."""

    def test_delegates_to_repo(self, service, mock_repo):
        expected = [SAMPLE_DATASET]
        mock_repo.list_datasets.return_value = expected

        result = service.list_datasets()

        assert result == expected
        mock_repo.list_datasets.assert_called_once()


class TestGetDataset:
    """Tests for get_dataset method."""

    def test_returns_dataset_with_sample_rows(self, service, mock_repo):
        mock_repo.get_dataset.return_value = dict(SAMPLE_DATASET)
        sample_rows = [{"match_value": "server01", "enrich_value": "NYC"}]
        mock_repo.get_rows.return_value = sample_rows

        result = service.get_dataset(SAMPLE_DATASET_ID)

        assert result is not None
        assert result["id"] == SAMPLE_DATASET_ID
        assert result["sample_rows"] == sample_rows
        mock_repo.get_dataset.assert_called_once_with(SAMPLE_DATASET_ID)
        mock_repo.get_rows.assert_called_once_with(SAMPLE_DATASET_ID, limit=50)

    def test_returns_none_when_not_found(self, service, mock_repo):
        mock_repo.get_dataset.return_value = None

        result = service.get_dataset(SAMPLE_DATASET_ID)

        assert result is None


class TestDeleteDataset:
    """Tests for delete_dataset method."""

    def test_delegates_to_repo(self, service, mock_repo):
        mock_repo.soft_delete_dataset.return_value = True

        result = service.delete_dataset(SAMPLE_DATASET_ID)

        assert result is True
        mock_repo.soft_delete_dataset.assert_called_once_with(SAMPLE_DATASET_ID)

    def test_returns_false_when_not_found(self, service, mock_repo):
        mock_repo.soft_delete_dataset.return_value = False

        result = service.delete_dataset(SAMPLE_DATASET_ID)

        assert result is False


class TestUpdateStatus:
    """Tests for update_status method."""

    def test_delegates_to_repo(self, service, mock_repo):
        mock_repo.update_dataset_status.return_value = True

        result = service.update_status(SAMPLE_DATASET_ID, "active")

        assert result is True
        mock_repo.update_dataset_status.assert_called_once_with(
            SAMPLE_DATASET_ID, "active"
        )


class TestResolveMatchValueKind:
    """_resolve_match maps a ResolvedField to the matcher's value_kind. The
    scalar path is the one that fixes integration-group matching (a COALESCE),
    so all three branches are covered, not just the native-array happy path."""

    @pytest.mark.parametrize("field_id,sql_kind,data_type,expected_kind", [
        # native text[] column → element-wise unnest
        (StaticField(kind="static", name="Hostname"),
         "column", FieldDataType.STRING_ARRAY, "array"),
        # jsonb array (core-enrichment Private IP) → jsonb_array_elements_text
        (CoreEnrichmentField(kind="core_enrichment", name="Private IP"),
         "jsonb_path", FieldDataType.JSONB_ARRAY, "jsonb_array"),
        # integration-group COALESCE → single scalar value
        (IntegrationGroupField(kind="integration_group", group="Entra", attribute="building"),
         "jsonb_coalesce", FieldDataType.STRING, "scalar"),
        # ext.<source>.<key> scalar jsonb path
        (ExtendedAttributeField(kind="extended_attribute", source="SentinelOne", attribute="siteName"),
         "jsonb_path", FieldDataType.STRING, "scalar"),
        # plain scalar column (e.g. category)
        (StaticField(kind="static", name="Category"),
         "column", FieldDataType.STRING, "scalar"),
    ])
    def test_value_kind_for_resolved_field(
        self, service, mock_resolver, field_id, sql_kind, data_type, expected_kind
    ):
        mock_resolver.resolve.return_value = ResolvedField(
            field_id=field_id,
            display_name="X",
            data_type=data_type,
            operators=frozenset(),
            sql_kind=sql_kind,
            sql_expr="RESOLVED_EXPR",
        )

        match_expr, value_kind = service._resolve_match("static:Hostname")

        assert match_expr == "RESOLVED_EXPR"
        assert value_kind == expected_kind

    @patch("src.modules.enrichment.services.enrichment_service.build_count_query")
    @patch("src.modules.enrichment.services.enrichment_service.build_preview_query")
    def test_integration_group_field_previews_as_scalar(
        self, mock_preview, mock_count, service, mock_repo, mock_db, mock_resolver
    ):
        # End-to-end wiring of the bug-fix path: a grouped integration field
        # resolves to a COALESCE scalar and the matcher is called with "scalar".
        mock_repo.get_dataset.return_value = dict(
            SAMPLE_DATASET, match_field="group:Entra:building"
        )
        mock_resolver.resolve.return_value = ResolvedField(
            field_id=IntegrationGroupField(
                kind="integration_group", group="Entra", attribute="building"
            ),
            display_name="Entra - building",
            data_type=FieldDataType.STRING,
            operators=frozenset(),
            sql_kind="jsonb_coalesce",
            sql_expr="COALESCE(a, b)",
        )
        mock_count.return_value = ("SELECT COUNT...", {})
        mock_preview.return_value = ("SELECT ...", {})
        mock_db.execute_query.side_effect = [[{"count": 1}], []]

        service.preview_dataset(SAMPLE_DATASET_ID)

        mock_count.assert_called_once_with(
            SAMPLE_DATASET_ID, "COALESCE(a, b)", "scalar", "exact"
        )
        mock_preview.assert_called_once_with(
            SAMPLE_DATASET_ID, "COALESCE(a, b)", "scalar", "exact"
        )


class TestPreviewDataset:
    """Tests for preview_dataset method."""

    @patch("src.modules.enrichment.services.enrichment_service.build_count_query")
    @patch("src.modules.enrichment.services.enrichment_service.build_preview_query")
    def test_returns_preview_data(
        self, mock_build_preview, mock_build_count, service, mock_repo, mock_db
    ):
        mock_repo.get_dataset.return_value = dict(SAMPLE_DATASET)
        mock_build_count.return_value = ("SELECT COUNT...", {"dataset_id": SAMPLE_DATASET_ID})
        mock_build_preview.return_value = ("SELECT ...", {"dataset_id": SAMPLE_DATASET_ID})

        sample_matches = [
            {"id": "uuid1", "hostname": "server01", "matched_value": "server01",
             "matched_against": "server01", "enrich_value": "NYC"},
        ]
        mock_db.execute_query.side_effect = [
            [{"count": 2}],    # count query result
            sample_matches,     # preview query result
        ]

        result = service.preview_dataset(SAMPLE_DATASET_ID)

        assert result is not None
        assert result["dataset_id"] == SAMPLE_DATASET_ID
        assert result["dataset_name"] == "Locations"
        assert result["matched_count"] == 2
        assert result["total_rows"] == 3
        assert result["sample_matches"] == sample_matches

        mock_build_count.assert_called_once_with(
            SAMPLE_DATASET_ID, "hostname", "array", "exact"
        )
        mock_build_preview.assert_called_once_with(
            SAMPLE_DATASET_ID, "hostname", "array", "exact"
        )

    def test_returns_none_when_dataset_not_found(self, service, mock_repo):
        mock_repo.get_dataset.return_value = None

        result = service.preview_dataset(SAMPLE_DATASET_ID)

        assert result is None


class TestApplyDataset:
    """Tests for apply_dataset method."""

    @patch("src.modules.enrichment.services.enrichment_service.build_match_and_apply_query")
    def test_applies_and_updates_count(
        self, mock_build_query, service, mock_repo, mock_db
    ):
        mock_repo.get_dataset.return_value = dict(SAMPLE_DATASET)
        mock_build_query.return_value = ("UPDATE ...", {"dataset_id": SAMPLE_DATASET_ID})
        mock_db.execute_query.return_value = [
            {"id": "uuid1"},
            {"id": "uuid2"},
        ]

        result = service.apply_dataset(SAMPLE_DATASET_ID)

        assert result["dataset_id"] == SAMPLE_DATASET_ID
        assert result["dataset_name"] == "Locations"
        assert result["matched_count"] == 2

        mock_build_query.assert_called_once_with(
            SAMPLE_DATASET_ID, "hostname", "array", "exact", "location"
        )
        mock_repo.update_matched_count.assert_called_once_with(
            SAMPLE_DATASET_ID, 2
        )

    @patch("src.modules.enrichment.services.enrichment_service.build_match_and_apply_query")
    def test_apply_handles_no_matches(
        self, mock_build_query, service, mock_repo, mock_db
    ):
        mock_repo.get_dataset.return_value = dict(SAMPLE_DATASET)
        mock_build_query.return_value = ("UPDATE ...", {"dataset_id": SAMPLE_DATASET_ID})
        mock_db.execute_query.return_value = []

        result = service.apply_dataset(SAMPLE_DATASET_ID)

        assert result["matched_count"] == 0
        mock_repo.update_matched_count.assert_called_once_with(
            SAMPLE_DATASET_ID, 0
        )

    def test_apply_returns_none_when_dataset_not_found(self, service, mock_repo):
        mock_repo.get_dataset.return_value = None

        result = service.apply_dataset(SAMPLE_DATASET_ID)

        assert result is None


class TestApplyAllActive:
    """Tests for apply_all_active method."""

    @patch("src.modules.enrichment.services.enrichment_service.build_match_and_apply_query")
    def test_applies_each_active_dataset(
        self, mock_build_query, service, mock_repo, mock_db
    ):
        dataset_1 = dict(SAMPLE_DATASET, id="id-1", name="DS1")
        dataset_2 = dict(SAMPLE_DATASET, id="id-2", name="DS2")
        mock_repo.get_active_datasets.return_value = [dataset_1, dataset_2]
        # apply_dataset re-fetches the full dataset by id
        datasets_by_id = {"id-1": dataset_1, "id-2": dataset_2}
        mock_repo.get_dataset.side_effect = lambda did: datasets_by_id[did]

        mock_build_query.return_value = ("UPDATE ...", {"dataset_id": "x"})
        mock_db.execute_query.side_effect = [
            [{"id": "a1"}, {"id": "a2"}],   # first dataset matches
            [{"id": "a3"}],                   # second dataset matches
        ]

        results = service.apply_all_active()

        assert len(results) == 2
        assert results[0]["dataset_id"] == "id-1"
        assert results[0]["matched_count"] == 2
        assert results[1]["dataset_id"] == "id-2"
        assert results[1]["matched_count"] == 1

        assert mock_repo.update_matched_count.call_count == 2

    def test_returns_empty_list_when_no_active(self, service, mock_repo):
        mock_repo.get_active_datasets.return_value = []

        results = service.apply_all_active()

        assert results == []


class TestApplyAllActiveRefreshesCatalog:
    """Regression: the pipeline ``TRUNCATE``s + rebuilds ``assets`` before the
    stage-21 enrichment re-apply runs. The FieldResolver's catalog derives its
    extended_attribute / integration_group entries by scanning
    ``assets.extended_attributes``, and the pipeline reuses the app catalog's
    TTL-cached snapshot — which can be the one taken mid-rebuild (assets empty).
    So a ``group:``/``ext:`` match field fails to resolve and the dataset is
    silently skipped, leaving the enrichment wiped after every pipeline run.

    Observed on production 2026.26.3: the 'Legal Entities' dataset
    (match_field ``group:SentinelOne:siteId``) matched 21,163 assets on a manual
    Apply, but 0 assets carried it because each pipeline run skipped it with
    "unresolved match_field 'group:SentinelOne:siteId'". apply_all_active() must
    refresh the catalog so resolution reflects the rebuilt assets.
    """

    class _FlippableDB:
        """db_adapter stub whose extended_attributes discovery scan changes
        between the catalog being primed (assets mid-rebuild) and re-apply
        (assets rebuilt). Routes only the queries the catalog + matcher run."""

        def __init__(self):
            self.ext_attr_pairs = []  # (source, attribute) pairs present in assets

        def execute_query(self, query, params=None, fetch=True):
            if "jsonb_object_keys" in query:        # catalog ext-attr discovery
                return [{"source_name": s, "field_name": a}
                        for (s, a) in self.ext_attr_pairs]
            if "asset_tags_v2" in query:            # catalog tag discovery
                return []
            if "posture_categories" in query:       # catalog posture discovery
                return []
            if "UPDATE assets" in query:            # matcher apply
                return [{"id": "asset-1"}]
            return []

    def _build_service(self, fake_db):
        catalog = FieldCatalog(
            db_adapter=fake_db,
            group_mapping_provider=lambda: {"Corp SentinelOne": "SentinelOne"},
        )
        resolver = FieldResolver(catalog=catalog)
        repo = MagicMock()
        dataset = dict(
            SAMPLE_DATASET,
            id="legal-entities",
            name="Legal Entities",
            match_field="group:SentinelOne:siteId",
            target_field="legal_entity",
        )
        repo.get_active_datasets.return_value = [dataset]
        repo.get_dataset.side_effect = lambda did: dataset
        service = EnrichmentService(
            repository=repo, db_adapter=fake_db, field_resolver=resolver
        )
        return service, catalog, repo

    def test_reapply_resolves_group_field_after_assets_rebuilt(self):
        fake_db = self._FlippableDB()
        service, catalog, repo = self._build_service(fake_db)

        # Catalog primed while the assets table is empty (post-truncate,
        # mid-rebuild): the integration_group entry does not exist yet.
        fake_db.ext_attr_pairs = []
        catalog.entries()

        # Assets are now rebuilt — the SentinelOne source + siteId are back.
        fake_db.ext_attr_pairs = [("Corp SentinelOne", "siteId")]

        results = service.apply_all_active()

        # The group match field must resolve against the rebuilt assets and the
        # dataset must actually be applied — not skipped as "unresolved".
        assert len(results) == 1
        assert "error" not in results[0], results[0]
        assert results[0]["matched_count"] == 1
        repo.update_matched_count.assert_called_once_with("legal-entities", 1)


class TestApplyDatasetMaterializedRefresh:
    """#1405 part 3: an API-triggered apply rewrites extended_attributes, so
    enrichment-sourced materialized unified fields must be refreshed. The
    pipeline path (apply_all_active, s21) is already followed by the
    enrichment-scoped materialize pass and must NOT refresh per dataset."""

    @patch("src.modules.enrichment.services.enrichment_service.refresh_for_sources")
    @patch("src.modules.enrichment.services.enrichment_service.UnifiedFieldsRepository")
    @patch("src.modules.enrichment.services.enrichment_service.build_match_and_apply_query")
    def test_api_apply_refreshes_enrichment_sourced_fields(
        self, mock_build_query, mock_repo_cls, mock_refresh,
        service, mock_repo, mock_db,
    ):
        mock_repo.get_dataset.return_value = dict(SAMPLE_DATASET)
        mock_build_query.return_value = ("UPDATE ...", {"dataset_id": SAMPLE_DATASET_ID})
        mock_db.execute_query.return_value = [{"id": "a1"}, {"id": "a2"}]
        defs = [{"slug": "legal_entity", "sources": ["ext:enrichment:legal_entity"]}]
        mock_repo_cls.return_value.list_for_catalog.return_value = defs

        result = service.apply_dataset(SAMPLE_DATASET_ID)

        assert result["matched_count"] == 2
        mock_refresh.assert_called_once_with(
            mock_db, service.field_resolver, defs, ("ext:enrichment:",))

    @patch("src.modules.enrichment.services.enrichment_service.refresh_for_sources")
    @patch("src.modules.enrichment.services.enrichment_service.UnifiedFieldsRepository")
    @patch("src.modules.enrichment.services.enrichment_service.build_match_and_apply_query")
    def test_apply_with_no_matches_does_not_refresh(
        self, mock_build_query, mock_repo_cls, mock_refresh,
        service, mock_repo, mock_db,
    ):
        mock_repo.get_dataset.return_value = dict(SAMPLE_DATASET)
        mock_build_query.return_value = ("UPDATE ...", {"dataset_id": SAMPLE_DATASET_ID})
        mock_db.execute_query.return_value = []

        result = service.apply_dataset(SAMPLE_DATASET_ID)

        assert result["matched_count"] == 0
        mock_refresh.assert_not_called()

    @patch("src.modules.enrichment.services.enrichment_service.refresh_for_sources")
    @patch("src.modules.enrichment.services.enrichment_service.UnifiedFieldsRepository")
    @patch("src.modules.enrichment.services.enrichment_service.build_match_and_apply_query")
    def test_pipeline_path_does_not_double_refresh(
        self, mock_build_query, mock_repo_cls, mock_refresh,
        service, mock_repo, mock_db,
    ):
        dataset = dict(SAMPLE_DATASET)
        mock_repo.get_active_datasets.return_value = [dataset]
        mock_repo.get_dataset.return_value = dataset
        mock_build_query.return_value = ("UPDATE ...", {"dataset_id": "x"})
        mock_db.execute_query.return_value = [{"id": "a1"}]

        results = service.apply_all_active()

        assert len(results) == 1
        mock_refresh.assert_not_called()
        mock_repo_cls.assert_not_called()

    @patch("src.modules.enrichment.services.enrichment_service.refresh_for_sources")
    @patch("src.modules.enrichment.services.enrichment_service.UnifiedFieldsRepository")
    @patch("src.modules.enrichment.services.enrichment_service.build_match_and_apply_query")
    def test_refresh_failure_does_not_fail_the_apply(
        self, mock_build_query, mock_repo_cls, mock_refresh,
        service, mock_repo, mock_db,
    ):
        mock_repo.get_dataset.return_value = dict(SAMPLE_DATASET)
        mock_build_query.return_value = ("UPDATE ...", {"dataset_id": SAMPLE_DATASET_ID})
        mock_db.execute_query.return_value = [{"id": "a1"}]
        mock_repo_cls.return_value.list_for_catalog.side_effect = RuntimeError("boom")

        result = service.apply_dataset(SAMPLE_DATASET_ID)   # must not raise

        assert result["matched_count"] == 1
