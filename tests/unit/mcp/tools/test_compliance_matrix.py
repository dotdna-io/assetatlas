# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for get_compliance_matrix — the posture-category × asset-category
coverage matrix + per-sub-rule breakdown + per-cell drilldown.

It WRAPS PostureRepository.get_category_stats and the posture preview service
(which delegates to the canonical AssetQueryCompiler); it never recomputes
matching. Every cell must carry a re-runnable drilldown filter (§3.6/§4).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.api.blueprints.chat.artifacts import validate_envelope
from src.mcp.tools._query import _normalize_filters
from src.mcp.tools.posture import get_compliance_matrix_impl


def _repo():
    repo = MagicMock()
    repo.get_category_stats.return_value = {
        "secured": {"pass": 12, "fail": 8, "n_a": 0, "by_asset_category": {
            "server": {"covered": 7, "total": 10},
            "workstation": {"covered": 5, "total": 10}}},
        "patched": {"pass": 3, "fail": 3, "n_a": 4, "by_asset_category": {
            "server": {"covered": 3, "total": 10}}},
    }
    repo.list_categories.return_value = [
        {"id": "secured", "name": "Secured"},
        {"id": "patched", "name": "Patching"},
    ]
    return repo


def _valid_table(art):
    assert validate_envelope(art, tool_name="get_compliance_matrix") is art
    assert art["type"] == "table"


def test_matrix_repository_missing_is_graceful():
    text, art = get_compliance_matrix_impl(posture_repository=None)
    assert "not available" in text.lower()
    assert art is None


def test_matrix_invalid_group_by_is_loud():
    text, art = get_compliance_matrix_impl(posture_repository=_repo(),
                                           group_by="banana")
    assert "Unknown group_by" in text
    assert art is None


def test_matrix_none_rolls_up_per_posture_category():
    text, art = get_compliance_matrix_impl(posture_repository=_repo())
    assert "Secured" in text and "Patching" in text
    assert "12" in text  # secured covered (=pass)
    _valid_table(art)
    assert len(art["series"]) == 2  # one row per posture category
    secured = next(r for r in art["series"]
                   if r["posture_category"] == "Secured")
    assert secured["covered"] == 12 and secured["fail"] == 8
    assert secured["total"] == 20  # pass+fail+n_a
    # drilldown to the covered (passing) set, re-runnable
    assert _normalize_filters(secured["drilldown"]["filters"]) is not None


def test_matrix_none_category_filter_resolves_by_id_or_name():
    by_id = get_compliance_matrix_impl(posture_repository=_repo(),
                                       category="secured")[1]
    by_name = get_compliance_matrix_impl(posture_repository=_repo(),
                                         category="Secured")[1]
    assert len(by_id["series"]) == 1
    assert by_id["series"][0]["posture_category"] == "Secured"
    assert len(by_name["series"]) == 1  # name resolves too


def test_matrix_unknown_posture_category_is_loud():
    text, art = get_compliance_matrix_impl(posture_repository=_repo(),
                                           category="nonexistent")
    assert "Unknown" in text
    assert art is None


def test_matrix_by_asset_category_emits_cells_with_rerunnable_drilldown():
    text, art = get_compliance_matrix_impl(posture_repository=_repo(),
                                           group_by="asset_category")
    _valid_table(art)
    cell = next(r for r in art["series"]
                if r["posture_category"] == "Secured"
                and r["asset_category"] == "server")
    assert cell["covered"] == 7 and cell["total"] == 10
    dd = cell["drilldown"]
    # re-runnable through the same grammar query_assets uses
    assert _normalize_filters(dd["filters"]) is not None
    fields = [f.get("field") for f in dd["filters"]]
    assert "Category" in fields
    assert any("posture_state" in str(f) for f in fields)
    # the pivoted column bucket is recorded
    assert dd["col_value"] == "server"


