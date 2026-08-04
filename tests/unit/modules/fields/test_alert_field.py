# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `alert` virtual field and the filter_only catalog flag.

Filtering the Explorer by the alert that names the assets, as an ordinary search
token rather than the bespoke ?alert_id= param (#907 follow-up).

What these tests hold, and what breaks if they go:

- The field is FILTER-ONLY and absent from the default /fields payload. Its SQL
  is a correlated subquery; every consumer that would project, pivot or sort by
  it evaluates that once per asset row, measured at >180s against the dev DB's
  73,740 assets. Drop the gate and a column picker offers a field that hangs the
  API.
- Only eq/any are offered. The bespoke clause builder implements nothing else,
  and an operator it silently drops is a filter that quietly does nothing (G-1,
  the same reason confidence_level narrows).
- The title predicate is scoped to OPEN alerts. Resolved rows accumulate under
  the same title — the dev DB holds 17 'Assets Disappeared' rows, none open — so
  an unscoped match would union years of history.
"""

from unittest.mock import MagicMock

from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.fields.catalog import (
    VIRTUAL_FIELDS,
    build_catalog_response,
    is_filter_only_field,
)
from src.modules.fields.encoding import encode, decode
from src.modules.fields.models import VirtualField
from src.modules.fields.types import FieldDataType


def _catalog():
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldCatalog(db_adapter=db)


def _entry(resp, display_name):
    return next(
        (e for e in resp["entries"] if e["display_name"] == display_name), None
    )


def test_alert_field_registered_as_filter_only():
    cfg = VIRTUAL_FIELDS["alert"]
    assert cfg["display_name"] == "Alert"
    assert cfg["data_type"] == FieldDataType.STRING
    assert cfg["operators"] == ["eq", "any"]
    assert cfg["filter_only"] is True


def test_alert_expression_reads_open_alerts_only():
    # The projection is only ever reached by a hand-crafted ?fields= request,
    # but it must agree with what the FILTER can match, or the column would show
    # a name the same token cannot select.
    sql = VIRTUAL_FIELDS["alert"]["sql"]
    assert "string_agg" in sql
    assert "FROM alerts al" in sql
    assert "al.status IN ('active','acknowledged')" in sql
    assert "assets.id = ANY(al.asset_ids)" in sql


def test_alert_resolves_to_a_virtual_expression():
    r = FieldResolver(catalog=_catalog()).resolve(
        VirtualField(kind="virtual", name="alert")
    )
    assert r.data_type == FieldDataType.STRING
    assert r.sql_kind == "virtual_expr"
    assert r.sql_expr == VIRTUAL_FIELDS["alert"]["sql"]


def test_alert_encodes_roundtrip():
    fid = VirtualField(kind="virtual", name="alert")
    assert encode(fid) == "virtual:alert"
    assert decode("virtual:alert") == fid


def test_alert_absent_from_the_default_catalog_response():
    # THE gate. Every consumer that has not opted in — reporting, unified
    # fields, the column picker, MCP — must not see it.
    resp = build_catalog_response(_catalog())
    assert _entry(resp, "Alert") is None


def test_alert_present_when_filter_only_is_requested():
    resp = build_catalog_response(_catalog(), include_filter_only=True)
    assert _entry(resp, "Alert") is not None


def test_filter_only_is_on_the_wire_for_every_entry():
    # The Explorer opts in for its search bar and then strips these entries from
    # its own column/pivot pickers, so it needs the flag per entry.
    resp = build_catalog_response(_catalog(), include_filter_only=True)
    assert resp["entries"]
    for e in resp["entries"]:
        assert "filter_only" in e
    assert _entry(resp, "Alert")["filter_only"] is True


def test_alert_offers_only_eq_and_any():
    resp = build_catalog_response(_catalog(), include_filter_only=True)
    codes = [s["code"] for s in _entry(resp, "Alert")["operator_specs"]]
    assert codes == ["eq", "any"]


def test_is_filter_only_field_recognises_alert_and_nothing_else():
    assert is_filter_only_field(VirtualField(kind="virtual", name="alert")) is True
    assert is_filter_only_field(VirtualField(kind="virtual", name="in_grace")) is False
    # Unified fields mint VirtualField ids whose slugs are not in VIRTUAL_FIELDS.
    assert is_filter_only_field(VirtualField(kind="virtual", name="nope")) is False
