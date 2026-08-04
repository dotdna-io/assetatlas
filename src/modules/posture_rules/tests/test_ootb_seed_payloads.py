# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pin migration 147's out-of-the-box posture seed payloads to the models.

The seeds live in SQL (147_revise_ootb_posture_rules.sql + schema-full.sql's
posture_sub_rules COPY), so nothing imports them — a model/catalog change that
orphans them (an operator removed, a field renamed, confidence_level's
allow-list narrowed) would otherwise surface only as broken defaults on the
next fresh install. These literals mirror the seeds exactly; validate them
through the same Pydantic models the API uses and check every operator is
legal for its field.
"""
import pytest

from src.modules.fields.catalog import FIELD_DATA_TYPES, VIRTUAL_FIELDS
from src.modules.fields.operators import operators_for, resolve as resolve_operator
from src.modules.fields.types import FieldDataType
from src.modules.posture_rules.models.rule import (
    PostureCategoryUpdateRequest,
    PostureSubRuleCreate,
)

_STATIC = "static"
_VIRTUAL = "virtual"


def _cond(value, kind, name, operator):
    return {"value": value, "field_id": {"kind": kind, "name": name}, "operator": operator}


def _tf_category(value):
    return [_cond(value, _STATIC, "Category", "any")]


_CONF_MED_HIGH = _cond("medium,high", _VIRTUAL, "confidence_level", "any")
_ACTIVE_15D = _cond("15d", _STATIC, "Last Seen", "within_last")

# The six seed rows, mirroring migration 147 / schema-full.sql verbatim.
OOTB_SEED_SUB_RULES = [
    {
        "category_id": "secured",
        "name": "Endpoint security coverage",
        "description": "Asset is seen by an endpoint-security data source with at least medium confidence and activity in the last 15 days.",
        "target_filters": _tf_category("server,workstation"),
        "compliance_criteria": {
            "logic": "all",
            "conditions": [
                _cond("endpoint security", _STATIC, "Data Source Category", "contains"),
                _CONF_MED_HIGH,
                _ACTIVE_15D,
            ],
        },
    },
    {
        "category_id": "secured",
        "name": "Endpoint security coverage (legacy EDR category)",
        "description": "Asset is seen by a data source still using the legacy EDR category, with at least medium confidence and activity in the last 15 days.",
        "target_filters": _tf_category("server,workstation"),
        "compliance_criteria": {
            "logic": "all",
            "conditions": [
                _cond("EDR", _STATIC, "Data Source Category", "contains"),
                _CONF_MED_HIGH,
                _ACTIVE_15D,
            ],
        },
    },
    {
        "category_id": "vulnerability_scanning",
        "name": "Vulnerability scanning coverage",
        "description": "Asset is seen by at least one vulnerability-management data source.",
        "target_filters": _tf_category("server,workstation,network"),
        "compliance_criteria": {
            "logic": "any",
            "conditions": [
                _cond("vulnerability management", _STATIC, "Data Source Category", "contains"),
            ],
        },
    },
    {
        "category_id": "domain_joined",
        "name": "Active Directory domain membership",
        "description": "Windows server or workstation seen by an Active Directory data source with at least medium confidence and activity in the last 15 days.",
        "target_filters": _tf_category("server,workstation"),
        "compliance_criteria": {
            "logic": "all",
            "conditions": [
                _cond("windows", _STATIC, "OS Normalized", "contains"),
                _cond("Active Directory", _STATIC, "Data Source", "contains"),
                _CONF_MED_HIGH,
                _ACTIVE_15D,
            ],
        },
    },
    {
        "category_id": "device_management",
        "name": "MDM enrollment",
        "description": "Mobile asset is seen by at least one mobile-device-management data source.",
        "target_filters": _tf_category("mobile"),
        "compliance_criteria": {
            "logic": "any",
            "conditions": [
                _cond("mobile device management", _STATIC, "Data Source Category", "contains"),
            ],
        },
    },
    {
        "category_id": "monitored",
        "name": "Monitoring coverage",
        "description": "Asset is seen by an observability, SIEM, or network-monitoring data source.",
        "target_filters": _tf_category("server,ot,network"),
        "compliance_criteria": {
            "logic": "any",
            "conditions": [
                _cond("observability", _STATIC, "Data Source Category", "contains"),
                _cond("SIEM", _STATIC, "Data Source Category", "contains"),
                _cond("Network Monitoring", _STATIC, "Data Source Category", "contains"),
            ],
        },
    },
]

# Final posture_categories.applicable_categories values after 147.
OOTB_APPLICABLE_CATEGORIES = {
    "secured": ["server", "workstation"],
    "vulnerability_scanning": ["server", "workstation", "network"],
    "domain_joined": ["server", "workstation"],
    "device_management": ["mobile"],
    "monitored": ["server", "ot", "network"],
}


def _rule_ids():
    return [r["name"] for r in OOTB_SEED_SUB_RULES]


def _all_conditions(rule):
    return list(rule["target_filters"]) + list(rule["compliance_criteria"]["conditions"])


class TestOotbSeedPayloadsValidate:
    @pytest.mark.parametrize("rule", OOTB_SEED_SUB_RULES, ids=_rule_ids())
    def test_validates_through_the_rule_models(self, rule):
        m = PostureSubRuleCreate(
            name=rule["name"],
            description=rule["description"],
            target_filters=rule["target_filters"],
            compliance_criteria=rule["compliance_criteria"],
        )
        assert m.name == rule["name"]
        assert len(m.target_filters) == len(rule["target_filters"])
        assert m.compliance_criteria.logic == rule["compliance_criteria"]["logic"]
        assert len(m.compliance_criteria.conditions) == len(rule["compliance_criteria"]["conditions"])

    @pytest.mark.parametrize("rule", OOTB_SEED_SUB_RULES, ids=_rule_ids())
    def test_operators_serialise_back_to_the_seeded_codes(self, rule):
        """Seeds store canonical registry codes (migration 109's invariant):
        dumping must return the same spelling, not a spaced display label."""
        m = PostureSubRuleCreate(
            name=rule["name"],
            target_filters=rule["target_filters"],
            compliance_criteria=rule["compliance_criteria"],
        )
        dumped = m.model_dump()
        for seeded, model in zip(rule["target_filters"], dumped["target_filters"]):
            assert model["operator"] == seeded["operator"]
            assert model["field_id"] == seeded["field_id"]
        for seeded, model in zip(
            rule["compliance_criteria"]["conditions"],
            dumped["compliance_criteria"]["conditions"],
        ):
            assert model["operator"] == seeded["operator"]
            assert model["field_id"] == seeded["field_id"]

    @pytest.mark.parametrize("rule", OOTB_SEED_SUB_RULES, ids=_rule_ids())
    def test_every_operator_is_legal_for_its_field(self, rule):
        for cond in _all_conditions(rule):
            op = resolve_operator(cond["operator"])  # raises on unknown spelling
            fid = cond["field_id"]
            if fid["kind"] == _VIRTUAL:
                allowed = VIRTUAL_FIELDS[fid["name"]]["operators"]
                assert op.code in allowed, (
                    f"{rule['name']}: operator {op.code!r} not allowed for "
                    f"virtual field {fid['name']!r} (allows {allowed})"
                )
            else:
                assert fid["kind"] == _STATIC
                data_type = FIELD_DATA_TYPES[fid["name"]]  # KeyError on unknown field
                allowed = [o.code for o in operators_for(data_type, is_array=False)]
                assert op.code in allowed, (
                    f"{rule['name']}: operator {op.code!r} not allowed for "
                    f"{fid['name']!r} ({data_type}); allows {allowed}"
                )


class TestOotbSeedOperatorBuildingBlocks:
    def test_confidence_level_allows_any(self):
        """The medium,high encoding relies on `any` — and on no numeric
        threshold ever being hardcoded (the bands are operator-configurable)."""
        assert "any" in VIRTUAL_FIELDS["confidence_level"]["operators"]

    def test_within_last_is_valid_for_last_seen(self):
        assert FIELD_DATA_TYPES["Last Seen"] == FieldDataType.TIMESTAMP
        assert "within_last" in [
            o.code for o in operators_for(FieldDataType.TIMESTAMP, is_array=False)
        ]

    def test_contains_is_valid_for_data_source_category(self):
        assert "contains" in [
            o.code
            for o in operators_for(FIELD_DATA_TYPES["Data Source Category"], is_array=True)
        ]


class TestOotbApplicableCategories:
    @pytest.mark.parametrize(
        "category_id,values", sorted(OOTB_APPLICABLE_CATEGORIES.items())
    )
    def test_validate_through_category_update_request(self, category_id, values):
        m = PostureCategoryUpdateRequest(applicable_categories=values)
        assert m.applicable_categories == values
