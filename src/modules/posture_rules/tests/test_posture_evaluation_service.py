# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock
import pytest

from src.modules.posture_rules.services.posture_evaluation_service import PostureEvaluationService

# DB-row shape for output_value when stored as JSONB
_FIELD_OUTPUT = lambda name: {"mode": "field", "field_id": {"kind": "static", "name": name}}  # noqa: E731
_TEXT_OUTPUT = lambda text: {"mode": "text", "text": text}  # noqa: E731

A1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"
A2 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa02"
ALL_ASSET_IDS = {A1, A2}


@pytest.fixture
def repo():
    r = MagicMock()
    r.list_categories.return_value = [
        {"id": "secured", "applicable_categories": []},
        {"id": "vulnerability_scanning", "applicable_categories": []},
        {"id": "domain_joined", "applicable_categories": []},
        {"id": "device_management", "applicable_categories": []},
        {"id": "monitored", "applicable_categories": []},
    ]
    r.fetch_asset_categories.return_value = {aid: "workstation" for aid in ALL_ASSET_IDS}
    r.list_enabled_sub_rules.return_value = []
    r.fetch_asset_field_values.return_value = {}
    return r


@pytest.fixture
def engine():
    return MagicMock()


@pytest.fixture
def db():
    m = MagicMock()
    # Default: pg_try_advisory_lock returns True; unlock is a no-op. This lets
    # existing tests that don't care about the lock continue to work unchanged.
    m.execute_query.return_value = [{"pg_try_advisory_lock": True}]
    return m


@pytest.fixture
def resolver():
    return MagicMock()


@pytest.fixture
def service(repo, engine, db, resolver):
    return PostureEvaluationService(
        repository=repo, query_engine=engine, db_adapter=db, resolver=resolver,
    )


def test_no_sub_rules_assigns_n_a_everywhere(service, repo, engine):
    engine.asset_ids_matching.return_value = ALL_ASSET_IDS

    service.evaluate_all()

    written = repo.write_asset_posture_rules.call_args[0][0]
    assert len(written) == 2
    _, payload = written[0]
    for cat in (
        "secured", "vulnerability_scanning", "domain_joined",
        "device_management", "monitored",
    ):
        assert payload[cat] == {"state": "n/a", "output": None}


def test_sub_rule_matching_and_passing_gives_true(service, repo, engine):
    repo.list_enabled_sub_rules.return_value = [{
        "id": "sr-1", "category_id": "secured",
        "target_filters": [{"field": "Category", "operator": "equals", "value": "server"}],
        "compliance_criteria": {
            "logic": "any",
            "conditions": [{"field": "Data Source Category", "operator": "contains", "value": "EDR"}],
        },
        "output_mode": None, "output_value": None,
    }]
    engine.asset_ids_matching.side_effect = [
        ALL_ASSET_IDS,      # load-all-assets
        ALL_ASSET_IDS,      # target match
        {A1},               # criteria pass
    ]

    service.evaluate_all()

    written = dict(repo.write_asset_posture_rules.call_args[0][0])
    assert written[A1]["secured"] == {"state": "true", "output": None}
    assert written[A2]["secured"] == {"state": "false", "output": None}


def test_no_target_match_gives_n_a(service, repo, engine):
    repo.list_enabled_sub_rules.return_value = [{
        "id": "sr-1", "category_id": "secured",
        "target_filters": [{"field": "Category", "operator": "equals", "value": "server"}],
        "compliance_criteria": {
            "logic": "any",
            "conditions": [{"field": "Data Source Category", "operator": "contains", "value": "EDR"}],
        },
        "output_mode": None, "output_value": None,
    }]
    engine.asset_ids_matching.side_effect = [ALL_ASSET_IDS, set()]

    service.evaluate_all()

    written = dict(repo.write_asset_posture_rules.call_args[0][0])
    assert written[A1]["secured"] == {"state": "n/a", "output": None}


