# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for Query Engine Service

Tests all functionality of the query engine including:
- Standard field queries (strings, numbers, dates)
- Array field queries (hostname[], ip[], mac[])
- JSONB field queries (extended_attributes)
- All operators (equals, contains, greater than, etc.)
- Compliance evaluation
"""

import pytest
from unittest.mock import MagicMock
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.tag_rules.services.query_engine_service import QueryEngineService


def _make_resolver(tag_names=None, ext_attrs=None):
    """Build a FieldResolver backed by a mocked DB.

    Args:
        tag_names: list of tag name strings (e.g. ["Windows-Server", "AV-Protected"])
        ext_attrs: list of (source_name, field_name) tuples for extended attributes
    """
    db = MagicMock()

    # The catalog calls execute_query three times:
    #   1. extended_attributes query
    #   2. tag_name query
    #   3. posture_categories query
    # We wire side_effect so each call returns the right rows.
    tag_rows = [{"tag_name": t} for t in (tag_names or [])]
    ext_rows = [{"source_name": s, "field_name": f} for s, f in (ext_attrs or [])]

    def _execute_query(sql, params):
        sql_stripped = sql.strip()
        if "jsonb_each" in sql_stripped or "extended_attributes" in sql_stripped:
            return ext_rows
        if "asset_tags_v2" in sql_stripped or "tag_name" in sql_stripped:
            return tag_rows
        if "posture_categories" in sql_stripped:
            return []
        return []

    db.execute_query.side_effect = _execute_query
    catalog = FieldCatalog(db_adapter=db)
    return FieldResolver(catalog=catalog)


def _make_engine(tag_names=None, ext_attrs=None):
    """Return a QueryEngineService wired to a resolver with the given catalog."""
    return QueryEngineService(resolver=_make_resolver(tag_names=tag_names, ext_attrs=ext_attrs))


class TestQueryEngineBasic:
    """Test basic query building functionality"""

    def setup_method(self):
        """Setup for each test"""
        self.engine = _make_engine()

    def test_single_filter_equals(self):
        """Test single filter with equals operator (exact, case-insensitive)"""
        filters = [
            {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows Server 2019"}
        ]

        where_clause, params = self.engine.build_where_clause(filters)

        # R4 (§6.1): scalar eq is now EXACT LOWER()=LOWER(), not ILIKE.
        assert "LOWER(os_normalized) = LOWER(%(param_0_0)s)" in where_clause
        assert params["param_0_0"] == "Windows Server 2019"

    def test_single_filter_contains(self):
        """Test single filter with contains operator (still ILIKE — unchanged by R4)"""
        filters = [
            {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"}
        ]

        where_clause, params = self.engine.build_where_clause(filters)

        assert "os_normalized ILIKE %(param_0_0)s" in where_clause
        assert params["param_0_0"] == "%Windows%"

    def test_multiple_filters_and(self):
        """Test multiple filters with AND logic"""
        filters = [
            {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"},
            {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}
        ]

        where_clause, params = self.engine.build_where_clause(filters, logic="AND")

        assert "os_normalized ILIKE %(param_0_0)s" in where_clause
        # R4 (§6.1): scalar eq is now EXACT LOWER()=LOWER().
        assert "LOWER(category) = LOWER(%(param_1_0)s)" in where_clause
        assert " AND " in where_clause
        assert len(params) == 2

    def test_multiple_filters_or(self):
        """Test multiple filters with OR logic"""
        filters = [
            {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"},
            {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Linux"}
        ]

        where_clause, params = self.engine.build_where_clause(filters, logic="OR")

        # R4 (§6.1): scalar eq is now EXACT LOWER()=LOWER().
        assert "LOWER(os_normalized) = LOWER(%(param_0_0)s)" in where_clause
        assert "LOWER(os_normalized) = LOWER(%(param_1_0)s)" in where_clause
        assert " OR " in where_clause
        assert len(params) == 2

    def test_empty_filters(self):
        """Test with empty filter list"""
        filters = []

        where_clause, params = self.engine.build_where_clause(filters)

        assert where_clause == ""
        assert params == {}


class TestQueryEngineOperators:
    """Test all supported operators"""

    def setup_method(self):
        """Setup for each test"""
        self.engine = _make_engine()

    def test_operator_equals(self):
        """Test equals operator on an array field (exact, case-insensitive).

        Location is a STRING_ARRAY field, so eq is array-aware EXISTS/unnest and,
        per R4 (§6.1), EXACT LOWER(x)=LOWER(v) — not ILIKE.
        """
        filters = [{"field_id": {"kind": "static", "name": "Location"}, "operator": "equals", "value": "NYC"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "EXISTS" in where_clause
        assert "unnest(location)" in where_clause
        assert "LOWER(x) = LOWER(%(param_0_0)s)" in where_clause
        assert "ILIKE" not in where_clause
        assert params["param_0_0"] == "NYC"

    def test_operator_not_equals(self):
        """Test not equals operator on an array field (exact, case-insensitive).

        Location is STRING_ARRAY → NOT EXISTS/unnest with EXACT LOWER()=LOWER()
        (R4 §6.1), not NOT ILIKE.
        """
        filters = [{"field_id": {"kind": "static", "name": "Location"}, "operator": "not equals", "value": "NYC"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "NOT EXISTS" in where_clause
        assert "unnest(location)" in where_clause
        assert "LOWER(x) = LOWER(%(param_0_0)s)" in where_clause
        assert "ILIKE" not in where_clause

    def test_operator_contains(self):
        """Test contains operator on an array field (still ILIKE — unchanged by R4).

        Owner is STRING_ARRAY → array-aware EXISTS/unnest with ILIKE.
        """
        filters = [{"field_id": {"kind": "static", "name": "Owner"}, "operator": "contains", "value": "admin"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "EXISTS" in where_clause
        assert "unnest(owner)" in where_clause
        assert "ILIKE %(param_0_0)s" in where_clause
        assert params["param_0_0"] == "%admin%"

    def test_operator_not_contains(self):
        """Test not contains operator on an array field (still ILIKE — unchanged by R4)."""
        filters = [{"field_id": {"kind": "static", "name": "Owner"}, "operator": "not contains", "value": "test"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "NOT EXISTS" in where_clause
        assert "unnest(owner)" in where_clause
        assert "ILIKE %(param_0_0)s" in where_clause
        assert params["param_0_0"] == "%test%"

    def test_operator_starts_with(self):
        """Test starts with operator on an array field (still ILIKE — unchanged by R4)."""
        filters = [{"field_id": {"kind": "static", "name": "Owner"}, "operator": "starts with", "value": "admin"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "EXISTS" in where_clause
        assert "unnest(owner)" in where_clause
        assert "ILIKE %(param_0_0)s" in where_clause
        assert params["param_0_0"] == "admin%"

    def test_operator_ends_with(self):
        """Test ends with operator on an array field (still ILIKE — unchanged by R4)."""
        filters = [{"field_id": {"kind": "static", "name": "Owner"}, "operator": "ends with", "value": "@company.com"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "EXISTS" in where_clause
        assert "unnest(owner)" in where_clause
        assert "ILIKE %(param_0_0)s" in where_clause
        assert params["param_0_0"] == "%@company.com"

    def test_operator_greater_than(self):
        """Test greater than operator (clause unchanged; value bound as string).

        The registry binds the raw value (passed through the relative-duration
        resolver, which leaves plain numbers untouched) — no float coercion.
        """
        filters = [{"field_id": {"kind": "static", "name": "Confidence Score"}, "operator": "greater than", "value": "0.8"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "confidence > %(param_0_0)s" in where_clause
        assert params["param_0_0"] == "0.8"

    def test_operator_less_than(self):
        """Test less than operator (clause unchanged; value bound as string)."""
        filters = [{"field_id": {"kind": "static", "name": "Confidence Score"}, "operator": "less than", "value": "0.5"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "confidence < %(param_0_0)s" in where_clause
        assert params["param_0_0"] == "0.5"

    def test_operator_is_empty(self):
        """Test is empty operator on an array field (NULL or empty array)."""
        filters = [{"field_id": {"kind": "static", "name": "Owner"}, "operator": "is empty", "value": ""}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "owner IS NULL" in where_clause
        assert len(params) == 0  # No parameter for IS NULL

    def test_operator_is_not_null(self):
        """Test is not empty operator on an array field."""
        filters = [{"field_id": {"kind": "static", "name": "Owner"}, "operator": "is not empty", "value": ""}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "owner IS NOT NULL" in where_clause
        assert len(params) == 0


class TestQueryEngineArrayFields:
    """Test array field queries"""

    def setup_method(self):
        """Setup for each test"""
        self.engine = _make_engine()

    def test_array_field_in_array(self):
        """Test 'in array' operator for array fields.

        'in array' resolves to eq (§9), which on a STRING_ARRAY field is exact
        case-insensitive membership: EXISTS/unnest with LOWER(x)=LOWER(v) (R4 §6.1).
        """
        filters = [{"field_id": {"kind": "static", "name": "Hostname"}, "operator": "in array", "value": "SERVER-01"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "EXISTS" in where_clause
        assert "unnest(hostname)" in where_clause
        assert "LOWER(x) = LOWER(" in where_clause
        assert params["param_0_0"] == "SERVER-01"

    def test_array_field_not_in_array(self):
        """Test 'not in array' operator for array fields.

        'not in array' resolves to neq (§9) → NOT EXISTS/unnest with exact
        LOWER(x)=LOWER(v) (R4 §6.1).
        """
        filters = [{"field_id": {"kind": "static", "name": "IP Address"}, "operator": "not in array", "value": "192.168.1.1"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "NOT EXISTS" in where_clause
        assert "unnest(ip)" in where_clause
        assert "LOWER(x) = LOWER(" in where_clause
        assert params["param_0_0"] == "192.168.1.1"

    def test_array_field_contains(self):
        """Test 'contains' operator for array fields (still ILIKE — unchanged by R4)"""
        filters = [{"field_id": {"kind": "static", "name": "Hostname"}, "operator": "contains", "value": "SERVER"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "EXISTS" in where_clause
        assert "unnest(hostname)" in where_clause
        assert "ILIKE" in where_clause
        assert params["param_0_0"] == "%SERVER%"

    def test_array_field_multiple_values(self):
        """Test multiple array field conditions"""
        filters = [
            {"field_id": {"kind": "static", "name": "Hostname"}, "operator": "in array", "value": "SERVER-01"},
            {"field_id": {"kind": "static", "name": "IP Address"}, "operator": "in array", "value": "10.0.0.1"}
        ]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "unnest(hostname)" in where_clause
        assert "unnest(ip)" in where_clause
        assert len(params) == 2


class TestQueryEngineJSONBFields:
    """Test JSONB field queries"""

    def setup_method(self):
        """Setup for each test"""
        self.engine = _make_engine(ext_attrs=[("Intune Full", "deviceType_s")])

    def test_jsonb_field_equals(self):
        """Test equals operator for JSONB extended attribute fields (exact, case-insensitive).

        deviceType_s is a jsonb_path scalar STRING (not jsonb_array) → eq is the
        scalar exact LOWER()=LOWER() (R4 §6.1), not ILIKE.
        """
        filters = [{"field_id": {"kind": "extended_attribute", "source": "Intune Full", "attribute": "deviceType_s"}, "operator": "equals", "value": "Desktop"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "LOWER(extended_attributes->'Intune Full'->>'deviceType_s') = LOWER(%(param_0_0)s)" in where_clause
        assert params["param_0_0"] == "Desktop"

    def test_jsonb_field_contains(self):
        """Test contains operator for JSONB extended attribute fields (still ILIKE — unchanged by R4)"""
        filters = [{"field_id": {"kind": "extended_attribute", "source": "Intune Full", "attribute": "deviceType_s"}, "operator": "contains", "value": "Desk"}]
        where_clause, params = self.engine.build_where_clause(filters)

        assert "extended_attributes->'Intune Full'->>'deviceType_s' ILIKE %(param_0_0)s" in where_clause
        assert params["param_0_0"] == "%Desk%"


class TestQueryEngineCompleteQueries:
    """Test complete query building"""

    def setup_method(self):
        """Setup for each test"""
        self.engine = _make_engine()

    def test_build_count_query(self):
        """Test building COUNT query"""
        filters = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"}]

        query, params = self.engine.build_count_query(filters)

        assert query.startswith("SELECT COUNT(*) FROM assets")
        assert "WHERE" in query
        assert "os_normalized ILIKE %(param_0_0)s" in query
        assert len(params) == 1

    def test_build_asset_query_simple(self):
        """Test building simple SELECT query"""
        filters = [{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}]

        query, params = self.engine.build_asset_query(filters)

        assert query.startswith("SELECT * FROM assets")
        # R4 (§6.1): scalar eq is now EXACT LOWER()=LOWER().
        assert "WHERE LOWER(category) = LOWER(%(param_0_0)s)" in query

    def test_build_asset_query_with_columns(self):
        """Test building SELECT query with specific columns"""
        filters = [{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}]
        columns = ["id", "hostname", "os_normalized"]

        query, params = self.engine.build_asset_query(filters, select_columns=columns)

        assert "SELECT id, hostname, os_normalized FROM assets" in query

    def test_build_asset_query_with_limit(self):
        """Test building SELECT query with LIMIT"""
        filters = [{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}]

        query, params = self.engine.build_asset_query(filters, limit=100)

        assert "LIMIT 100" in query

    def test_build_asset_query_with_pagination(self):
        """Test building SELECT query with LIMIT and OFFSET"""
        filters = [{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}]

        query, params = self.engine.build_asset_query(filters, limit=50, offset=100)

        assert "LIMIT 50" in query
        assert "OFFSET 100" in query


class TestQueryEngineCompliance:
    """Test compliance evaluation"""

    def setup_method(self):
        """Setup for each test"""
        self.engine = _make_engine()

    def test_compliance_all_logic_pass(self):
        """Test compliance with ALL logic - all conditions pass"""
        asset_rows = [
            {
                "id": "uuid1",
                "os_normalized": "Windows Server 2022",
                "category": "server",
            }
        ]

        compliance_criteria = {
            "logic": "all",
            "conditions": [
                {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"},
                {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}
            ]
        }

        results = self.engine.evaluate_compliance(asset_rows, compliance_criteria)

        assert results["uuid1"] == "pass"

    def test_compliance_all_logic_fail(self):
        """Test compliance with ALL logic - one condition fails"""
        asset_rows = [
            {
                "id": "uuid1",
                "os_normalized": "Ubuntu 22.04",
                "category": "server",
            }
        ]

        compliance_criteria = {
            "logic": "all",
            "conditions": [
                {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"},
                {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}
            ]
        }

        results = self.engine.evaluate_compliance(asset_rows, compliance_criteria)

        assert results["uuid1"] == "fail"

    def test_compliance_any_logic_pass(self):
        """Test compliance with ANY logic - at least one condition passes"""
        asset_rows = [
            {
                "id": "uuid1",
                "os_normalized": "Ubuntu 22.04",
                "category": "server",
            }
        ]

        compliance_criteria = {
            "logic": "any",
            "conditions": [
                {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"},
                {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}
            ]
        }

        results = self.engine.evaluate_compliance(asset_rows, compliance_criteria)

        assert results["uuid1"] == "pass"  # Category condition passes

    def test_compliance_any_logic_fail(self):
        """Test compliance with ANY logic - no conditions pass"""
        asset_rows = [
            {
                "id": "uuid1",
                "os_normalized": "Ubuntu 22.04",
                "category": "workstation",
            }
        ]

        compliance_criteria = {
            "logic": "any",
            "conditions": [
                {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"},
                {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}
            ]
        }

        results = self.engine.evaluate_compliance(asset_rows, compliance_criteria)

        assert results["uuid1"] == "fail"

    def test_compliance_multiple_assets(self):
        """Test compliance with multiple assets"""
        asset_rows = [
            {"id": "uuid1", "os_normalized": "Windows 11"},
            {"id": "uuid2", "os_normalized": "Ubuntu 22.04"},
            {"id": "uuid3", "os_normalized": "macOS 14"},
        ]

        compliance_criteria = {
            "logic": "any",
            "conditions": [
                {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains", "value": "Windows"}
            ]
        }

        results = self.engine.evaluate_compliance(asset_rows, compliance_criteria)

        assert results["uuid1"] == "pass"
        assert results["uuid2"] == "fail"
        assert results["uuid3"] == "fail"

    def test_compliance_no_conditions(self):
        """Test compliance with no conditions - all assets should pass"""
        asset_rows = [
            {"id": "uuid1", "extended_attributes": {}},
            {"id": "uuid2", "extended_attributes": {}}
        ]

        compliance_criteria = {
            "logic": "all",
            "conditions": []
        }

        results = self.engine.evaluate_compliance(asset_rows, compliance_criteria)

        assert results["uuid1"] == "pass"
        assert results["uuid2"] == "pass"


class TestQueryEngineEdgeCases:
    """Test edge cases and error handling"""

    def setup_method(self):
        """Setup for each test"""
        self.engine = _make_engine()

    def test_invalid_filter_missing_field(self):
        """Test handling of invalid filter (missing field_id)"""
        filters = [
            {"operator": "equals", "value": "test"}  # Missing field_id
        ]

        where_clause, params = self.engine.build_where_clause(filters)

        # Should skip invalid filter
        assert where_clause == ""
        assert params == {}

    def test_invalid_filter_missing_operator(self):
        """Test handling of invalid filter (missing operator)"""
        filters = [
            {"field_id": {"kind": "static", "name": "OS Normalized"}, "value": "Windows"}  # Missing operator
        ]

        where_clause, params = self.engine.build_where_clause(filters)

        # Should skip invalid filter
        assert where_clause == ""
        assert params == {}

    def test_mixed_valid_invalid_filters(self):
        """Test handling of mix of valid and invalid filters"""
        filters = [
            {"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"},
            {"operator": "equals", "value": "test"},  # Invalid — no field_id
            {"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}
        ]

        where_clause, params = self.engine.build_where_clause(filters)

        # Should process only valid filters. R4 (§6.1): scalar eq is EXACT now.
        assert "LOWER(os_normalized) = LOWER(%(param_0_0)s)" in where_clause
        assert "LOWER(category) = LOWER(%(param_1_0)s)" in where_clause
        assert len(params) == 2

    def test_parameter_name_uniqueness(self):
        """Test that parameter names are unique across multiple calls"""
        filters1 = [{"field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "equals", "value": "Windows"}]
        filters2 = [{"field_id": {"kind": "static", "name": "Category"}, "operator": "equals", "value": "server"}]

        _, params1 = self.engine.build_where_clause(filters1)
        _, params2 = self.engine.build_where_clause(filters2)

        # Each call should start with param_0 (registry suffixes _0 off param_N).
        assert "param_0_0" in params1
        assert "param_0_0" in params2

    def test_special_characters_in_value(self):
        """Test handling of special characters in filter values (array field, exact eq).

        Owner is STRING_ARRAY → array-aware exact eq (R4 §6.1); the value is
        parameterised verbatim regardless of special characters.
        """
        filters = [{"field_id": {"kind": "static", "name": "Owner"}, "operator": "equals", "value": "user'@company.com"}]

        where_clause, params = self.engine.build_where_clause(filters)

        assert "unnest(owner)" in where_clause
        assert "LOWER(x) = LOWER(%(param_0_0)s)" in where_clause
        assert params["param_0_0"] == "user'@company.com"

    def test_unknown_operator_raises_loud(self):
        """§6.2: an unmapped operator must RAISE out of build_where_clause.

        Before the registry re-point the compiler silently caught the error and
        dropped the condition, which would silently widen the matched set. The
        loud failure makes an unmapped operator a hard error (surfaces in logs/4xx)
        rather than a quietly-broken rule. (This is an intentional behavior change.)
        """
        from src.modules.fields import OperatorResolutionError

        filters = [{"field_id": {"kind": "static", "name": "OS Normalized"},
                    "operator": "frobnicate", "value": "x"}]

        with pytest.raises(OperatorResolutionError):
            self.engine.build_where_clause(filters)

    def test_unresolvable_field_is_tolerated(self):
        """An unresolvable FIELD is still skipped (continue), not raised.

        Only the OPERATOR resolution became loud (§6.2); the pre-existing
        tolerance for non-live FieldIds is preserved.
        """
        filters = [{"field_id": {"kind": "static", "name": "NoSuchFieldZZZ"},
                    "operator": "equals", "value": "x"}]

        where_clause, params = self.engine.build_where_clause(filters)

        assert where_clause == ""
        assert params == {}

    def test_scalar_is_empty_matches_null_or_blank(self):
        """§6.1: scalar empty is now NULL OR '' (was NULL-only).

        A scalar STRING field's `is empty` must match both SQL NULL and the
        empty string; a stored `''` now counts as empty.
        """
        filters = [{"field_id": {"kind": "static", "name": "OS Normalized"},
                    "operator": "is empty", "value": ""}]

        where_clause, params = self.engine.build_where_clause(filters)

        assert "os_normalized IS NULL" in where_clause
        assert "os_normalized = ''" in where_clause
        assert params == {}


class TestQueryEngineInMemorySuperset:
    """§6.1 R4: in-memory match gained any-of/gte/lte (scalar) + starts/ends/gt/lt
    (array), which previously silently returned False."""

    def setup_method(self):
        self.engine = _make_engine()

    def test_scalar_gte_now_matches(self):
        rows = [{"id": "u1", "confidence": "0.9"}]
        criteria = {"logic": "all", "conditions": [
            {"field_id": {"kind": "static", "name": "Confidence Score"},
             "operator": "greater than or equal", "value": "0.8"}]}
        assert self.engine.evaluate_compliance(rows, criteria)["u1"] == "pass"

    def test_scalar_any_of_now_matches(self):
        rows = [{"id": "u1", "os_normalized": "Ubuntu 22.04"}]
        criteria = {"logic": "all", "conditions": [
            {"field_id": {"kind": "static", "name": "OS Normalized"},
             "operator": "any of", "value": ["Ubuntu 22.04", "Windows 11"]}]}
        assert self.engine.evaluate_compliance(rows, criteria)["u1"] == "pass"

    def test_array_starts_with_now_matches(self):
        rows = [{"id": "u1", "hostname": ["server-01", "db-02"]}]
        criteria = {"logic": "all", "conditions": [
            {"field_id": {"kind": "static", "name": "Hostname"},
             "operator": "starts with", "value": "server"}]}
        assert self.engine.evaluate_compliance(rows, criteria)["u1"] == "pass"

    def test_scalar_empty_blank_string(self):
        rows = [{"id": "u1", "owner": ""}]
        criteria = {"logic": "all", "conditions": [
            {"field_id": {"kind": "static", "name": "Owner"},
             "operator": "is empty", "value": ""}]}
        assert self.engine.evaluate_compliance(rows, criteria)["u1"] == "pass"


# =============================================================================
# Run tests
# =============================================================================

if __name__ == "__main__":
    # Run with: python -m pytest tag_rules/tests/test_query_engine.py -v
    pytest.main([__file__, "-v", "--tb=short"])
