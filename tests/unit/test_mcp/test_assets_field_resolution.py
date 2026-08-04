# SPDX-License-Identifier: AGPL-3.0-or-later
"""P5 (#735): _normalize_field_name falls back to the live catalog for
non-static/virtual fields when a resolver is provided."""
from unittest.mock import MagicMock

from src.mcp.tools.assets import _normalize_field_name
from src.modules.fields.models import IntegrationGroupField


def _resolver_with(fid):
    resolver = MagicMock()
    resolver.catalog.return_value.field_id_for_display_name.return_value = fid
    return resolver


def test_static_still_resolves_without_resolver():
    assert _normalize_field_name("Category") == "static:Category"


def test_per_source_field_resolves_via_catalog():
    fid = IntegrationGroupField(kind="integration_group", group="Beszel",
                                attribute="cpu")
    out = _normalize_field_name("Beszel - cpu", resolver=_resolver_with(fid))
    assert out == "group:Beszel:cpu"


def test_unknown_field_without_match_returns_none():
    out = _normalize_field_name("nonsense", resolver=_resolver_with(None))
    assert out is None
