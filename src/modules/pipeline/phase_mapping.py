# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mapping from pipeline stage keys to operator-facing phases.

The asset-generation pipeline emits ~26 stage keys via `ctx.stats['stages']`.
The operator view groups them into four phases so a real run renders as
four cards instead of 26 rows. Sub-stages are surfaced only when flagged
as outliers.

The mapping is intentionally a constant — phases are a UX concept, not
a configuration knob.
"""

from typing import Dict, TypedDict


class PhaseMeta(TypedDict):
    label: str
    order: int


# Phase metadata. `order` controls UI display order.
PHASES: Dict[str, PhaseMeta] = {
    "ingest_identify":     {"label": "Ingest & Identify",      "order": 0},
    "enrich_score":        {"label": "Enrich & Score",         "order": 1},
    "persist_apply_rules": {"label": "Persist & Apply Rules",  "order": 2},
    "post_process":        {"label": "Post-process",           "order": 3},
}


# Stage key (as written by PipelineStage.execute into ctx.stats['stages'])
# → phase key.
STAGE_TO_PHASE: Dict[str, str] = {
    # Ingest & Identify (s00–s07)
    "cleanup_expired":              "ingest_identify",
    "preprocessing":                "ingest_identify",
    "asset_field_generation":       "ingest_identify",
    "load_mappings":                "ingest_identify",
    "id_assignment":                "ingest_identify",
    "conflict_detection":           "ingest_identify",
    "excluded_records":             "ingest_identify",
    "expansion":                    "ingest_identify",

    # Enrich & Score (s08–s15)
    "enrichment":                   "enrich_score",
    "category_prediction":          "enrich_score",
    "os_selection":                 "enrich_score",
    "provider_prepare":             "enrich_score",
    "aggregation":                  "enrich_score",
    "category_selection_combined":  "enrich_score",
    "scoring":                      "enrich_score",
    "ip_classification":            "enrich_score",
    "ip_geolocation":               "enrich_score",
    "os_eol_enrichment":            "enrich_score",
    "asset_label_overrides":        "enrich_score",

    # Persist & Apply Rules (s16–s17b)
    "grace_carry_forward":          "persist_apply_rules",
    "persistence":                  "persist_apply_rules",
    "materialize_unified_values":   "persist_apply_rules",
    "tag_rules":                    "persist_apply_rules",
    "posture_rules":                "persist_apply_rules",

    # Post-process (s18–s22)
    "chronicle":                    "post_process",
    "alert_evaluation":             "post_process",
    "csv_enrichment":               "post_process",
    "materialize_unified_values_enrichment": "post_process",
    "embedding_update":             "post_process",
}


def phase_for_stage(stage_key: str) -> str:
    """Return the phase key for a given stage key. Raises KeyError if the
    stage is not mapped — callers should add new stages to STAGE_TO_PHASE
    rather than swallow the error."""
    return STAGE_TO_PHASE[stage_key]