def test_or_semantics_across_multiple_sub_rules(service, repo, engine):
    """If asset matches both sub-rules and one passes, final state is 'true'."""
    repo.list_enabled_sub_rules.return_value = [
        {"id": "sr-1", "category_id": "secured",
         "target_filters": [{"field": "Category", "operator": "equals", "value": "server"}],
         "compliance_criteria": {"logic": "any", "conditions": [{"field": "x", "operator": "equals", "value": "y"}]},
         "output_mode": None, "output_value": None},
        {"id": "sr-2", "category_id": "secured",
         "target_filters": [{"field": "Category", "operator": "equals", "value": "server"}],
         "compliance_criteria": {"logic": "any", "conditions": [{"field": "x", "operator": "equals", "value": "z"}]},
         "output_mode": None, "output_value": None},
    ]
    engine.asset_ids_matching.side_effect = [
        ALL_ASSET_IDS,  # load-all-assets
        {A1},           # sr-1 target
        set(),          # sr-1 criteria (fail)
        {A1},           # sr-2 target
        {A1},           # sr-2 criteria (pass)
    ]

    service.evaluate_all()

    written = dict(repo.write_asset_posture_rules.call_args[0][0])
    assert written[A1]["secured"] == {"state": "true", "output": None}


def test_output_field_populated_from_first_passing_sub_rule(service, repo, engine):
    """When a sub-rule with output_field passes, the field value is stored as output."""
    repo.list_enabled_sub_rules.return_value = [
        {"id": "sr-1", "category_id": "domain_joined",
         "target_filters": [{"field": "Category", "operator": "equals", "value": "workstation"}],
         "compliance_criteria": {"logic": "any", "conditions": [{"field": "Domain", "operator": "is_not_empty", "value": ""}]},
         "output_mode": "field", "output_value": _FIELD_OUTPUT("Domain")},
    ]
    engine.asset_ids_matching.side_effect = [
        ALL_ASSET_IDS,  # load-all-assets
        {A1, A2},       # sr-1 target (both match)
        {A1, A2},       # sr-1 criteria (both pass)
    ]
    repo.fetch_asset_field_values.return_value = {A1: "corp.example.com", A2: "other.local"}

    service.evaluate_all()

    written = dict(repo.write_asset_posture_rules.call_args[0][0])
    assert written[A1]["domain_joined"] == {"state": "true", "output": "corp.example.com"}
    assert written[A2]["domain_joined"] == {"state": "true", "output": "other.local"}
    repo.fetch_asset_field_values.assert_called_once()


def test_output_field_first_wins_when_multiple_sub_rules_pass(service, repo, engine):
    """First sub-rule (by list order) with output_field wins for an asset."""
    repo.list_enabled_sub_rules.return_value = [
        {"id": "sr-1", "category_id": "monitored",
         "target_filters": [{"field": "Category", "operator": "equals", "value": "server"}],
         "compliance_criteria": {"logic": "any", "conditions": [{"field": "x", "operator": "equals", "value": "y"}]},
         "output_mode": "field", "output_value": _FIELD_OUTPUT("Data Source Category")},
        {"id": "sr-2", "category_id": "monitored",
         "target_filters": [{"field": "Category", "operator": "equals", "value": "server"}],
         "compliance_criteria": {"logic": "any", "conditions": [{"field": "x", "operator": "equals", "value": "z"}]},
         "output_mode": "field", "output_value": _FIELD_OUTPUT("Location")},
    ]
    engine.asset_ids_matching.side_effect = [
        ALL_ASSET_IDS,  # load-all-assets
        {A1},           # sr-1 target
        {A1},           # sr-1 criteria (pass)
        {A1},           # sr-2 target
        {A1},           # sr-2 criteria (pass)
    ]
    repo.fetch_asset_field_values.return_value = {A1: "Splunk"}

    service.evaluate_all()

    written = dict(repo.write_asset_posture_rules.call_args[0][0])
    # sr-1's output_field ("Data Source Category") wins — sr-2 never queried for this asset
    assert written[A1]["monitored"] == {"state": "true", "output": "Splunk"}
    assert repo.fetch_asset_field_values.call_count == 1


