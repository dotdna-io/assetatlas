# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static guards for Task 1b — dashboard materialized views must bucket
confidence through the configurable ``confidence_threshold_{high,medium}()``
SQL functions instead of hardcoded 0.70 / 0.45 literals.

These tests read the SQL files as text (they do NOT touch a database). They
exist to catch a transcription slip in migration 110 or in schema-full.sql
that would silently leave a hardcoded threshold in a view body — which would
make the dashboard ignore the operator's confidence slider.
"""


def test_migration_110_buckets_via_functions_not_literals():
    sql = open(
        "src/database/migrations/110_confidence_threshold_functions_and_views.sql"
    ).read()
    # functions exist and are used by the views
    assert "confidence_threshold_high()" in sql and "confidence_threshold_medium()" in sql
    # no literal bucketing comparisons remain (function COALESCE defaults like ", 0.70" are fine)
    for bad in (">= 0.70", ">= 0.45", "< 0.70", "< 0.45"):
        assert bad not in sql, f"literal threshold comparison {bad!r} still present"
    # the COALESCE defaults are still there
    assert ", 0.70" in sql and ", 0.45" in sql
    # all four views + their unique indexes are recreated
    for name in ("mv_dashboard_summary", "mv_asset_quality_metrics",
                 "mv_provider_coverage", "mv_provider_coverage_grouped"):
        assert f"CREATE MATERIALIZED VIEW {name}" in sql.replace("IF NOT EXISTS ", "")
    for idx in ("idx_mv_dashboard_summary_key", "idx_mv_asset_quality_metrics_category",
                "idx_mv_provider_coverage_id", "idx_mv_provider_coverage_grouped_id_type"):
        assert idx in sql


def test_schema_full_views_use_threshold_functions():
    sql = open("src/database/schema-full.sql").read()
    assert "confidence_threshold_high()" in sql and "confidence_threshold_medium()" in sql
    # Scope the literal check to the dashboard MV bodies: none of these bucketing
    # comparisons should survive anywhere the confidence column is bucketed.
    for bad in ("confidence >= 0.70", "confidence >= 0.45",
                "confidence < 0.70", "confidence < 0.45",
                "AVG(confidence) >= 0.70", "AVG(confidence) >= 0.45"):
        assert bad not in sql, f"{bad!r} still hardcoded in schema-full.sql"
