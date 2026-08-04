# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the unified rule chat tools (rules.py): preview_rule,
preview_rule_output, list_rules, get_rule_history.

These tools wrap the existing tag / alert / posture services (which already
delegate filter matching to the canonical AssetQueryCompiler). The tests pin
the contract the chat layer owns: ONE field/op grammar normalized to each
service's shape, LOUD errors over silent drops, and faithful surfacing of the
counts the backend computes (never fabricated).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp.tools.rules import (
    _to_rule_conditions,
    _to_rule_criteria,
    preview_rule_impl,
)


# --- shared normalization -----------------------------------------------------

def test_to_rule_conditions_normalizes_bare_name_and_both_key_spellings():
    """The model speaks ONE grammar ({field, op, value}); the rule services
    speak {field_id, operator, value}. Normalization must map a bare display
    name to a typed FieldId dict and accept either key spelling, so a wizard-
    style ({field_id, operator}) and a chat-style ({field, op}) call behave
    identically."""
    chat = _to_rule_conditions(
        [{"field": "Category", "op": "eq", "value": "server"}], None)
    wizard = _to_rule_conditions(
        [{"field_id": "Category", "operator": "eq", "value": "server"}], None)
    expected = [{"field_id": {"kind": "static", "name": "Category"},
                 "operator": "eq", "value": "server"}]
    assert chat == expected
    assert wizard == expected


def test_to_rule_conditions_accepts_json_string():
    """LLMs frequently serialise the array as a JSON string-in-string."""
    out = _to_rule_conditions(
        '[{"field": "Owner", "op": "contains", "value": "alice"}]', None)
    assert out == [{"field_id": {"kind": "static", "name": "Owner"},
                    "operator": "contains", "value": "alice"}]


def test_to_rule_conditions_unknown_field_raises_not_drops():
    """A dropped filter silently WIDENS the previewed population — a correctness
    AND over-disclosure risk (§4). Unknown fields must raise, not vanish."""
    import pytest
    with pytest.raises(ValueError):
        _to_rule_conditions(
            [{"field": "Definitely Not A Field", "op": "eq", "value": "x"}],
            None)


def test_to_rule_criteria_validates_logic_enum():
    import pytest
    with pytest.raises(ValueError):
        _to_rule_criteria(
            {"logic": "maybe", "conditions": [
                {"field": "Owner", "op": "eq", "value": "a"}]}, None)


def test_to_rule_criteria_normalizes_conditions():
    out = _to_rule_criteria(
        {"logic": "any", "conditions": [
            {"field": "Owner", "op": "eq", "value": "a"}]}, None)
    assert out == {"logic": "any", "conditions": [
        {"field_id": {"kind": "static", "name": "Owner"},
         "operator": "eq", "value": "a"}]}


# --- preview_rule dispatch ----------------------------------------------------

def test_preview_rule_unknown_kind_is_loud():
    out = preview_rule_impl("nonsense",
                            [{"field": "Category", "op": "eq", "value": "s"}])
    assert "Unknown rule_kind" in out


def test_preview_rule_requires_at_least_one_target_filter():
    """Every preview service mandates a non-empty target population; an empty
    preview would silently mean 'the whole inventory'."""
    out = preview_rule_impl("tag", [], tag_preview_service=MagicMock())
    assert "at least one" in out.lower()


def test_preview_rule_tag_compliance_delegates_and_surfaces_counts():
    tag_svc = MagicMock()
    tag_svc.preview_compliance.return_value = (10, 7, 3, 12)  # total, pass, fail, ms
    out = preview_rule_impl(
        "tag",
        [{"field": "Category", "op": "eq", "value": "server"}],
        {"logic": "all", "conditions": [
            {"field": "Owner", "op": "eq", "value": "alice"}]},
        resolver=None, tag_preview_service=tag_svc)

    # delegated with the canonical {field_id, operator, value} shape
    args = tag_svc.preview_compliance.call_args.args
    assert args[0] == [{"field_id": {"kind": "static", "name": "Category"},
                        "operator": "eq", "value": "server"}]
    assert args[1] == {"logic": "all", "conditions": [
        {"field_id": {"kind": "static", "name": "Owner"},
         "operator": "eq", "value": "alice"}]}
    # surfaces the counts the backend computed
    assert "10" in out and "7" in out and "3" in out
    tag_svc.count_matching_assets.assert_not_called()


