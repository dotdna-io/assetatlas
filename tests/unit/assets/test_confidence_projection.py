# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for confidence_level SELECT projection threshold wiring.

WHY these tests exist: sorting and grouping by Confidence Level is driven by
the projected CASE expression in _build_select_columns. Before this fix the
expression was frozen at 0.67/0.34 (the old catalog fallback), so sorting by
Confidence Level disagreed with both the filter (_build_confidence_level_clause,
which uses the configured thresholds) and the cell post-processor
(asset_service.py). The projection must use whatever thresholds are passed in.
"""
from unittest.mock import MagicMock

from src.modules.assets.repositories.asset_repository import AssetRepository
from src.modules.fields import FieldCatalog, FieldResolver


def _make_repo() -> AssetRepository:
    """Build an AssetRepository with a real resolver (minimal catalog)."""
    db = MagicMock()
    catalog = FieldCatalog(db_adapter=db, group_mapping_provider=lambda: {})
    resolver = FieldResolver(catalog=catalog)
    return AssetRepository(db, resolver=resolver)


def test_confidence_level_projection_uses_thresholds():
    """
    Projection SQL must embed the supplied thresholds, not the old static defaults.

    WHY: sort/group-by Confidence Level must agree with the filter and the
    cell; the projection was frozen at 0.67/0.34 regardless of the slider.
    """
    repo = _make_repo()
    cols = repo._build_select_columns(
        ["virtual:confidence_level"],
        confidence_thresholds=(0.50, 0.90),
    )
    # Supplied thresholds appear in the SQL
    assert "0.9" in cols, f"Expected high threshold 0.90 in: {cols}"
    assert "0.5" in cols, f"Expected medium threshold 0.50 in: {cols}"
    # Old stale values must NOT appear
    assert "0.67" not in cols, f"Old high threshold 0.67 must not appear in: {cols}"
    assert "0.34" not in cols, f"Old medium threshold 0.34 must not appear in: {cols}"
    # The alias is preserved so the Explorer response dict key is correct
    assert "confidence_level" in cols, f"Alias 'confidence_level' missing in: {cols}"


def test_confidence_level_projection_default_thresholds():
    """
    When no thresholds are passed, the projection uses DEFAULT_CONFIDENCE_THRESHOLDS
    (0.45, 0.70) — consistent with catalog fallback and settings default.
    """
    repo = _make_repo()
    cols = repo._build_select_columns(["virtual:confidence_level"])
    assert "0.7" in cols, f"Expected default high threshold 0.70 in: {cols}"
    assert "0.45" in cols, f"Expected default medium threshold 0.45 in: {cols}"
    assert "0.67" not in cols, f"Old stale value 0.67 must not appear in: {cols}"
    assert "0.34" not in cols, f"Old stale value 0.34 must not appear in: {cols}"


def test_non_confidence_level_virtual_fields_still_resolve():
    """
    Other virtual fields (e.g. data_source_count) must continue to resolve
    via the normal resolver path — not be caught by the confidence_level branch.
    """
    repo = _make_repo()
    cols = repo._build_select_columns(
        ["virtual:data_source_count"],
        confidence_thresholds=(0.50, 0.90),
    )
    # data_source_count's SQL comes from the catalog, not from the CASE branch
    assert "array_length" in cols or "COALESCE" in cols, (
        f"data_source_count should use catalog SQL, got: {cols}"
    )
    assert "data_source_count" in cols