def test_output_text_mode_stores_literal(service, repo, engine):
    """output_mode='text' stores the literal value without fetching from the asset."""
    repo.list_enabled_sub_rules.return_value = [{
        "id": "sr-1", "category_id": "domain_joined",
        "target_filters": [{"field": "Category", "operator": "equals", "value": "workstation"}],
        "compliance_criteria": {"logic": "any", "conditions": [{"field": "Domain", "operator": "is_not_empty", "value": ""}]},
        "output_mode": "text", "output_value": _TEXT_OUTPUT("Active Directory"),
    }]
    engine.asset_ids_matching.side_effect = [
        ALL_ASSET_IDS, {A1, A2}, {A1, A2},
    ]

    service.evaluate_all()

    written = dict(repo.write_asset_posture_rules.call_args[0][0])
    assert written[A1]["domain_joined"] == {"state": "true", "output": "Active Directory"}
    assert written[A2]["domain_joined"] == {"state": "true", "output": "Active Directory"}
    repo.fetch_asset_field_values.assert_not_called()


def test_non_applicable_asset_category_forces_na(service, repo, engine):
    """Assets whose category is not in applicable_categories get n/a, regardless of sub-rule match."""
    # secured: applicable to server, workstation
    repo.list_categories.return_value = [{
        "id": "secured",
        "applicable_categories": ["server", "workstation"],
    }]
    repo.list_enabled_sub_rules.return_value = [{
        "id": "sr-1", "category_id": "secured",
        "target_filters": [{"field": "Category", "operator": "in", "value": "server,workstation,iot"}],
        "compliance_criteria": {"logic": "any", "conditions": [{"field": "Data Source Category", "operator": "contains", "value": "EDR"}]},
        "output_mode": None, "output_value": None,
    }]
    # A1 = server (applicable, passes), A2 = iot (not applicable, should be n/a)
    repo.fetch_asset_categories.return_value = {A1: "server", A2: "iot"}
    engine.asset_ids_matching.side_effect = [
        ALL_ASSET_IDS,  # load-all-assets
        {A1},           # sr-1 target (only A1 now — A2 pre-filtered out)
        {A1},           # sr-1 criteria (pass)
    ]

    service.evaluate_all()

    written = dict(repo.write_asset_posture_rules.call_args[0][0])
    assert written[A1]["secured"] == {"state": "true", "output": None}
    assert written[A2]["secured"] == {"state": "n/a", "output": None}


def test_empty_applicable_categories_behaves_as_unconstrained(service, repo, engine):
    """applicable_categories == [] means the category evaluates for every asset."""
    repo.list_categories.return_value = [{
        "id": "secured",
        "applicable_categories": [],
    }]
    repo.list_enabled_sub_rules.return_value = [{
        "id": "sr-1", "category_id": "secured",
        "target_filters": [{"field": "Category", "operator": "in", "value": "server,iot"}],
        "compliance_criteria": {"logic": "any", "conditions": [{"field": "x", "operator": "equals", "value": "y"}]},
        "output_mode": None, "output_value": None,
    }]
    repo.fetch_asset_categories.return_value = {A1: "server", A2: "iot"}
    engine.asset_ids_matching.side_effect = [
        ALL_ASSET_IDS,  # load-all-assets
        ALL_ASSET_IDS,  # sr-1 target
        {A1, A2},       # sr-1 criteria (both pass)
    ]

    service.evaluate_all()

    written = dict(repo.write_asset_posture_rules.call_args[0][0])
    assert written[A1]["secured"]["state"] == "true"
    assert written[A2]["secured"]["state"] == "true"