def test_preview_rule_tag_without_criteria_uses_count_path():
    tag_svc = MagicMock()
    tag_svc.count_matching_assets.return_value = (42, 5)  # count, ms
    out = preview_rule_impl(
        "tag", [{"field": "Category", "op": "eq", "value": "server"}],
        resolver=None, tag_preview_service=tag_svc)
    tag_svc.count_matching_assets.assert_called_once()
    tag_svc.preview_compliance.assert_not_called()
    assert "42" in out


def test_preview_rule_posture_delegates_and_surfaces_pass_fail():
    pp = MagicMock()
    pp.preview.return_value = {"matched": 20, "pass": 12, "fail": 8}
    out = preview_rule_impl(
        "posture", [{"field": "Category", "op": "eq", "value": "server"}],
        resolver=None, posture_preview_service=pp)
    args = pp.preview.call_args.args
    assert args[0] == [{"field_id": {"kind": "static", "name": "Category"},
                        "operator": "eq", "value": "server"}]
    # no criteria -> empty all-criteria passed (not None, the service indexes it)
    assert args[1] == {"logic": "all", "conditions": []}
    assert "20" in out and "12" in out and "8" in out


def test_preview_rule_alert_uses_alert_filter_shape_and_total_only():
    """Alert filter rules AND-merge criteria — the backend returns only
    total_matching; we must surface THAT and not fabricate a pass/fail split."""
    al = MagicMock()
    al.preview_rule.return_value = {"total_matching": 42}
    out = preview_rule_impl(
        "alert", [{"field": "Category", "op": "eq", "value": "server"}],
        resolver=None, alerts_service=al)
    args = al.preview_rule.call_args.args
    # alert repo expects {field, op, value} (field becomes field_id)
    assert args[0] == [{"field": {"kind": "static", "name": "Category"},
                        "op": "eq", "value": "server"}]
    assert "42" in out


def test_preview_rule_unknown_field_is_loud_and_does_not_call_service():
    tag_svc = MagicMock()
    out = preview_rule_impl(
        "tag", [{"field": "Not A Real Field", "op": "eq", "value": "x"}],
        resolver=None, tag_preview_service=tag_svc)
    assert "Unknown" in out
    tag_svc.preview_compliance.assert_not_called()
    tag_svc.count_matching_assets.assert_not_called()


def test_preview_rule_unknown_operator_is_loud():
    tag_svc = MagicMock()
    out = preview_rule_impl(
        "tag", [{"field": "Category", "op": "wat", "value": "x"}],
        resolver=None, tag_preview_service=tag_svc)
    assert "operator" in out.lower()
    tag_svc.preview_compliance.assert_not_called()


def test_preview_rule_service_missing_reports_unavailable():
    out = preview_rule_impl(
        "posture", [{"field": "Category", "op": "eq", "value": "s"}],
        resolver=None, posture_preview_service=None)
    assert "not available" in out.lower()


def test_preview_rule_echoes_rerunnable_drilldown_filters():
    """§4: a preview count must be substantiated by a re-runnable filter so the
    user can 'show me those N' via query_assets. The echoed filters must
    round-trip the query grammar."""
    from src.mcp.tools._query import _normalize_filters
    tag_svc = MagicMock()
    tag_svc.count_matching_assets.return_value = (3, 1)
    out = preview_rule_impl(
        "tag", [{"field": "Category", "op": "eq", "value": "server"}],
        resolver=None, tag_preview_service=tag_svc)
    # the original (bare) filter is echoed for re-use
    assert "Category" in out
    # and it is genuinely re-runnable
    assert _normalize_filters(
        [{"field": "Category", "op": "eq", "value": "server"}]) is not None


