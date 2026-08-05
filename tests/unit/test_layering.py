# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static check: domain code (src/modules/) should not import from src/api/.

The pipeline and domain modules are meant to be reusable from the CLI and
the scheduler worker without an HTTP context. Importing from src/api/
(e.g. middleware error types, Flask-bound caches, settings schemas
nested under api/modules/) couples the domain to the API layer and
breaks that reusability.

The OSS release-readiness review (issue #596 H21) called out 3 violations.
The actual tree has 12 (the audit drifted between writing and now). All
are allow-listed below so the test passes on the current tree but rejects
any new import. Inverting the existing set is tracked as follow-up — each
helper has 5-30+ importers and inverting requires moving files out of
src/api/ into a shared layer; safer to stop the bleed first and refactor
incrementally.

2026-08: the exception hierarchy moved to src/modules/shared/exceptions.py
and the confidence thresholds to src/modules/shared/confidence_thresholds.py
(re-exported from their old src/api homes, so existing callers are
unaffected), which let the newest violations be repointed instead of
allow-listed. The remaining allow-listed entries are genuinely app-coupled
(Prometheus counters, SettingsManager, the app factory) and each carries
its unwind plan inline. Test modules under src/modules/*/tests/ are out of
scope for this guard — see _find_violations.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULES_ROOT = PROJECT_ROOT / "src" / "modules"

# Pattern for any `import src.api...` or `from src.api... import ...`.
API_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+src\.api(?:\.|\b)")

# Frozen list of legacy violations: relative path → count. The count is
# enforced exactly so removing a violation also updates the list,
# preventing drift in either direction.
ALLOWED_VIOLATIONS = {
    "alerts/prometheus_webhook_service.py": 1,
    "asset_generation/services/asset_generation_service.py": 1,
    "assets/repositories/asset_repository.py": 1,
    "assets/services/asset_service.py": 1,
    "charts/services/query_builder.py": 2,
    "integrations/services/scheduler_service.py": 2,
    "monitoring/service.py": 1,
    "pipeline_watchdog/repository.py": 1,
    "reports/services/reports_service.py": 2,
    "reports/services/system_preferences_service.py": 2,

    # ── Added 2026-08 (assertion-drift cleanup) — each entry needs explicit
    # reviewer signoff per the rule below. These predate this branch; the
    # guard was muted by the continue-on-error CI lane when they landed.
    #
    # Prometheus track_* helpers: the counters they emit to are defined
    # alongside the HTTP/chat metrics in src/api/utils/metrics.py, so the
    # helpers can't move to shared without extracting those counters into a
    # Flask-free module first — a dedicated metrics refactor.
    "asset_generation/services/pipeline_orchestrator.py": 1,
    "integrations/services/integration_service.py": 1,
    "tag_rules/services/rule_execution_service.py": 1,
    # Lazy SettingsManager import; unwinding means constructor-injecting the
    # settings manager through the alert_delivery wiring.
    "alert_delivery/wiring.py": 1,
    # Installer/CLI entry point: lazily builds the Flask app (create_app) and
    # reads the blueprint module's BUNDLE_REPO. Genuinely app-coupled; the
    # honest fix is relocating the CLI out of src/modules/.
    "reference_data/apply.py": 2,
    # Second import is a lazy, try/except-guarded invalidate_dashboard_cache
    # call (25729e9e) — kept local so the scheduler import graph stays
    # Flask-free at module load; unwinding means injecting a cache
    # invalidator callback.
    "pipeline/pipeline_service.py": 2,
}


def _find_violations():
    """Return {relpath: count} of imports of src.api inside src/modules/.

    Test modules (``*/tests/*``) are excluded: the rule targets shipped
    domain code, and a test under src/modules/ may deliberately import the
    API layer to exercise the real decorators/handlers (e.g.
    assets/tests/test_reclassify_asset_id_canonicalisation.py asserts the
    ValueError → 400 mapping through the real ``handle_errors``).
    """
    counts: dict[str, int] = {}
    for path in MODULES_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.relative_to(MODULES_ROOT).parts:
            continue
        rel = path.relative_to(MODULES_ROOT).as_posix()
        n = sum(1 for line in path.read_text().splitlines() if API_IMPORT_RE.match(line))
        if n:
            counts[rel] = n
    return counts


def test_no_new_api_imports_from_modules():
    actual = _find_violations()

    new_files = set(actual) - set(ALLOWED_VIOLATIONS)
    assert not new_files, (
        "New domain → API layering violation(s):\n  "
        + "\n  ".join(sorted(new_files))
        + "\n\nDomain code must not import from src.api. "
        "If the helper is genuinely shared, move it to src/modules/shared/ "
        "(or pass it through the constructor). Adding to ALLOWED_VIOLATIONS "
        "without that move requires explicit reviewer signoff."
    )

    drift = {f: (ALLOWED_VIOLATIONS[f], actual.get(f, 0)) for f in ALLOWED_VIOLATIONS}
    bad = {f: c for f, c in drift.items() if c[0] != c[1]}
    assert not bad, (
        "Allow-listed violation count drifted:\n  "
        + "\n  ".join(f"{f}: expected {exp}, got {got}" for f, (exp, got) in bad.items())
        + "\n\nIf you removed a violation (good!), drop the entry from "
        "ALLOWED_VIOLATIONS in this file."
    )
