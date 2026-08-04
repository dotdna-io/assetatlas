# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify AlertsRepository excludes external rules from the in-pipeline
evaluator's rule set but still returns them in administrative queries."""
import pytest

from src.modules.alerts.repository import AlertsRepository


@pytest.mark.integration
def test_get_enabled_rules_excludes_external(live_db):
    """Watchdog rules are seeded by migration 082 with evaluation_type='external'.
    They must not appear in get_enabled_rules (used by the in-pipeline evaluator)."""
    repo = AlertsRepository(live_db)
    rules = repo.get_enabled_rules()
    external_slugs = {r["slug"] for r in rules if r.get("evaluation_type") == "external"}
    assert external_slugs == set(), (
        "get_enabled_rules must not return external rules; "
        f"found: {external_slugs}"
    )
    watchdog_slugs = {r["slug"] for r in rules} & {
        "pipeline-stale", "pipeline-last-run-failed", "pipeline-stuck",
    }
    assert watchdog_slugs == set(), (
        "get_enabled_rules leaked watchdog slugs: " f"{watchdog_slugs}"
    )


@pytest.mark.integration
def test_get_all_rules_includes_external(live_db):
    """Administrative queries (Rules page) must still see external rules."""
    repo = AlertsRepository(live_db)
    rules = repo.get_all_rules()
    slugs = {r["slug"] for r in rules}
    assert {"pipeline-stale", "pipeline-last-run-failed", "pipeline-stuck"}.issubset(slugs)
    # evaluation_type is present in the payload
    ext = [r for r in rules if r["slug"] == "pipeline-stale"][0]
    assert ext["evaluation_type"] == "external"
