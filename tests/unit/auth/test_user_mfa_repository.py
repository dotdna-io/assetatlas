# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for UserMfaRepository (multi-method, post-#608)."""
from uuid import UUID, uuid4

from src.auth.repositories.user_mfa_repository import UserMfaRepository


# ---- read paths ------------------------------------------------------------

def test_list_methods_returns_rows(mock_conn_manager):
    cm, cursor, _ = mock_conn_manager
    user_id = uuid4()
    method_id = uuid4()
    cursor.fetchall.return_value = [{
        'id': str(method_id), 'user_id': str(user_id),
        'type': 'totp', 'label': 'Phone',
        'mfa_enabled': True, 'mfa_secret': 'ENC:abc',
        'created_at': None, 'updated_at': None,
    }]
    repo = UserMfaRepository(cm)
    rows = repo.list_methods(user_id)
    assert len(rows) == 1
    assert rows[0]['mfa_enabled'] is True
    args, _ = cursor.execute.call_args
    assert 'SELECT' in args[0] and 'FROM user_mfa' in args[0]
    assert 'ORDER BY created_at ASC' in args[0]


def test_list_enabled_methods_filters_pending(mock_conn_manager):
    cm, cursor, _ = mock_conn_manager
    cursor.fetchall.return_value = [
        {'id': '1', 'mfa_enabled': True, 'mfa_secret': 'ENC:a'},
        {'id': '2', 'mfa_enabled': False, 'mfa_secret': 'ENC:b'},
        {'id': '3', 'mfa_enabled': True, 'mfa_secret': 'ENC:c'},
    ]
    repo = UserMfaRepository(cm)
    rows = repo.list_enabled_methods(uuid4())
    assert [r['id'] for r in rows] == ['1', '3']


def test_count_enabled_methods(mock_conn_manager):
    cm, cursor, _ = mock_conn_manager
    cursor.fetchone.return_value = {'n': 2}
    repo = UserMfaRepository(cm)
    assert repo.count_enabled_methods(uuid4()) == 2
    args, _ = cursor.execute.call_args
    assert 'SELECT COUNT(*)' in args[0]
    assert 'mfa_enabled = true' in args[0]


def test_count_enabled_methods_returns_zero_when_no_row(mock_conn_manager):
    cm, cursor, _ = mock_conn_manager
    cursor.fetchone.return_value = None
    repo = UserMfaRepository(cm)
    assert repo.count_enabled_methods(uuid4()) == 0


def test_get_method_by_id(mock_conn_manager):
    cm, cursor, _ = mock_conn_manager
    method_id = uuid4()
    cursor.fetchone.return_value = {
        'id': str(method_id), 'user_id': str(uuid4()),
        'type': 'totp', 'label': 'Phone',
        'mfa_enabled': True, 'mfa_secret': 'ENC:x',
    }
    repo = UserMfaRepository(cm)
    row = repo.get_method(method_id)
    assert row['id'] == str(method_id)
    args, _ = cursor.execute.call_args
    assert 'WHERE id =' in args[0]


def test_get_pending_method_returns_most_recent(mock_conn_manager):
    cm, cursor, _ = mock_conn_manager
    cursor.fetchone.return_value = {
        'id': 'pending-id', 'user_id': str(uuid4()),
        'mfa_enabled': False, 'mfa_secret': 'ENC:p',
    }
    repo = UserMfaRepository(cm)
    row = repo.get_pending_method(uuid4())
    assert row is not None
    assert row['mfa_enabled'] is False
    args, _ = cursor.execute.call_args
    assert 'mfa_enabled = false' in args[0]
    assert 'ORDER BY created_at DESC' in args[0]
    assert 'LIMIT 1' in args[0]


# ---- atomic write paths ----------------------------------------------------

def test_replace_pending_method_locks_clears_then_inserts(mock_conn_manager):
    """Three SQL calls in order: advisory lock → DELETE pending → INSERT.
    All inside one transaction (single commit at the end)."""
    cm, cursor, conn = mock_conn_manager
    new_id = uuid4()
    cursor.fetchone.return_value = {'id': str(new_id)}
    repo = UserMfaRepository(cm)

    result = repo.replace_pending_method(
        uuid4(), encrypted_secret='ENC:newsecret', label='Phone',
    )

    assert isinstance(result, UUID)
    assert result == new_id
    sqls = [c.args[0] for c in cursor.execute.call_args_list]
    assert len(sqls) == 3
    assert 'pg_advisory_xact_lock' in sqls[0]
    assert 'DELETE FROM user_mfa' in sqls[1]
    assert 'mfa_enabled = false' in sqls[1]
    assert 'INSERT INTO user_mfa' in sqls[2]
    assert 'RETURNING id' in sqls[2]
    insert_params = cursor.execute.call_args_list[2].args[1]
    assert insert_params['label'] == 'Phone'
    assert insert_params['secret'] == 'ENC:newsecret'
    assert insert_params['type'] == 'totp'
    # Single commit covers the whole atomic op.
    conn.commit.assert_called_once()


def test_replace_pending_method_uses_default_label_when_blank(mock_conn_manager):
    cm, cursor, _ = mock_conn_manager
    cursor.fetchone.return_value = {'id': str(uuid4())}
    repo = UserMfaRepository(cm)
    repo.replace_pending_method(uuid4(), encrypted_secret='x', label='   ')
    insert_params = cursor.execute.call_args_list[2].args[1]
    assert insert_params['label'] == UserMfaRepository.DEFAULT_LABEL


