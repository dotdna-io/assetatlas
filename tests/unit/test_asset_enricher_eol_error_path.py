# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression test for AssetEnricher.enrich_os_eol() failure path (issue #681 A5).

When EOL enrichment raises, the except-path must still write an os_eol_status
value that satisfies the chk_os_eol_status CHECK constraint on `assets`
(active | approaching_eol | end_of_life | unknown | NULL). Writing an
out-of-set value such as 'enrichment_failed' makes the later `assets` INSERT
violate the constraint and — post-A1, with the whole pipeline running as one
atomic transaction — roll the ENTIRE run back. A localized EOL hiccup must not
take down the run, so the failure path has to land on a constraint-valid value.
"""

from unittest.mock import MagicMock

import pandas as pd

from src.modules.asset_generation.domain.asset_enricher import AssetEnricher

# Values permitted by chk_os_eol_status
# (src/database/migrations/021_add_os_eol_tracking.sql + schema-full.sql).
# NULL/None is also allowed, but the except-path writes a non-null literal.
ALLOWED_EOL_STATUSES = {"active", "approaching_eol", "end_of_life", "unknown"}


def _enricher_with_failing_eol() -> AssetEnricher:
    """An enricher whose EOL backend raises, forcing enrich_os_eol's except path."""
    enricher = AssetEnricher(postgres_adapter=None)
    failing_eol = MagicMock()
    failing_eol.enrich_dataframe.side_effect = RuntimeError("boom")
    enricher.eol_enricher = failing_eol
    return enricher


def test_eol_error_path_writes_constraint_valid_status():
    enricher = _enricher_with_failing_eol()
    df = pd.DataFrame({"os_normalized": ["Windows 10", "Ubuntu 22.04"]})

    out = enricher.enrich_os_eol(df)

    # Every written status must be inside the DB CHECK set, or the assets
    # INSERT aborts the atomic run.
    assert set(out["os_eol_status"].dropna().unique()) <= ALLOWED_EOL_STATUSES
    # The except-path value specifically: the constraint-valid "couldn't
    # determine it" status.
    assert (out["os_eol_status"] == "unknown").all()
