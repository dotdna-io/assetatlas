# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unified rule chat tools — preview, output-preview, list, and history across
the three rule kinds (tag / alert / posture).

These are CHAT-layer primitives that unify the granular per-kind MCP tools
(``list_tag_rules`` / ``list_alert_rules`` / ``list_posture_rules`` and the
preview routes) behind a single ``rule_kind`` dispatch. They are thin wrappers:
every filter/criteria is matched by the existing services, which delegate to the
canonical ``AssetQueryCompiler`` (``current_app.query_engine``). NO matching
logic lives here — re-implementing it would be a forbidden 5th engine.

The model speaks ONE filter grammar everywhere (``{field, op, value}`` with a
bare display name, an encoded FieldId, or a ``{field_id}`` dict; ``op`` a registry
code/alias). This module normalizes it to each service's native shape:
tag/posture want ``{field_id, operator, value}``; the alert repository wants
``{field, op, value}`` (its ``field`` becomes the compiler's ``field_id``).
Unresolvable fields/operators raise (LOUD) rather than silently widening the
previewed population (§4).
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Dict, List, Optional

from src.modules.fields.encoding import coerce_field_id, decode

from src.mcp.tools._common import parse_filters_arg, unknown_enum
from src.mcp.tools._query import (
    _UNKNOWN_FIELD_HINT,
    _normalize_field_name,
    _normalize_filters,
)
from src.mcp.tools.alert_rules import format_alert_rule_lines
from src.mcp.tools.posture import format_posture_subrule_lines

_RULE_KINDS = ("tag", "alert", "posture")


# --- shared field/op normalization -------------------------------------------

def _to_rule_conditions(filters: Any, resolver=None) -> List[Dict[str, Any]]:
    """Normalize the chat filter grammar to the rule services' canonical
    ``{field_id, operator, value}`` shape.

    Accepts a Python list or a JSON string; accepts either key spelling
    (``field``/``field_id``, ``op``/``operator``); resolves bare names + encoded
    ids via the same ``_normalize_filters`` path ``query_assets`` uses — so an
    unresolvable field or operator RAISES (``ValueError`` subclass) instead of
    being dropped (a dropped filter silently widens the previewed set — §4).
    """
    parsed, err = parse_filters_arg(filters)
    if err:
        raise ValueError(err)
    chat: List[Dict[str, Any]] = []
    for c in (parsed or []):
        if not isinstance(c, dict):
            continue
        chat.append({
            "field": c.get("field", c.get("field_id")),
            "op": c.get("op", c.get("operator")),
            "value": c.get("value"),
        })
    normed = _normalize_filters(chat, resolver) or []
    return [{"field_id": n["field_id"], "operator": n["op"], "value": n["value"]}
            for n in normed]


def _to_rule_criteria(criteria: Any, resolver=None) -> Optional[Dict[str, Any]]:
    """Normalize a ``compliance_criteria`` object to
    ``{logic: all|any, conditions: [{field_id, operator, value}]}`` or ``None``.

    ``logic`` is validated against the enum (LOUD on anything else) and its
    conditions are normalized exactly like target filters."""
    if not criteria:
        return None
    if isinstance(criteria, str):
        try:
            criteria = json.loads(criteria)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in compliance_criteria: {e}")
    if not isinstance(criteria, dict):
        raise ValueError(
            "compliance_criteria must be an object "
            "{logic: 'all'|'any', conditions: [{field, op, value}]}.")
    logic = str(criteria.get("logic") or "all").lower()
    if logic not in ("all", "any"):
        raise ValueError(
            unknown_enum("compliance_criteria.logic", logic, ["all", "any"]))
    conds = _to_rule_conditions(criteria.get("conditions"), resolver)
    return {"logic": logic, "conditions": conds}


def _alert_conditions(conds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map canonical ``{field_id, operator, value}`` to the alert repository's
    ``{field, op, value}`` shape (its ``field`` is passed to the compiler as
    ``field_id``)."""
    return [{"field": c["field_id"], "op": c["operator"], "value": c["value"]}
            for c in conds]


def _to_field_id(raw: Any, resolver=None):
    """Resolve a bare display name / encoded id / FieldId dict to a TYPED FieldId.

    MUST return a typed FieldId, not a dict: posture ``preview_output`` feeds
    ``output_field_id`` straight to ``FieldResolver.resolve`` (via
    ``fetch_asset_field_values``), which dispatches on ``isinstance(field_id,
    StaticField)`` etc. — a dict matches none, raises ``InvalidFieldError``, and
    ``preview_output`` SWALLOWS it, silently blanking every output value. LOUD on
    an unknown bare name."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return coerce_field_id(raw)
    enc = _normalize_field_name(str(raw), resolver)
    if not enc:
        raise ValueError(
            _UNKNOWN_FIELD_HINT.format(param="output field", name=raw))
    return decode(enc)


def _drilldown_text(conds: List[Dict[str, Any]]) -> str:
    """Echo the target filters as a re-runnable query_assets call so a preview
    count can always be substantiated by 'show me those N' (§4/§3.6). The
    ``field_id`` form round-trips the same resolution path query_assets uses."""
    dd = [{"field_id": c["field_id"], "op": c["operator"], "value": c["value"]}
          for c in conds]
    return ("To list the matching assets: "
            "query_assets(mode='list', filters=" + json.dumps(dd) + ")")


# --- preview_rule -------------------------------------------------------------

def preview_rule_impl(
    rule_kind: str,
    target_filters: Any,
    compliance_criteria: Any = None,
    *,
    resolver=None,
    tag_preview_service=None,
    alerts_service=None,
    posture_preview_service=None,
) -> str:
    """Dry-run a candidate rule: how many assets match (and, where the backend
    computes it, pass/fail). Delegates to the per-kind preview service, which
    runs the canonical compiler."""
    if rule_kind not in _RULE_KINDS:
        return unknown_enum("rule_kind", rule_kind, _RULE_KINDS)
    try:
        conds = _to_rule_conditions(target_filters, resolver)
        crit = _to_rule_criteria(compliance_criteria, resolver)
    except ValueError as e:
        return str(e)
    if not conds:
        return ("preview_rule needs at least one target filter (the population "
                "to evaluate). Pass target_filters=[{field, op, value}].")

    if rule_kind == "tag":
        return _preview_tag(conds, crit, tag_preview_service)
    if rule_kind == "alert":
        return _preview_alert(conds, crit, alerts_service)
    return _preview_posture(conds, crit, posture_preview_service)


def _preview_tag(conds, crit, svc) -> str:
    if svc is None:
        return "Tag rule preview service not available."
    if crit:
        total, passed, failed, ms = svc.preview_compliance(conds, crit)
    else:
        total, ms = svc.count_matching_assets(conds)
        passed = failed = None
    lines = [f"Tag rule preview — {total} asset(s) match the target filters."]
    if passed is not None:
        lines.append(f"Pass (meet compliance criteria): {passed}")
        lines.append(f"Fail: {failed}")
    else:
        lines.append("(No compliance_criteria given — pass/fail not evaluated; "
                     "total_matching is the target population.)")
    lines.append(f"Evaluated in {ms} ms.")
    lines.append(_drilldown_text(conds))
    return "\n".join(lines)


def _preview_posture(conds, crit, svc) -> str:
    if svc is None:
        return "Posture preview service not available."
    res = svc.preview(conds, crit or {"logic": "all", "conditions": []})
    matched = res.get("matched", 0)
    passed = res.get("pass", 0)
    failed = res.get("fail", 0)
    return "\n".join([
        f"Posture rule preview — {matched} asset(s) match the target filters.",
        f"Pass (meet compliance criteria): {passed}",
        f"Fail: {failed}",
        _drilldown_text(conds),
    ])


def _preview_alert(conds, crit, svc) -> str:
    if svc is None:
        return "Alerts service not available."
    alert_cc = (
        {"logic": crit["logic"], "conditions": _alert_conditions(crit["conditions"])}
        if crit else None
    )
    res = svc.preview_rule(_alert_conditions(conds), alert_cc)
    total = res.get("total_matching", 0)
    scope = ("the target filters + compliance criteria"
             if crit else "the target filters")
    return "\n".join([
        f"Alert rule preview — {total} asset(s) match {scope}.",
        "(Alert filter rules don't split pass/fail — compliance criteria are "
        "AND-merged into the match; total_matching is the count that would "
        "drive the alert.)",
        _drilldown_text(conds),
    ])


# --- preview_rule_output (posture) -------------------------------------------

def preview_rule_output_impl(
    target_filters: Any,
    compliance_criteria: Any = None,
    *,
    output_mode: str = "field",
    output_field_id: Any = None,
    output_text: Optional[str] = None,
    resolver=None,
    posture_preview_service=None,
) -> str:
    """Preview a posture sub-rule's RESOLVED OUTPUT values (not just match
    counts): for the passing assets, what each one's output would be — a field's
    value (``output_mode='field'``) or a literal string (``output_mode='text'``).
    Wraps ``posture_preview_service.preview_output`` (canonical compiler)."""
    if output_mode not in ("field", "text"):
        return unknown_enum("output_mode", output_mode, ["field", "text"])
    try:
        conds = _to_rule_conditions(target_filters, resolver)
        crit = _to_rule_criteria(compliance_criteria, resolver)
    except ValueError as e:
        return str(e)
    if not conds:
        return ("preview_rule_output needs at least one target filter. "
                "Pass target_filters=[{field, op, value}].")

    field_id = None
    if output_mode == "field":
        if not output_field_id:
            return ("output_mode='field' needs output_field_id (the catalog "
                    "field whose value becomes each asset's output).")
        try:
            field_id = _to_field_id(output_field_id, resolver)
        except ValueError as e:
            return str(e)
    elif not output_text:
        return "output_mode='text' needs output_text (the literal output value)."

    if posture_preview_service is None:
        return "Posture preview service not available."

    res = posture_preview_service.preview_output(
        target_filters=conds,
        compliance_criteria=crit or {"logic": "all", "conditions": []},
        output_mode=output_mode,
        output_field_id=field_id,
        output_text=output_text,
    )
    matched = res.get("matched", 0)
    passed = res.get("passed", 0)
    samples = res.get("samples") or []
    lines = [
        f"Posture output preview — {matched} asset(s) match the target "
        f"filters; {passed} pass the compliance criteria.",
    ]
    if samples:
        lines.append("")
        lines.append("Sample outputs (up to 5 passing assets):")
        for s in samples:
            host = s.get("hostname") or "(no hostname)"
            output = s.get("output")
            output = "(none)" if output in (None, "") else output
            lines.append(f"- {host}: {output}")
    else:
        lines.append("(No passing assets to sample.)")
    return "\n".join(lines)


# --- list_rules ---------------------------------------------------------------

def _as_dict(obj: Any) -> Dict[str, Any]:
    """Normalize a pydantic model (or dict) to a plain dict. The tag service
    returns pydantic models; the alert/posture services return plain dicts."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _format_tag_rule(rule: Dict[str, Any]) -> List[str]:
    name = rule.get("name") or "—"
    lines = [f"## {name}  (id={rule.get('id')})"]
    meta = [f"type: {rule.get('type')}", f"enabled: {rule.get('enabled')}"]
    # TagRule has no `priority` field; only surface it if a future model adds one
    # (avoid emitting a constant "priority: None" the model would misread).
    if rule.get("priority") is not None:
        meta.append(f"priority: {rule['priority']}")
    lines.append("- " + " | ".join(meta))
    if rule.get("description"):
        lines.append(f"- description: {rule['description']}")
    if rule.get("tags"):
        lines.append(f"- tags: {json.dumps(rule['tags'])}")
    if rule.get("target_filters"):
        lines.append(f"- target_filters: {json.dumps(rule['target_filters'])}")
    if rule.get("compliance_criteria"):
        lines.append(
            f"- compliance_criteria: {json.dumps(rule['compliance_criteria'])}")
    if rule.get("detection_config"):
        lines.append(
            f"- detection_config: {json.dumps(rule['detection_config'])}")
    stats = {k: rule.get(k) for k in (
        "execution_count", "last_executed_at",
        "last_execution_assets_matched", "last_execution_assets_passed",
        "last_execution_assets_failed") if rule.get(k) is not None}
    if stats:
        lines.append(f"- stats: {json.dumps(stats, default=str)}")
    return lines


def _render_rule_list(
    kind: str, rules: List[Dict[str, Any]], total: int, page: int,
    per_page: int, formatter: Callable[[Dict[str, Any]], List[str]],
) -> str:
    if not rules:
        return f"No {kind} rules match."
    pages = max(1, math.ceil(total / per_page)) if per_page else 1
    blocks = [f"# {kind.capitalize()} rules ({total} total, page {page}/{pages})",
              ""]
    for r in rules:
        blocks.append("\n".join(formatter(r)))
        blocks.append("")
    if total > page * per_page:
        remaining = total - page * per_page
        blocks.append(
            f"… {remaining} more — pass page={page + 1} to continue "
            f"(or rule_id=… for one rule's full detail).")
    return "\n".join(blocks).rstrip()


def list_rules_impl(
    rule_kind: str,
    *,
    enabled_only: bool = False,
    rule_type: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    rule_id: Optional[str] = None,
    tag_rule_service=None,
    alerts_service=None,
    posture_category_read_service=None,
) -> str:
    """Unified, paged rule catalogue surfacing FULL definitions WITH ids for tag
    / alert / posture rules (so each rule's filters are reusable downstream)."""
    if rule_kind not in _RULE_KINDS:
        return unknown_enum("rule_kind", rule_kind, _RULE_KINDS)
    page = max(1, int(page or 1))
    per_page = max(1, min(int(per_page or 20), 100))
    if rule_kind == "tag":
        return _list_tag(tag_rule_service, enabled_only, rule_type, page,
                         per_page, rule_id)
    if rule_kind == "alert":
        return _list_alert(alerts_service, enabled_only, rule_type, page,
                           per_page, rule_id)
    return _list_posture(posture_category_read_service, enabled_only, rule_type,
                         page, per_page, rule_id)


def _list_tag(svc, enabled_only, rule_type, page, per_page, rule_id) -> str:
    if svc is None:
        return "Tag rule service not available."
    if rule_id:
        rule = svc.get_rule(rule_id)
        if rule is None:
            return f"No tag rule found with id {rule_id!r}."
        return "\n".join(_format_tag_rule(_as_dict(rule)))
    result = _as_dict(svc.list_rules(
        page=page, limit=per_page, rule_type=rule_type,
        enabled_only=enabled_only))
    rules = result.get("rules") or []
    total = int(result.get("total", len(rules)) or 0)
    return _render_rule_list("tag", rules, total, page, per_page,
                             _format_tag_rule)


def _list_alert(svc, enabled_only, rule_type, page, per_page, rule_id) -> str:
    if svc is None:
        return "Alerts service not available."
    if rule_id:
        rule = svc.get_rule(rule_id)
        if rule is None:
            return f"No alert rule found with id {rule_id!r}."
        return "\n".join(format_alert_rule_lines(_as_dict(rule)))
    all_rules = (svc.get_all_rules() or {}).get("rules") or []
    if enabled_only:
        all_rules = [r for r in all_rules if r.get("enabled")]
    if rule_type:
        all_rules = [r for r in all_rules
                     if (r.get("type") or "").lower() == rule_type.lower()]
    total = len(all_rules)
    start = (page - 1) * per_page
    page_rules = all_rules[start:start + per_page]
    return _render_rule_list("alert", page_rules, total, page, per_page,
                             format_alert_rule_lines)


def _list_posture(svc, enabled_only, rule_type, page, per_page, rule_id) -> str:
    if svc is None:
        return "Posture rule service not available."
    categories = (svc.list_categories() or {}).get("categories") or []
    flat: List[Dict[str, Any]] = []
    for cat in categories:
        for sr in (cat.get("sub_rules") or []):
            sr = dict(sr)
            sr["_category_name"] = cat.get("name")
            sr["_applicable"] = cat.get("applicable_categories")
            flat.append(sr)
    if rule_id:
        match = next((sr for sr in flat if str(sr.get("id")) == str(rule_id)),
                     None)
        if match is None:
            return f"No posture sub-rule found with id {rule_id!r}."
        return "\n".join(format_posture_subrule_lines(match))
    if enabled_only:
        flat = [sr for sr in flat if sr.get("enabled")]
    if rule_type:
        flat = [sr for sr in flat
                if (sr.get("_category_name") or "").lower() == rule_type.lower()]
    total = len(flat)
    start = (page - 1) * per_page
    page_rules = flat[start:start + per_page]
    return _render_rule_list("posture", page_rules, total, page, per_page,
                             format_posture_subrule_lines)


# --- get_rule_history ---------------------------------------------------------

_HISTORY_KINDS = ("posture_sub", "tag", "alert")

# Tag and alert rules have NO change-history table or read path in AssetAtlas —
# only posture sub-rules do (posture_sub_rule_history). The closest signal for
# the other two is the generic HTTP audit trail, surfaced by get_audit_trail.
_NO_HISTORY_PATH = {"tag": "/api/tag-rules/", "alert": "/api/alert-rules/"}


def get_rule_history_impl(
    rule_kind: str,
    rule_id: str,
    *,
    posture_sub_rule_mgmt_service=None,
) -> str:
    """Return a rule's change-audit trail. Only posture sub-rules carry one
    (create/update/toggle/delete with before/after + who/when); tag and alert
    rules have no change-history backend, so the tool says so honestly and
    points at the generic HTTP audit instead of fabricating a trail."""
    if rule_kind not in _HISTORY_KINDS:
        return unknown_enum("rule_kind", rule_kind, _HISTORY_KINDS)

    if rule_kind in _NO_HISTORY_PATH:
        path = _NO_HISTORY_PATH[rule_kind]
        return (
            f"Change history is not recorded for {rule_kind} rules — AssetAtlas "
            f"has no {rule_kind}-rule change-audit table (only posture "
            f"sub-rules do). For HTTP-level 'who called the {rule_kind}-rule API "
            f"and when', use get_audit_trail with path='{path}'.")

    if posture_sub_rule_mgmt_service is None:
        return "Posture sub-rule service not available."
    entries = posture_sub_rule_mgmt_service.get_history(rule_id) or []
    if not entries:
        return f"No change history recorded for posture sub-rule {rule_id!r}."

    lines = [f"# Change history — posture sub-rule {rule_id} "
             f"({len(entries)} event(s), newest first)", ""]
    for e in entries:
        who = e.get("changed_by_username") or e.get("changed_by") or "(unknown)"
        lines.append(f"### {e.get('created_at')} — {e.get('action')} by {who}")
        before = e.get("before_state")
        after = e.get("after_state")
        lines.append(f"- before: {json.dumps(before, default=str) if before else '(none)'}")
        lines.append(f"- after: {json.dumps(after, default=str) if after else '(none)'}")
        lines.append("")
    return "\n".join(lines).rstrip()