def test_enable_method_flips_enabled(mock_conn_manager):
    cm, cursor, conn = mock_conn_manager
    method_id = uuid4()
    repo = UserMfaRepository(cm)
    repo.enable_method(method_id)
    args, _ = cursor.execute.call_args
    assert 'UPDATE user_mfa' in args[0]
    assert 'mfa_enabled = true' in args[0].lower()
    assert args[1]['method_id'] == str(method_id)
    conn.commit.assert_called_once()


def test_update_label_safely_returns_true_when_row_updated(mock_conn_manager):
    cm, cursor, conn = mock_conn_manager
    cursor.rowcount = 1
    repo = UserMfaRepository(cm)
    assert repo.update_label_safely(uuid4(), uuid4(), 'New Name') is True
    args, _ = cursor.execute.call_args
    assert 'UPDATE user_mfa' in args[0]
    # Ownership check is in the SQL, not in Python — atomic.
    assert 'WHERE id = %(method_id)s AND user_id = %(user_id)s' in args[0]
    conn.commit.assert_called_once()


def test_update_label_safely_returns_false_when_no_row_matches(mock_conn_manager):
    """Cross-user attempt: WHERE clause doesn't match, rowcount=0."""
    cm, cursor, _ = mock_conn_manager
    cursor.rowcount = 0
    repo = UserMfaRepository(cm)
    assert repo.update_label_safely(uuid4(), uuid4(), 'attempted') is False


def test_update_label_safely_falls_back_to_default_when_blank(mock_conn_manager):
    cm, cursor, _ = mock_conn_manager
    cursor.rowcount = 1
    repo = UserMfaRepository(cm)
    repo.update_label_safely(uuid4(), uuid4(), '   ')
    assert cursor.execute.call_args[0][1]['label'] == UserMfaRepository.DEFAULT_LABEL


def test_delete_method_safely_deletes_when_pending(mock_conn_manager):
    """Pending rows are always removable — no floor check needed."""
    cm, cursor, conn = mock_conn_manager
    method_id = uuid4()
    user_id = uuid4()
    cursor.fetchone.return_value = {
        'id': str(method_id), 'user_id': str(user_id),
        'type': 'totp', 'label': 'Pending',
        'mfa_enabled': False, 'mfa_secret': 'ENC:p',
    }
    repo = UserMfaRepository(cm)

    result = repo.delete_method_safely(user_id, method_id)

    assert result['deleted'] is True
    assert result['method']['mfa_enabled'] is False
    sqls = [c.args[0] for c in cursor.execute.call_args_list]
    assert 'pg_advisory_xact_lock' in sqls[0]
    assert any('DELETE FROM user_mfa' in s for s in sqls)
    # Pending → no count query because the floor doesn't apply.
    assert not any('COUNT(*)' in s for s in sqls)
    conn.commit.assert_called_once()


def test_delete_method_safely_deletes_enabled_when_count_above_one(mock_conn_manager):
    cm, cursor, conn = mock_conn_manager
    method_id = uuid4()
    user_id = uuid4()
    # Sequence of fetchone calls: target row, then count-of-enabled.
    cursor.fetchone.side_effect = [
        {
            'id': str(method_id), 'user_id': str(user_id),
            'type': 'totp', 'label': 'Phone',
            'mfa_enabled': True, 'mfa_secret': 'ENC:x',
        },
        {'n': 2},
    ]
    repo = UserMfaRepository(cm)
    result = repo.delete_method_safely(user_id, method_id)
    assert result['deleted'] is True
    sqls = [c.args[0] for c in cursor.execute.call_args_list]
    assert any('SELECT COUNT(*)' in s for s in sqls)
    assert any('DELETE FROM user_mfa' in s for s in sqls)
    conn.commit.assert_called_once()


def test_delete_method_safely_refuses_last_enabled(mock_conn_manager):
    cm, cursor, conn = mock_conn_manager
    method_id = uuid4()
    user_id = uuid4()
    cursor.fetchone.side_effect = [
        {
            'id': str(method_id), 'user_id': str(user_id),
            'type': 'totp', 'label': 'Only',
            'mfa_enabled': True, 'mfa_secret': 'ENC:x',
        },
        {'n': 1},
    ]
    repo = UserMfaRepository(cm)
    result = repo.delete_method_safely(user_id, method_id)
    assert result['deleted'] is False
    assert result['reason'] == 'must_keep_one_mfa_method'
    assert result['method']['id'] == str(method_id)
    # Rolled back, NOT committed.
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once()
    # No DELETE was issued.
    sqls = [c.args[0] for c in cursor.execute.call_args_list]
    assert not any(s.lstrip().startswith('DELETE') for s in sqls)


def test_delete_method_safely_returns_not_found_for_missing_or_other_user(mock_conn_manager):
    """The SELECT is scoped to (id, user_id), so cross-user attempts return
    no row — same response as a genuine 'missing'."""
    cm, cursor, conn = mock_conn_manager
    cursor.fetchone.return_value = None
    repo = UserMfaRepository(cm)
    result = repo.delete_method_safely(uuid4(), uuid4())
    assert result['deleted'] is False
    assert result['reason'] == 'method_not_found'
    assert result['method'] is None
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once()


def test_delete_all_methods_returns_count(mock_conn_manager):
    cm, cursor, conn = mock_conn_manager
    cursor.rowcount = 3
    repo = UserMfaRepository(cm)
    deleted = repo.delete_all_methods(uuid4())
    assert deleted == 3
    args, _ = cursor.execute.call_args
    assert 'DELETE FROM user_mfa' in args[0]
    assert 'WHERE user_id' in args[0]
    conn.commit.assert_called_once()
