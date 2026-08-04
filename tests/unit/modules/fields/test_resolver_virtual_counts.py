# SPDX-License-Identifier: AGPL-3.0-or-later
"""Round-trip resolver verification for the new virtual fields:
catalog dict -> CatalogEntry -> ResolvedField -> SQL expression.

Uses a mock DB adapter so the catalog can build its index without
hitting a live database — only static + virtual entries are needed
for these assertions; extended_attribute/tag/posture entries return
empty.
"""

from unittest.mock import MagicMock

import pytest

from src.modules.fields.catalog import FieldCatalog
from src.modules.fields.models import VirtualField
from src.modules.fields.resolver import FieldResolver
from src.modules.fields.types import FieldDataType, FilterOperator


@pytest.fixture(scope="module")
def resolver():
    db = MagicMock()
    db.execute_query.return_value = []
    catalog = FieldCatalog(db_adapter=db)
    return FieldResolver(catalog=catalog)


COUNT_CASES = [
    ("hostname_count", "hostname"),
    ("ip_count",       "ip"),
    ("fqdn_count",     "fqdn"),
    ("user_count",     "user_field"),
    ("owner_count",    "owner"),
]


@pytest.mark.parametrize("name,col", COUNT_CASES)
def test_count_field_resolves_to_integer_sql(resolver, name, col):
    resolved = resolver.resolve(VirtualField(kind="virtual", name=name))
    assert resolved.data_type == FieldDataType.INTEGER
    assert resolved.where_expr() == f"COALESCE(array_length({col}, 1), 0)"
    # Numeric operators must be present
    assert FilterOperator.GREATER_THAN in resolved.operators
    assert FilterOperator.EQUALS in resolved.operators
    assert FilterOperator.IS_NULL in resolved.operators
    # No string-only operators leaked in
    assert FilterOperator.CONTAINS not in resolved.operators


def test_asset_label_resolves_to_string_sql(resolver):
    resolved = resolver.resolve(VirtualField(kind="virtual", name="asset_label"))
    assert resolved.data_type == FieldDataType.STRING
    expected = (
        "COALESCE("
        "NULLIF(asset_label_override, ''), "
        "NULLIF(hostname[1], ''), "
        "NULLIF(fqdn[1], ''), "
        "NULLIF(mac[1], ''), "
        "NULLIF(sn[1], ''), "
        "NULLIF(ip[1], '')"
        ")"
    )
    assert resolved.where_expr() == expected
    # String operators present
    assert FilterOperator.CONTAINS in resolved.operators
    assert FilterOperator.EQUALS in resolved.operators
    # Numeric-only operators NOT present
    assert FilterOperator.GREATER_THAN not in resolved.operators
