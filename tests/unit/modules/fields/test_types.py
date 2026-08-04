# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for src/modules/fields/types.py."""

from src.modules.fields.types import FieldDataType, FilterOperator


class TestFieldDataType:
    def test_string_enum_values(self):
        assert FieldDataType.STRING.value == "string"
        assert FieldDataType.STRING_ARRAY.value == "string_array"
        assert FieldDataType.JSONB_ARRAY.value == "jsonb_array"

    def test_is_str_enum(self):
        # Must be a str-enum so Pydantic validators serialize cleanly
        assert FieldDataType.STRING == "string"


class TestFilterOperator:
    def test_operator_values(self):
        assert FilterOperator.EQUALS.value == "equals"
        assert FilterOperator.IS_NULL.value == "is empty"  # UI-friendly
        assert FilterOperator.IN.value == "any of"

    def test_all_operators_present(self):
        # The 13 legacy operators, the four Phase-2 registry operators exposed by
        # operator_specs (all/none array set-ops, is_true/is_false), plus the
        # Phase-3 scalar helpers `between` and `within_last`.
        expected = {
            "equals", "not equals", "contains", "not contains",
            "starts with", "ends with", "greater than", "less than",
            "greater than or equal", "less than or equal",
            "any of", "is empty", "is not empty",
            "all of", "none of", "is true", "is false",
            "between", "in the last",
        }
        actual = {op.value for op in FilterOperator}
        assert actual == expected

    def test_accepts_array_and_boolean_codes(self):
        # Phase 2 surfaces all/none (array set-ops) and is_true/is_false in
        # operator_specs, so the write-path enum must accept those codes or the
        # tag/posture rule wizards 422 on save.
        assert FilterOperator("all") is FilterOperator.ALL_OF
        assert FilterOperator("none") is FilterOperator.NONE_OF
        assert FilterOperator("is_true") is FilterOperator.IS_TRUE
        assert FilterOperator("is_false") is FilterOperator.IS_FALSE
        # spaced display labels and underscored forms also resolve
        assert FilterOperator("all of") is FilterOperator.ALL_OF
        assert FilterOperator("none of") is FilterOperator.NONE_OF
        assert FilterOperator("is true") is FilterOperator.IS_TRUE

    def test_every_registry_code_resolves_through_filter_operator(self):
        # Drift guard: every canonical code the registry (and therefore
        # operator_specs / FilterRow) can emit MUST resolve through the
        # write-path enum without ValueError. Fails the moment the two tables
        # diverge again.
        from src.modules.fields.operators import OPERATOR_REGISTRY
        for code in OPERATOR_REGISTRY:
            FilterOperator(code)  # must not raise

    def test_accepts_underscored_form_from_frontend(self):
        # The frontend stores operators with underscores ("is_empty",
        # "not_equals") for internal state; payloads should still validate.
        # The canonical .value stays the spaced display form.
        assert FilterOperator("is_empty") is FilterOperator.IS_NULL
        assert FilterOperator("is_not_empty") is FilterOperator.IS_NOT_NULL
        assert FilterOperator("not_equals") is FilterOperator.NOT_EQUALS
        assert FilterOperator("starts_with") is FilterOperator.STARTS_WITH
        assert FilterOperator("greater_than_or_equal") is FilterOperator.GREATER_THAN_OR_EQUAL
        # Spaced form still works
        assert FilterOperator("is empty") is FilterOperator.IS_NULL
        # Serialized value stays canonical
        assert FilterOperator("is_empty").value == "is empty"

    def test_rejects_unknown_operator(self):
        import pytest
        with pytest.raises(ValueError):
            FilterOperator("totally_made_up")


class TestFilterConditionAcceptsArrayCodes:
    """The tag/posture write boundary is FilterCondition.operator: FilterOperator
    (shared by both rule models). The 'has all of'/'has none of' codes FilterRow
    now emits must validate through the MODEL (not just the bare enum), or the
    wizard save 422s. This is the regression-prone coercion surface."""

    def test_filter_condition_accepts_all_none_codes(self):
        from src.modules.tag_rules.models.rule import FilterCondition, ComplianceCriteria

        target = FilterCondition(
            field_id={"kind": "static", "name": "Data Source"}, operator="none", value="jamf,sccm"
        )
        assert target.operator is FilterOperator.NONE_OF

        criteria = ComplianceCriteria(
            logic="any",
            conditions=[
                {"field_id": {"kind": "static", "name": "Data Source"}, "operator": "all", "value": "edr,mdm"},
            ],
        )
        assert criteria.conditions[0].operator is FilterOperator.ALL_OF
        assert criteria.logic == "any"  # combinator preserved, not confused with the 'any' op code

    def test_filter_condition_serializes_operator_as_canonical_code(self):
        """Phase 3 write-path: a saved/edited rule persists the canonical
        registry CODE ('neq'), not the spaced display label ('not equals'),
        completing what migration 109 started — without this, editing a rule
        re-introduces labels and undoes the migration."""
        from src.modules.tag_rules.models.rule import FilterCondition

        # Built from a spaced label -> serialises to the code.
        fc = FilterCondition(
            field_id={"kind": "static", "name": "Category"},
            operator="not equals", value="x")
        assert fc.model_dump()["operator"] == "neq"

        # Built from a code -> idempotent.
        fc2 = FilterCondition(
            field_id={"kind": "static", "name": "Category"},
            operator="neq", value="x")
        assert fc2.model_dump()["operator"] == "neq"

        # Phase-3 op serialises to its code (not the 'in the last' label).
        fc3 = FilterCondition(
            field_id={"kind": "static", "name": "Last Seen"},
            operator="within_last", value="30d")
        assert fc3.model_dump()["operator"] == "within_last"
