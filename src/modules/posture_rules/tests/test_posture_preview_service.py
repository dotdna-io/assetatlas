# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock
import pytest

from src.modules.fields.models import StaticField
from src.modules.posture_rules.services.posture_preview_service import PosturePreviewService

_DOMAIN_FIELD = StaticField(kind="static", name="Domain")
_HOSTNAME_FIELD = StaticField(kind="static", name="Hostname")


@pytest.fixture
def engine():
    return MagicMock()


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def repo():
    return MagicMock()


@pytest.fixture
def resolver():
    return MagicMock()


@pytest.fixture
def service(repo, engine, db, resolver):
    return PosturePreviewService(
        repository=repo, query_engine=engine, db_adapter=db, resolver=resolver,
    )


def test_preview_counts_matched_pass_fail(service, engine):
    # Two calls: first returns target-matching set, second returns criteria-passing set.
    engine.asset_ids_matching.side_effect = [
        {"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01",
         "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa02",
         "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa03"},  # target match (3)
        {"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"},   # criteria pass (1)
    ]
    out = service.preview(
        target_filters=[{"field": "Category", "operator": "equals", "value": "server"}],
        compliance_criteria={"logic": "any", "conditions": [{"field": "x", "operator": "equals", "value": "y"}]},
    )
    assert out == {"matched": 3, "pass": 1, "fail": 2}


def test_preview_zero_target_match(service, engine):
    engine.asset_ids_matching.side_effect = [set(), set()]
    out = service.preview(
        target_filters=[{"field": "Category", "operator": "equals", "value": "server"}],
        compliance_criteria={"logic": "any", "conditions": [{"field": "x", "operator": "equals", "value": "y"}]},
    )
    assert out == {"matched": 0, "pass": 0, "fail": 0}


def test_preview_logic_mapping_all_to_AND(service, engine):
    """Verify 'all' logic maps to 'AND' in engine call."""
    engine.asset_ids_matching.side_effect = [{"a"}, {"a"}]
    service.preview(
        target_filters=[{"field": "Category", "operator": "equals", "value": "server"}],
        compliance_criteria={"logic": "all", "conditions": [{"field": "x", "operator": "equals", "value": "y"}]},
    )
    # Second call is the criteria call — verify it used AND
    second_call_kwargs = engine.asset_ids_matching.call_args_list[1].kwargs
    assert second_call_kwargs.get("logic") == "AND"


def test_preview_logic_mapping_any_to_OR(service, engine):
    engine.asset_ids_matching.side_effect = [{"a"}, {"a"}]
    service.preview(
        target_filters=[{"field": "Category", "operator": "equals", "value": "server"}],
        compliance_criteria={"logic": "any", "conditions": [{"field": "x", "operator": "equals", "value": "y"}]},
    )
    second_call_kwargs = engine.asset_ids_matching.call_args_list[1].kwargs
    assert second_call_kwargs.get("logic") == "OR"


class TestPreviewOutput:
    A1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"
    A2 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa02"

    def test_returns_zero_when_no_target_match(self, service, engine):
        engine.asset_ids_matching.return_value = set()
        result = service.preview_output(
            target_filters=[{"field": "Category", "operator": "equals", "value": "server"}],
            compliance_criteria={"logic": "any", "conditions": []},
            output_mode="field",
            output_field_id=_HOSTNAME_FIELD,
        )
        assert result == {"matched": 0, "passed": 0, "samples": []}

    def test_returns_zero_passed_when_criteria_fails_all(self, service, engine):
        engine.asset_ids_matching.side_effect = [{self.A1, self.A2}, set()]
        result = service.preview_output(
            target_filters=[{"field": "Category", "operator": "equals", "value": "server"}],
            compliance_criteria={"logic": "any", "conditions": [
                {"field": "Domain", "operator": "equals", "value": "x"},
            ]},
            output_mode="field",
            output_field_id=_HOSTNAME_FIELD,
        )
        assert result["matched"] == 2
        assert result["passed"] == 0
        assert result["samples"] == []

    def test_text_mode_returns_literal(self, service, engine, db, repo):
        engine.asset_ids_matching.side_effect = [{self.A1}, {self.A1}]
        db.execute_query.return_value = [{"id": self.A1, "hostname": "host-a"}]
        result = service.preview_output(
            target_filters=[{"field": "Category", "operator": "equals", "value": "server"}],
            compliance_criteria={"logic": "any", "conditions": [
                {"field": "Domain", "operator": "equals", "value": "x"},
            ]},
            output_mode="text",
            output_text="Active Directory",
        )
        assert result["matched"] == 1
        assert result["passed"] == 1
        assert result["samples"] == [{"hostname": "host-a", "output": "Active Directory"}]
        # In text mode, repo.fetch_asset_field_values must not be called
        repo.fetch_asset_field_values.assert_not_called()

    def test_field_mode_uses_fetch_asset_field_values(self, service, engine, db, repo):
        engine.asset_ids_matching.side_effect = [{self.A1}, {self.A1}]
        db.execute_query.return_value = [{"id": self.A1, "hostname": "host-a"}]
        repo.fetch_asset_field_values.return_value = {self.A1: "corp.example.com"}
        result = service.preview_output(
            target_filters=[{"field": "Category", "operator": "equals", "value": "workstation"}],
            compliance_criteria={"logic": "any", "conditions": [
                {"field": "Domain", "operator": "is_not_empty", "value": ""},
            ]},
            output_mode="field",
            output_field_id=_DOMAIN_FIELD,
        )
        assert result["matched"] == 1
        assert result["passed"] == 1
        assert result["samples"] == [{"hostname": "host-a", "output": "corp.example.com"}]
        repo.fetch_asset_field_values.assert_called_once()

    def test_respects_sample_limit(self, service, engine, db, repo):
        many_ids = {f"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa{i:04d}" for i in range(10)}
        engine.asset_ids_matching.side_effect = [many_ids, many_ids]
        # Return hostnames for every sample id (deterministic mapping)
        db.execute_query.side_effect = lambda sql, params: [
            {"id": aid, "hostname": f"host-{aid[-4:]}"} for aid in params["ids"]
        ]
        repo.fetch_asset_field_values.return_value = {}
        result = service.preview_output(
            target_filters=[{"field": "Category", "operator": "equals", "value": "server"}],
            compliance_criteria={"logic": "any", "conditions": [
                {"field": "Domain", "operator": "is_not_empty", "value": ""},
            ]},
            output_mode="field",
            output_field_id=_DOMAIN_FIELD,
            sample_limit=3,
        )
        assert result["matched"] == 10
        assert result["passed"] == 10
        assert len(result["samples"]) == 3