# --- preview_rule_output ------------------------------------------------------

from src.mcp.tools.rules import preview_rule_output_impl  # noqa: E402


def test_preview_rule_output_field_mode_passes_typed_field_id():
    """field mode MUST pass a TYPED FieldId, not a dict: posture preview_output
    feeds output_field_id straight to FieldResolver.resolve (isinstance dispatch),
    which raises InvalidFieldError on a dict and is then swallowed → every output
    silently blank. A dict here = a broken (empty-output) field preview."""
    from src.modules.fields.models import StaticField
    pp = MagicMock()
    pp.preview_output.return_value = {
        "matched": 5, "passed": 4,
        "samples": [{"hostname": "web01", "output": "CrowdStrike"},
                    {"hostname": "web02", "output": "Defender"}],
    }
    out = preview_rule_output_impl(
        [{"field": "Category", "op": "eq", "value": "server"}],
        None, output_mode="field", output_field_id="OS Normalized",
        resolver=None, posture_preview_service=pp)

    fid = pp.preview_output.call_args.kwargs["output_field_id"]
    assert not isinstance(fid, dict), "must be a typed FieldId, not a dict"
    assert fid == StaticField(kind="static", name="OS Normalized")
    assert pp.preview_output.call_args.kwargs["output_mode"] == "field"
    assert "5" in out and "4" in out
    assert "web01" in out and "CrowdStrike" in out


def test_preview_rule_output_text_mode_uses_output_text():
    pp = MagicMock()
    pp.preview_output.return_value = {
        "matched": 2, "passed": 2,
        "samples": [{"hostname": "h1", "output": "must encrypt"}]}
    out = preview_rule_output_impl(
        [{"field": "Category", "op": "eq", "value": "server"}],
        None, output_mode="text", output_text="must encrypt",
        resolver=None, posture_preview_service=pp)
    kwargs = pp.preview_output.call_args.kwargs
    assert kwargs["output_mode"] == "text"
    assert kwargs["output_text"] == "must encrypt"
    assert "h1" in out


def test_preview_rule_output_invalid_mode_is_loud():
    out = preview_rule_output_impl(
        [{"field": "Category", "op": "eq", "value": "s"}], None,
        output_mode="banana", posture_preview_service=MagicMock())
    assert "Unknown output_mode" in out


def test_preview_rule_output_field_mode_requires_field_id():
    out = preview_rule_output_impl(
        [{"field": "Category", "op": "eq", "value": "s"}], None,
        output_mode="field", output_field_id=None,
        posture_preview_service=MagicMock())
    assert "output_field_id" in out


def test_preview_rule_output_text_mode_requires_text():
    out = preview_rule_output_impl(
        [{"field": "Category", "op": "eq", "value": "s"}], None,
        output_mode="text", output_text=None,
        posture_preview_service=MagicMock())
    assert "output_text" in out


def test_preview_rule_output_unknown_target_field_is_loud():
    pp = MagicMock()
    out = preview_rule_output_impl(
        [{"field": "Bogus", "op": "eq", "value": "s"}], None,
        output_mode="text", output_text="x",
        resolver=None, posture_preview_service=pp)
    assert "Unknown" in out
    pp.preview_output.assert_not_called()


def test_preview_rule_output_service_missing():
    out = preview_rule_output_impl(
        [{"field": "Category", "op": "eq", "value": "s"}], None,
        output_mode="text", output_text="x", posture_preview_service=None)
    assert "not available" in out.lower()


# --- list_rules ---------------------------------------------------------------

from src.mcp.tools.rules import list_rules_impl  # noqa: E402


class _Model:
    """Mimic a pydantic model with model_dump(mode='json')."""
    def __init__(self, payload):
        self._p = payload

    def model_dump(self, **kw):
        return self._p


def test_list_rules_unknown_kind_is_loud():
    out = list_rules_impl("dragon")
    assert "Unknown rule_kind" in out


def test_list_rules_service_missing():
    out = list_rules_impl("tag", tag_rule_service=None)
    assert "not available" in out.lower()