def test_matrix_by_asset_category_filter_narrows_columns():
    text, art = get_compliance_matrix_impl(
        posture_repository=_repo(), group_by="asset_category",
        asset_category="server")
    assert all(r["asset_category"] == "server" for r in art["series"])
    assert len(art["series"]) == 2  # secured/server + patched/server


def test_matrix_unknown_asset_category_warns_loudly():
    """§4: warn when an asset-category scope matches zero — never an empty
    matrix that reads as 'all compliant'."""
    text, art = get_compliance_matrix_impl(
        posture_repository=_repo(), group_by="asset_category",
        asset_category="toaster")
    assert "toaster" in text
    assert art is None


def test_matrix_sub_rule_uses_live_preview_per_subrule():
    read_svc = MagicMock()
    read_svc.list_categories.return_value = {"categories": [{
        "id": "secured", "name": "Secured", "applicable_categories": [],
        "sub_rules": [{
            "id": "sr1", "name": "Has EDR", "enabled": True,
            "target_filters": [{"field_id": {"kind": "static", "name": "Category"},
                                "operator": "eq", "value": "server"}],
            "compliance_criteria": {"logic": "all", "conditions": []},
        }]}]}
    pp = MagicMock()
    pp.preview.return_value = {"matched": 10, "pass": 7, "fail": 3}
    text, art = get_compliance_matrix_impl(
        posture_repository=_repo(), group_by="sub_rule",
        posture_category_read_service=read_svc, posture_preview_service=pp)
    pp.preview.assert_called_once()
    assert "Has EDR" in text
    assert "10" in text and "7" in text and "3" in text
    _valid_table(art)
    row = art["series"][0]
    assert row["matched"] == 10 and row["pass"] == 7 and row["fail"] == 3
    # drilldown = the sub-rule's own target filters, re-runnable
    assert _normalize_filters(row["drilldown"]["filters"]) is not None


def test_matrix_sub_rule_scoped_to_asset_category_prepends_filter():
    read_svc = MagicMock()
    read_svc.list_categories.return_value = {"categories": [{
        "id": "secured", "name": "Secured", "applicable_categories": [],
        "sub_rules": [{
            "id": "sr1", "name": "Has EDR", "enabled": True,
            "target_filters": [{"field_id": {"kind": "static", "name": "OS Normalized"},
                                "operator": "contains", "value": "Win"}],
            "compliance_criteria": {"logic": "all", "conditions": []},
        }]}]}
    pp = MagicMock()
    pp.preview.return_value = {"matched": 1, "pass": 1, "fail": 0}
    get_compliance_matrix_impl(
        posture_repository=_repo(), group_by="sub_rule", asset_category="server",
        posture_category_read_service=read_svc, posture_preview_service=pp)
    target_filters = pp.preview.call_args.args[0]
    # the asset-category scope is prepended to the sub-rule's own filters
    assert target_filters[0] == {"field_id": {"kind": "static", "name": "Category"},
                                 "operator": "eq", "value": "server"}
    assert len(target_filters) == 2


def test_matrix_drilldown_flag_resolves_asset_ids_via_engine():
    qe = MagicMock()
    qe.asset_ids_matching.return_value = {"id1", "id2", "id3"}
    text, art = get_compliance_matrix_impl(
        posture_repository=_repo(), group_by="asset_category", drilldown=True,
        query_engine=qe, db=MagicMock())
    qe.asset_ids_matching.assert_called()
    assert any("asset_ids" in r["drilldown"] for r in art["series"])
    a_cell = next(r for r in art["series"] if r["drilldown"].get("asset_ids"))
    assert a_cell["drilldown"]["asset_id_count"] == 3


def test_matrix_without_drilldown_flag_omits_asset_ids_but_keeps_filter():
    qe = MagicMock()
    text, art = get_compliance_matrix_impl(
        posture_repository=_repo(), group_by="asset_category", drilldown=False,
        query_engine=qe, db=MagicMock())
    qe.asset_ids_matching.assert_not_called()
    assert all("asset_ids" not in r["drilldown"] for r in art["series"])
    assert all(r["drilldown"]["filters"] for r in art["series"])
