# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the pipeline stage → phase mapping."""

import inspect
import pkgutil
import importlib

import pytest

from src.modules.pipeline.phase_mapping import (
    STAGE_TO_PHASE,
    PHASES,
    phase_for_stage,
)
from src.modules.asset_generation.pipeline import stages as stages_pkg
from src.modules.asset_generation.pipeline.base import PipelineStage


def _all_stage_keys() -> set[str]:
    """Discover every concrete PipelineStage subclass in pipeline/stages and
    return its `.key`."""
    keys: set[str] = set()
    for mod_info in pkgutil.iter_modules(stages_pkg.__path__):
        module = importlib.import_module(
            f"{stages_pkg.__name__}.{mod_info.name}"
        )
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(cls, PipelineStage)
                and cls is not PipelineStage
                and cls.__module__ == module.__name__
            ):
                # Some stages need a service in __init__; key is a property
                # bound to instance, but for the concrete classes in this
                # repo it doesn't read self, so we can build a throwaway
                # instance-like object via __new__.
                instance = cls.__new__(cls)
                keys.add(instance.key)
    return keys


def test_every_pipeline_stage_has_a_phase():
    """Every stage in pipeline/stages/ must be mapped to one of the four
    phases — otherwise a new stage would silently disappear from the
    operator view."""
    discovered = _all_stage_keys()
    mapped = set(STAGE_TO_PHASE.keys())

    missing = discovered - mapped
    assert not missing, (
        f"Pipeline stages without a phase mapping: {sorted(missing)}. "
        f"Add them to STAGE_TO_PHASE in src/modules/pipeline/phase_mapping.py."
    )


def test_no_orphans_in_phase_mapping():
    """Every key in STAGE_TO_PHASE must correspond to a real stage."""
    discovered = _all_stage_keys()
    orphans = set(STAGE_TO_PHASE.keys()) - discovered
    assert not orphans, (
        f"STAGE_TO_PHASE contains keys with no matching stage: "
        f"{sorted(orphans)}."
    )


def test_phases_cover_exactly_four_buckets():
    """The issue specifies exactly four phases."""
    assert set(STAGE_TO_PHASE.values()) == set(PHASES.keys())
    assert len(PHASES) == 4


def test_phase_for_stage_returns_phase_key():
    assert phase_for_stage("scoring") == "enrich_score"
    assert phase_for_stage("persistence") == "persist_apply_rules"
    assert phase_for_stage("preprocessing") == "ingest_identify"
    assert phase_for_stage("embedding_update") == "post_process"


def test_phase_for_stage_unknown_raises():
    with pytest.raises(KeyError):
        phase_for_stage("definitely_not_a_real_stage")


def test_phases_have_human_labels():
    """UI needs a label per phase."""
    for key, meta in PHASES.items():
        assert meta["label"], f"Phase {key} has no label"
        assert meta["order"] >= 0