def test_list_rules_tag_surfaces_id_and_filters():
    """The whole point of the unified tool: stop dropping the id + target_filters
    + compliance logic the service already returns (so downstream preview_rule /
    query_assets can reuse them)."""
    svc = MagicMock()
    svc.list_rules.return_value = _Model({
        "rules": [{
            "id": "t1", "name": "Tag A", "type": "compliance",
            "enabled": True, "priority": 10,
            "target_filters": [{"field_id": {"kind": "static", "name": "Category"},
                                "operator": "eq", "value": "server"}],
            "compliance_criteria": {"logic": "all", "conditions": []},
            "tags": {"pass": ["ok"]}, "execution_count": 3,
        }],
        "total": 1, "page": 1, "limit": 20,
    })
    out = list_rules_impl("tag", tag_rule_service=svc)
    assert "Tag A" in out
    assert "id=t1" in out
    assert "target_filters" in out and "Category" in out
    assert "compliance_criteria" in out
    svc.list_rules.assert_called_once()


def test_list_rules_tag_detail_by_id():
    svc = MagicMock()
    svc.get_rule.return_value = _Model({
        "id": "t9", "name": "Detail Rule", "type": "classification",
        "enabled": False, "priority": 1,
        "target_filters": [{"field_id": {"kind": "static", "name": "Owner"},
                            "operator": "empty", "value": None}],
    })
    out = list_rules_impl("tag", rule_id="t9", tag_rule_service=svc)
    svc.get_rule.assert_called_once_with("t9")
    svc.list_rules.assert_not_called()
    assert "Detail Rule" in out and "id=t9" in out


def test_list_rules_tag_detail_not_found_is_loud():
    svc = MagicMock()
    svc.get_rule.return_value = None
    out = list_rules_impl("tag", rule_id="missing", tag_rule_service=svc)
    assert "No tag rule" in out


def test_list_rules_paging_reports_total_and_overflow():
    svc = MagicMock()
    svc.list_rules.return_value = _Model({
        "rules": [{"id": f"t{i}", "name": f"R{i}"} for i in range(10)],
        "total": 25, "page": 1, "limit": 10,
    })
    out = list_rules_impl("tag", page=1, per_page=10, tag_rule_service=svc)
    assert "25 total" in out
    assert "page 1/3" in out
    assert "more" in out  # overflow signal, never silent truncation


def test_list_rules_alert_in_memory_paging_and_filters():
    svc = MagicMock()
    svc.get_all_rules.return_value = {"rules": [
        {"id": "a1", "name": "A1", "type": "security", "severity": "high",
         "source": "system", "enabled": True, "evaluation_type": "filter",
         "target_filters": [{"field_id": {"kind": "static", "name": "Category"},
                             "operator": "eq", "value": "server"}]},
        {"id": "a2", "name": "A2", "type": "security", "severity": "low",
         "source": "user", "enabled": False, "evaluation_type": "sql",
         "query": "SELECT 1"},
    ]}
    out = list_rules_impl("alert", enabled_only=True, alerts_service=svc)
    assert "A1" in out and "id=a1" in out
    assert "A2" not in out  # disabled filtered out


def test_list_rules_alert_detail_surfaces_threshold_and_provenance():
    svc = MagicMock()
    svc.get_rule.return_value = {
        "id": "a1", "name": "A1", "slug": "a1", "type": "security",
        "severity": "high", "source": "system", "enabled": True,
        "evaluation_type": "filter",
        "target_filters": [{"field_id": {"kind": "static", "name": "Category"},
                            "operator": "eq", "value": "server"}],
        "threshold": {"condition": "exceeds", "value": 5, "unit": "number"},
        "silence_duration": 60, "created_by": "u1", "created_at": "2026-01-01",
    }
    out = list_rules_impl("alert", rule_id="a1", alerts_service=svc)
    assert "slug" in out and "threshold" in out
    assert "silence_duration" in out and "created_by" in out


