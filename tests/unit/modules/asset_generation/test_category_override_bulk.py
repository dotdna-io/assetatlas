# SPDX-License-Identifier: AGPL-3.0-or-later
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from src.modules.asset_generation.repositories.category_override_repository import (
    CategoryOverrideRepository,
)


def _repo_with_cursor():
    db = MagicMock()
    cursor = MagicMock()
    cursor.rowcount = 2

    @contextmanager
    def _get_cursor():
        yield (cursor, MagicMock())

    db.conn_manager.get_cursor.side_effect = _get_cursor
    return CategoryOverrideRepository(db), db, cursor


def test_save_overrides_bulk_lowercases_category_and_nulls_snapshot():
    repo, db, cursor = _repo_with_cursor()
    with patch(
        'src.modules.asset_generation.repositories.category_override_repository.extras.execute_values'
    ) as ev:
        n = repo.save_overrides_bulk(['a1', 'a2'], 'Network', 'Switch',
                                     reason='bulk', overridden_by='admin')
    assert n == 2
    values = ev.call_args[0][2]
    assert values[0] == ('a1', 'network', 'Switch', 'bulk', 'admin', True, None)
    assert 'ON CONFLICT (asset_id)' in ev.call_args[0][1]


def test_save_overrides_bulk_empty_is_noop():
    repo, db, _ = _repo_with_cursor()
    assert repo.save_overrides_bulk([], 'Network', 'Switch') == 0
    db.conn_manager.get_cursor.assert_not_called()


def test_deactivate_overrides_bulk_uses_any_array():
    db = MagicMock()
    repo = CategoryOverrideRepository(db)
    n = repo.deactivate_overrides_bulk(['a1', 'a2'])
    assert n == 2
    sql, params = db.execute_query.call_args[0][0], db.execute_query.call_args[0][1]
    assert 'active = false' in sql
    assert 'ANY(%(ids)s::uuid[])' in sql
    assert params['ids'] == ['a1', 'a2']
