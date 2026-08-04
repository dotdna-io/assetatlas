# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests STANDARD_FIELDS in src/mcp/resources/schema.py exposes the
seven count + label fields added in #496."""

import pytest

from src.mcp.resources.schema import STANDARD_FIELDS


REQUIRED = [
    ("data_source_count", "integer"),
    ("hostname_count",    "integer"),
    ("ip_count",          "integer"),
    ("fqdn_count",        "integer"),
    ("user_count",        "integer"),
    ("owner_count",       "integer"),
    ("asset_label",       "string"),
]


@pytest.mark.parametrize("key,dtype", REQUIRED)
def test_standard_fields_includes(key, dtype):
    by_key = {k: (t, d) for k, t, d in STANDARD_FIELDS}
    assert key in by_key, f"{key} not in STANDARD_FIELDS"
    actual_type, actual_desc = by_key[key]
    assert actual_type == dtype, f"{key} declared as {actual_type!r}, expected {dtype!r}"
    assert actual_desc, f"{key} has empty description"


def test_no_duplicate_keys():
    keys = [k for k, _, _ in STANDARD_FIELDS]
    duplicates = [k for k in set(keys) if keys.count(k) > 1]
    assert not duplicates, f"Duplicate field keys in STANDARD_FIELDS: {duplicates}"