def test_list_rules_posture_flattens_subrules_with_applicable_and_last_edit():
    svc = MagicMock()
    svc.list_categories.return_value = {"categories": [{
        "id": "secured", "name": "Secured",
        "applicable_categories": ["server", "workstation"],
        "sub_rules": [{
            "id": "sr1", "name": "Has EDR", "enabled": True, "display_order": 1,
            "target_filters": [{"field_id": {"kind": "static", "name": "Category"},
                                "operator": "eq", "value": "server"}],
            "compliance_criteria": {"logic": "all", "conditions": []},
            "output_mode": "field",
            "output_value": {"mode": "field",
                             "field_id": {"kind": "static", "name": "Owner"}},
            "last_edit": {"action": "update", "changed_by_username": "admin",
                          "at": "2026-01-02"},
        }],
    }]}
    out = list_rules_impl("posture", posture_category_read_service=svc)
    assert "Has EDR" in out and "id=sr1" in out
    assert "Secured" in out
    assert "server, workstation" in out
    assert "last edit" in out and "admin" in out


def test_list_rules_posture_detail_by_subrule_id():
    svc = MagicMock()
    svc.list_categories.return_value = {"categories": [{
        "id": "secured", "name": "Secured", "applicable_categories": [],
        "sub_rules": [{"id": "sr1", "name": "Has EDR", "enabled": True,
                       "target_filters": [{"field_id": {"kind": "static",
                                                        "name": "Category"},
                                           "operator": "eq", "value": "server"}]}],
    }]}
    out = list_rules_impl("posture", rule_id="sr1",
                          posture_category_read_service=svc)
    assert "Has EDR" in out and "target_filters" in out


def test_list_rules_posture_detail_not_found_is_loud():
    svc = MagicMock()
    svc.list_categories.return_value = {"categories": []}
    out = list_rules_impl("posture", rule_id="ghost",
                          posture_category_read_service=svc)
    assert "No posture sub-rule" in out


# --- get_rule_history ---------------------------------------------------------

from src.mcp.tools.rules import get_rule_history_impl  # noqa: E402


def test_get_rule_history_unknown_kind_is_loud():
    out = get_rule_history_impl("wombat", "x")
    assert "Unknown rule_kind" in out


def test_get_rule_history_posture_renders_audit_trail():
    svc = MagicMock()
    svc.get_history.return_value = [
        {"action": "update", "changed_by": "u1", "changed_by_username": "admin",
         "created_at": "2026-02-01T10:00:00Z",
         "before_state": {"enabled": True}, "after_state": {"enabled": False}},
        {"action": "create", "changed_by": "u1", "changed_by_username": "admin",
         "created_at": "2026-01-01T09:00:00Z",
         "before_state": None, "after_state": {"enabled": True}},
    ]
    out = get_rule_history_impl("posture_sub", "sr1",
                               posture_sub_rule_mgmt_service=svc)
    svc.get_history.assert_called_once_with("sr1")
    assert "update" in out and "create" in out
    assert "admin" in out
    assert "enabled" in out  # before/after surfaced


def test_get_rule_history_posture_empty_is_explicit():
    svc = MagicMock()
    svc.get_history.return_value = []
    out = get_rule_history_impl("posture_sub", "sr1",
                               posture_sub_rule_mgmt_service=svc)
    assert "No change history" in out


def test_get_rule_history_posture_service_missing():
    out = get_rule_history_impl("posture_sub", "sr1",
                               posture_sub_rule_mgmt_service=None)
    assert "not available" in out.lower()


def test_get_rule_history_tag_is_honest_about_no_backend():
    """Tag rules have NO change-history table (only an execution log with no
    reader). The tool must say so honestly and point at the only available
    signal — not fabricate an audit trail."""
    out = get_rule_history_impl("tag", "t1")
    assert "not recorded" in out.lower()
    assert "get_audit_trail" in out
    assert "/api/tag-rules" in out


def test_get_rule_history_alert_is_honest_about_no_backend():
    out = get_rule_history_impl("alert", "a1")
    assert "not recorded" in out.lower()
    assert "get_audit_trail" in out
    assert "/api/alert-rules" in out
