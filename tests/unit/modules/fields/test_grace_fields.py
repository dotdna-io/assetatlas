# SPDX-License-Identifier: AGPL-3.0-or-later
"""Grace-period virtual fields: in_grace (boolean) + days_in_grace (integer) (#795)."""

from unittest.mock import MagicMock

from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.catalog import VIRTUAL_FIELDS
from src.modules.fields.encoding import encode, decode
from src.modules.fields.models import VirtualField
from src.modules.fields.types import FieldDataType

DAYS_SQL = "FLOOR(EXTRACT(EPOCH FROM (now() - missing_since)) / 86400)::int"


def _resolver():
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


def test_grace_virtual_fields_registered():
    assert VIRTUAL_FIELDS["in_grace"]["data_type"] == FieldDataType.BOOLEAN
    assert VIRTUAL_FIELDS["in_grace"]["sql"] == "(missing_since IS NOT NULL)"
    assert VIRTUAL_FIELDS["in_grace"]["operators"] == ["eq"]
    assert VIRTUAL_FIELDS["days_in_grace"]["data_type"] == FieldDataType.INTEGER
    assert VIRTUAL_FIELDS["days_in_grace"]["sql"] == DAYS_SQL


def test_in_grace_resolves_to_boolean_expr():
    r = _resolver().resolve(VirtualField(kind="virtual", name="in_grace"))
    assert r.data_type == FieldDataType.BOOLEAN
    assert r.sql_kind == "virtual_expr"
    assert r.sql_expr == "(missing_since IS NOT NULL)"


def test_days_in_grace_resolves_to_integer_expr():
    r = _resolver().resolve(VirtualField(kind="virtual", name="days_in_grace"))
    assert r.data_type == FieldDataType.INTEGER
    assert r.sql_kind == "virtual_expr"
    assert r.sql_expr == DAYS_SQL


def test_grace_fields_encode_roundtrip():
    for name in ("in_grace", "days_in_grace"):
        fid = VirtualField(kind="virtual", name=name)
        assert encode(fid) == f"virtual:{name}"
        assert decode(f"virtual:{name}") == fid