def test_mark_all_evaluated_called_after_write(service, repo, engine):
    """mark_all_evaluated must be called after write_asset_posture_rules."""
    repo.list_categories.return_value = [{"id": "secured", "applicable_categories": []}]
    repo.list_enabled_sub_rules.return_value = []
    repo.fetch_asset_categories.return_value = {A1: "server", A2: "workstation"}
    engine.asset_ids_matching.return_value = ALL_ASSET_IDS

    service.evaluate_all()

    repo.write_asset_posture_rules.assert_called_once()
    repo.mark_all_evaluated.assert_called_once()


class TestEvaluateAllAdvisoryLock:
    """evaluate_all must try to acquire a posture-evaluation advisory lock."""

    def test_acquires_and_releases_lock_on_success(self, service, repo, engine):
        """Lock acquired before eval, released after write."""
        repo.list_categories.return_value = [
            {"id": "secured", "applicable_categories": []},
        ]
        repo.list_enabled_sub_rules.return_value = []
        repo.fetch_asset_categories.return_value = {}
        engine.asset_ids_matching.return_value = set()
        # side_effect: first call returns lock acquisition, second is unlock (ignored)
        service.db.execute_query.side_effect = [
            [{"pg_try_advisory_lock": True}],
            None,
        ]

        result = service.evaluate_all()

        assert result["skipped"] is False
        assert "pg_try_advisory_lock" in service.db.execute_query.call_args_list[0][0][0]
        assert "pg_advisory_unlock" in service.db.execute_query.call_args_list[-1][0][0]

    def test_skips_when_lock_unavailable(self, service, repo, engine):
        """If another evaluation is running, skip + return skipped=True."""
        service.db.execute_query.side_effect = [
            [{"pg_try_advisory_lock": False}],
        ]

        result = service.evaluate_all()

        assert result["skipped"] is True
        assert result["updated_count"] == 0
        repo.write_asset_posture_rules.assert_not_called()
        repo.mark_all_evaluated.assert_not_called()

    def test_releases_lock_even_on_error(self, service, repo, engine):
        """If the inner eval raises, the advisory lock must still be released."""
        service.db.execute_query.side_effect = [
            [{"pg_try_advisory_lock": True}],
            None,
        ]
        repo.list_categories.side_effect = RuntimeError("boom")

        import pytest
        with pytest.raises(RuntimeError):
            service.evaluate_all()

        unlock_calls = [
            c for c in service.db.execute_query.call_args_list
            if "pg_advisory_unlock" in c[0][0]
        ]
        assert len(unlock_calls) == 1


def test_evaluate_all_writes_coverage_snapshot(service, repo, engine):
    """A successful evaluate_all run must persist a coverage snapshot once."""
    engine.asset_ids_matching.return_value = ALL_ASSET_IDS

    service.evaluate_all()

    assert repo.write_coverage_snapshot.call_count == 1


def test_evaluate_all_succeeds_when_coverage_snapshot_write_fails(service, repo, engine, caplog):
    """Coverage-snapshot bookkeeping must never break the evaluation pass."""
    engine.asset_ids_matching.return_value = ALL_ASSET_IDS
    repo.write_coverage_snapshot.side_effect = RuntimeError("disk full")

    result = service.evaluate_all()

    assert result["skipped"] is False
    assert any(
        "coverage snapshot" in r.message.lower() and r.levelname == "WARNING"
        for r in caplog.records
    )


def test_evaluate_all_writes_tier_snapshot(service, repo, engine):
    """A successful evaluate_all run must persist a tier snapshot once."""
    engine.asset_ids_matching.return_value = ALL_ASSET_IDS

    service.evaluate_all()

    assert repo.write_tier_snapshot.call_count == 1


def test_evaluate_all_succeeds_when_snapshot_write_fails(service, repo, engine, caplog):
    """Snapshot bookkeeping must never break the evaluation pass."""
    engine.asset_ids_matching.return_value = ALL_ASSET_IDS
    repo.write_tier_snapshot.side_effect = RuntimeError("disk full")

    result = service.evaluate_all()

    assert result["skipped"] is False
    assert any(
        "snapshot" in r.message.lower() and r.levelname == "WARNING"
        for r in caplog.records
    )
