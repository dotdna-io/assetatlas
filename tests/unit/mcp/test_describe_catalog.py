# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``describe_catalog`` (plan §2.A) — the model's discovery backbone.

``describe_catalog`` rewrites the old ``list_fields`` binding to call
``build_catalog_response`` so it surfaces, per field, the OPERATOR set that field
accepts (``operator_specs``). Today's ``list_fields`` DROPS that — the single
biggest discovery gap, because the model then can't tell that ``between`` works
on a number but not on a string. It also adds bucket/source/search filters,
real paging with an explicit total + overflow hint (no silent truncation, §4),
and a ``view=operators`` dump.

Uses an empty-DB catalog, so only the built-in static / core-enrichment /
virtual fields are present (dynamic ext/group/tag/posture need a live DB).
"""

from unittest.mock import MagicMock

from src.mcp.tools.field_registry import describe_catalog_impl
from src.modules.fields import FieldCatalog, FieldResolver


def _resolver():
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


class TestDescribeCatalogFields:
    def test_surfaces_per_field_operator_codes(self):
        """THE gap this closes: each field advertises the operators it accepts.
        A numeric field must show ``between`` (a number/date-only operator) so the
        model can discover it instead of guessing."""
        out = describe_catalog_impl(_resolver(), view="fields",
                                    search="Confidence Score")
        assert "Confidence Score" in out
        assert "between" in out

    def test_includes_encoded_key_and_total(self):
        out = describe_catalog_impl(_resolver(), view="fields", search="Category")
        assert "static:Category" in out
        assert "total" in out.lower()

    def test_bucket_filter_restricts_results(self):
        virtual = describe_catalog_impl(_resolver(), view="fields",
                                        bucket="virtual")
        assert "Confidence Level" in virtual
        # a static field must not appear under the virtual bucket
        assert "static:Hostname" not in virtual
        core = describe_catalog_impl(_resolver(), view="fields",
                                     bucket="core_enrichment")
        assert "Private IP" in core

    def test_unknown_bucket_is_loud(self):
        """An unknown bucket must be a clear error listing the valid set, not a
        silent fall-through that returns the whole catalog (§4)."""
        out = describe_catalog_impl(_resolver(), view="fields", bucket="nonsense")
        assert "nonsense" in out.lower()
        assert "target" in out  # lists the valid buckets

    def test_paging_caps_with_overflow_hint(self):
        out = describe_catalog_impl(_resolver(), view="fields", limit=3)
        # only 3 rows render, but the total reflects the full catalog and a
        # "more" hint signals the truncation (never silent).
        assert "more" in out.lower()

    def test_offset_past_end_is_clear_not_empty(self):
        """An offset past the total must return a clear, recoverable message —
        not an empty table with a contradictory backwards 'showing N+1-N'
        header and no hint."""
        out = describe_catalog_impl(_resolver(), view="fields", offset=100000)
        low = out.lower()
        assert "offset" in low and "past" in low
        assert "100001-100000" not in out

    def test_search_miss_is_clear_not_empty(self):
        out = describe_catalog_impl(_resolver(), view="fields",
                                    search="zzzqqq_definitely_nope")
        assert "no" in out.lower()

    def test_source_filter_no_match_is_clear(self):
        """A source filter that matches no field reports so plainly (the field
        may not exist on this deployment) instead of silently empty."""
        out = describe_catalog_impl(_resolver(), view="fields", source="Beszel")
        assert "Beszel" in out and "no" in out.lower()


class TestDescribeCatalogOperators:
    def test_operators_view_dumps_registry_with_aliases(self):
        out = describe_catalog_impl(_resolver(), view="operators")
        for code in ("eq", "contains", "between", "within_last", "any", "none",
                     "empty", "notempty"):
            assert code in out
        # aliases must be surfaced so the model learns accepted spellings
        assert "any of" in out
