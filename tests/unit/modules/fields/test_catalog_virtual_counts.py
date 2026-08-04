# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the count virtual fields added in #496.

These cover the dict shape only — resolver round-trip is covered by
test_resolver_virtual_counts.py.
"""

import pytest

from src.modules.fields.catalog import VIRTUAL_FIELDS
from src.modules.fields.types import FieldDataType


COUNT_FIELDS = [
    ("hostname_count", "Hostname Count", "hostname"),
    ("ip_count",       "IP Count",       "ip"),
    ("fqdn_count",     "FQDN Count",     "fqdn"),
    ("user_count",     "User Count",     "user_field"),
    ("owner_count",    "Owner Count",    "owner"),
]


@pytest.mark.parametrize("name,display,col", COUNT_FIELDS)
def test_count_field_registered(name, display, col):
    assert name in VIRTUAL_FIELDS, f"{name} missing from VIRTUAL_FIELDS"
    entry = VIRTUAL_FIELDS[name]
    assert entry["display_name"] == display
    assert entry["data_type"] == FieldDataType.INTEGER
    expected_sql = f"COALESCE(array_length({col}, 1), 0)"
    assert entry["sql"] == expected_sql, (
        f"{name} sql is {entry['sql']!r}, expected {expected_sql!r}"
    )


def test_existing_data_source_count_unchanged():
    """Regression guard: don't accidentally rewrite the existing virtual."""
    entry = VIRTUAL_FIELDS["data_source_count"]
    assert entry["data_type"] == FieldDataType.INTEGER
    assert entry["sql"] == "COALESCE(array_length(data_source, 1), 0)"


def test_asset_label_registered():
    assert "asset_label" in VIRTUAL_FIELDS
    entry = VIRTUAL_FIELDS["asset_label"]
    assert entry["display_name"] == "Asset Label"
    assert entry["data_type"] == FieldDataType.STRING
    expected_sql = (
        "COALESCE("
        "NULLIF(asset_label_override, ''), "
        "NULLIF(hostname[1], ''), "
        "NULLIF(fqdn[1], ''), "
        "NULLIF(mac[1], ''), "
        "NULLIF(sn[1], ''), "
        "NULLIF(ip[1], '')"
        ")"
    )
    assert entry["sql"] == expected_sql, (
        f"asset_label sql is {entry['sql']!r}, expected {expected_sql!r}"
    )
