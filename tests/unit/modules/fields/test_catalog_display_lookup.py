# SPDX-License-Identifier: AGPL-3.0-or-later
"""P5 (#735): FieldCatalog resolves a user-facing display name (any case/
spacing) to its FieldId, covering integration_group fields like 'Beszel - cpu'."""
from src.modules.fields.catalog import FieldCatalog


class _FakeDB:
    """Returns one Beszel/cpu extended attribute so _build emits an entry.

    The _extended_attribute_entries SQL returns columns 'source_name' and
    'field_name'. With an empty group_mapping (lambda: {}), every source
    is individual → yields an extended_attribute entry with display_name
    "Beszel - cpu".
    """
    def execute_query(self, query, params=None):
        q = query.lower()
        if "jsonb_each(extended_attributes)" in q:
            return [{"source_name": "Beszel", "field_name": "cpu"}]
        return []


def _catalog():
    # No group mapping -> the extended attribute yields an extended_attribute
    # entry named "Beszel - cpu" (single-source, ungrouped).
    return FieldCatalog(_FakeDB(), group_mapping_provider=lambda: {})


def test_resolve_exact_display_name():
    cat = _catalog()
    fid = cat.field_id_for_display_name("Beszel - cpu")
    assert fid is not None
    # Extended_attribute encoding — assert it round-trips to a Beszel/cpu field.
    assert getattr(fid, "attribute", None) == "cpu"


def test_resolve_is_case_and_spacing_insensitive():
    cat = _catalog()
    assert cat.field_id_for_display_name("beszel  cpu") is not None
    assert cat.field_id_for_display_name("BESZEL - CPU") is not None


def test_unknown_display_name_returns_none():
    assert _catalog().field_id_for_display_name("no such field") is None
