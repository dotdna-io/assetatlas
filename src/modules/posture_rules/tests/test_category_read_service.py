# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock
import pytest

from src.modules.posture_rules.services.category_read_service import PostureCategoryReadService
from tests.factories import posture_category_factory, posture_sub_rule_factory


@pytest.fixture
def repo():
    r = MagicMock()
    r.list_categories.return_value = [posture_category_factory()]
    r.list_all_sub_rules.return_value = [posture_sub_rule_factory(id="sr-1")]
    r.list_sub_rules_by_category.return_value = [posture_sub_rule_factory(id="sr-1")]
    r.get_category_stats.return_value = {
        "secured": {"pass": 100, "fail": 5, "n_a": 20}
    }
    r.latest_history_per_sub_rule.return_value = {}
    return r


@pytest.fixture
def service(repo):
    return PostureCategoryReadService(repository=repo)


def test_list_categories_uses_single_sub_rules_query(service, repo):
    out = service.list_categories()

    assert len(out["categories"]) == 1
    cat = out["categories"][0]
    assert cat["id"] == "secured"
    assert cat["stats"] == {"pass": 100, "fail": 5, "n_a": 20}
    assert cat["sub_rules"][0]["id"] == "sr-1"
    # N+1 guard: per-category lookup must NOT be used in list_categories.
    repo.list_sub_rules_by_category.assert_not_called()
    repo.list_all_sub_rules.assert_called_once()


def test_list_categories_groups_sub_rules_correctly(service, repo):
    repo.list_categories.return_value = [
        posture_category_factory(id="secured", display_order=1),
        posture_category_factory(id="monitored", display_order=5),
    ]
    repo.list_all_sub_rules.return_value = [
        posture_sub_rule_factory(id="sr-ep", category_id="secured"),
        posture_sub_rule_factory(id="sr-mon-1", category_id="monitored"),
        posture_sub_rule_factory(id="sr-mon-2", category_id="monitored"),
    ]
    repo.get_category_stats.return_value = {}

    out = service.list_categories()

    by_id = {c["id"]: c for c in out["categories"]}
    assert [sr["id"] for sr in by_id["secured"]["sub_rules"]] == ["sr-ep"]
    assert [sr["id"] for sr in by_id["monitored"]["sub_rules"]] == ["sr-mon-1", "sr-mon-2"]


def test_get_category_404(service, repo):
    repo.get_category.return_value = None
    assert service.get_category("missing") is None


def test_get_category_returns_full_shape(service, repo):
    repo.get_category.return_value = posture_category_factory()
    out = service.get_category("secured")
    assert out is not None
    assert out["id"] == "secured"
    assert "sub_rules" in out
    assert "stats" in out


def test_list_categories_exposes_last_evaluated_at():
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from src.modules.posture_rules.services.category_read_service import (
        PostureCategoryReadService,
    )

    ts = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    repo = MagicMock()
    repo.list_categories.return_value = [{
        "id": "secured",
        "name": "Secured",
        "description": "",
        "icon": "",
        "applicable_categories": ["server", "workstation"],
        "display_order": 1,
        "last_evaluated_at": ts,
    }]
    repo.get_category_stats.return_value = {}
    repo.list_all_sub_rules.return_value = []
    repo.latest_history_per_sub_rule.return_value = {}

    svc = PostureCategoryReadService(repository=repo)
    result = svc.list_categories()

    assert result["categories"][0]["last_evaluated_at"] == ts


def test_list_categories_attaches_last_edit_when_history_exists():
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from src.modules.posture_rules.services.category_read_service import (
        PostureCategoryReadService,
    )

    ts = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    repo = MagicMock()
    repo.list_categories.return_value = [{
        "id": "secured",
        "name": "Secured",
        "description": "",
        "icon": "",
        "applicable_categories": [],
        "display_order": 1,
    }]
    repo.get_category_stats.return_value = {}
    repo.list_all_sub_rules.return_value = [{
        "id": "sr-1", "category_id": "secured", "name": "x",
    }]
    repo.latest_history_per_sub_rule.return_value = {
        "sr-1": {
            "action": "update", "changed_by": "u1",
            "changed_by_username": "alice", "created_at": ts,
        },
    }

    svc = PostureCategoryReadService(repository=repo)
    result = svc.list_categories()

    sub_rules = result["categories"][0]["sub_rules"]
    assert sub_rules[0]["last_edit"]["changed_by_username"] == "alice"
    assert sub_rules[0]["last_edit"]["action"] == "update"


def test_list_categories_no_history_returns_null_last_edit():
    from unittest.mock import MagicMock
    from src.modules.posture_rules.services.category_read_service import (
        PostureCategoryReadService,
    )

    repo = MagicMock()
    repo.list_categories.return_value = [{
        "id": "secured",
        "name": "Secured",
        "description": "",
        "icon": "",
        "applicable_categories": [],
        "display_order": 1,
    }]
    repo.get_category_stats.return_value = {}
    repo.list_all_sub_rules.return_value = [{
        "id": "sr-1", "category_id": "secured", "name": "x",
    }]
    repo.latest_history_per_sub_rule.return_value = {}

    svc = PostureCategoryReadService(repository=repo)
    result = svc.list_categories()

    sub_rules = result["categories"][0]["sub_rules"]
    assert sub_rules[0]["last_edit"] is None
