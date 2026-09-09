# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic migration-149 probe state for every test in this directory.

column_present() caches a True probe per process and warn_migration_149_pending()
warns once per process — both deliberate in production, but cross-test leaks
here: one test's cached True would decide another test's degrade path. Reset
both before each test so each models its own database.
"""

import pytest

from src.modules.fields.unified import materialize


@pytest.fixture(autouse=True)
def _reset_migration_149_probe_state(monkeypatch):
    monkeypatch.setattr(materialize, "_COLUMN_PRESENT", {})
    monkeypatch.setattr(materialize, "_MIGRATION_149_WARNED", False)
