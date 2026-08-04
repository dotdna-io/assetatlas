# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from unittest.mock import MagicMock
from src.modules.fields import FieldCatalog, FieldResolver
from src.modules.tag_rules.services.query_engine_service import QueryEngineService
from src.modules.fields.models import CoreEnrichmentField


def _make_resolver():
    """Resolver with static + core_enrichment entries only (no DB hits needed)."""
    db = MagicMock()
    db.execute_query.return_value = []
    return FieldResolver(catalog=FieldCatalog(db_adapter=db))


@pytest.fixture
def qe():
    return QueryEngineService(resolver=_make_resolver())


# Private IP resolves to a JSONB_ARRAY field (sql_kind=jsonb_path), so going
# through the public _build_single_condition delegate exercises the registry's
# jsonb_array routing. _build_single_condition resolves the field itself and
# delegates to the operator registry (the old _build_jsonb_array_condition was
# deleted in favour of the registry's build_sql).
_PRIVATE_IP = CoreEnrichmentField(kind="core_enrichment", name="Private IP")


class TestJsonbArrayConditionViaRegistry:
    """JSONB_ARRAY operators route through _build_single_condition → registry.

    Tests use the real param_counter pattern: mutable list [int].
    """

    def test_equals_operator(self, qe):
        counter = [0]
        sql, params = qe._build_single_condition(_PRIVATE_IP, "equals", "10.0.1.5", counter)
        assert "jsonb_array_elements_text" in sql
        # R4 (§6.1): eq is now EXACT case-insensitive, not ILIKE.
        assert "LOWER(x.value) = LOWER(" in sql
        assert counter[0] > 0

    def test_contains_operator(self, qe):
        counter = [0]
        sql, params = qe._build_single_condition(_PRIVATE_IP, "contains", "10.0", counter)
        assert "jsonb_array_elements_text" in sql
        assert "ILIKE" in sql  # contains stays ILIKE (unchanged by R4)

    def test_starts_with_operator(self, qe):
        counter = [0]
        sql, params = qe._build_single_condition(_PRIVATE_IP, "starts with", "192.168", counter)
        assert "jsonb_array_elements_text" in sql
        assert "ILIKE" in sql

    def test_is_empty_operator(self, qe):
        counter = [0]
        sql, params = qe._build_single_condition(_PRIVATE_IP, "is empty", "", counter)
        assert "IS NULL" in sql or "jsonb_array_length" in sql

    def test_is_not_empty_operator(self, qe):
        counter = [0]
        sql, params = qe._build_single_condition(_PRIVATE_IP, "is not empty", "", counter)
        assert "IS NOT NULL" in sql

    def test_any_of_operator(self, qe):
        counter = [0]
        sql, params = qe._build_single_condition(
            _PRIVATE_IP, "any of", "10.0.1.5, 192.168.1.1", counter
        )
        assert "jsonb_array_elements_text" in sql
        assert len(params) == 2


class TestBuildSingleConditionDispatch:
    """Verify JSONB_ARRAY fields get routed correctly via typed FieldId."""

    def test_private_ip_dispatches_to_jsonb_array(self, qe):
        """CoreEnrichmentField(Private IP) resolves to JSONB_ARRAY → jsonb_array_elements_text."""
        counter = [0]
        field_id = CoreEnrichmentField(kind="core_enrichment", name="Private IP")
        sql, params = qe._build_single_condition(field_id, "contains", "10.0", counter)
        assert "jsonb_array_elements_text" in sql

    def test_ip_type_dispatches_to_jsonb_scalar(self, qe):
        """CoreEnrichmentField(IP Type) resolves to STRING (jsonb_path) → scalar clause."""
        counter = [0]
        field_id = CoreEnrichmentField(kind="core_enrichment", name="IP Type")
        sql, params = qe._build_single_condition(field_id, "equals", "mixed", counter)
        assert "->>'ip_type'" in sql
