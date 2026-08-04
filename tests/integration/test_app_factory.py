# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smoke tests for the Flask app factory."""

import pytest

from src.modules.fields import FieldResolver


@pytest.mark.integration
def test_factory_attaches_field_resolver(live_app):
    """FieldResolver must be attached as app.field_resolver and its catalog must build."""
    assert hasattr(live_app, "field_resolver")
    assert isinstance(live_app.field_resolver, FieldResolver)

    # Exercise the DB queries (extended_attributes, tags, posture_categories)
    # by triggering a real catalog build.
    with live_app.app_context():
        entries = live_app.field_resolver.catalog().entries()

    # Static + core_enrichment entries are always present (don't depend on DB rows)
    kinds = {e.kind_name for e in entries}
    assert "static" in kinds
    assert "core_enrichment" in kinds
