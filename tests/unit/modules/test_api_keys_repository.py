# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ApiKeyRepository."""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from src.modules.api_keys.repository import ApiKeyRepository


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def repo(db):
    return ApiKeyRepository(db)


class TestCreate:
    def test_create_inserts_and_returns_row(self, repo, db):
        db.execute_query.return_value = [{
            'id': 'key-uuid',
            'user_id': 'user-uuid',
            'name': 'Test Key',
            'key_prefix': 'aa_abcde',
            'key_hash': 'somehash',
            'expires_at': datetime(2027, 1, 1, tzinfo=timezone.utc),
            'is_active': True,
            'last_used_at': None,
            'last_used_ip': None,
            'created_at': datetime(2026, 4, 2, tzinfo=timezone.utc),
        }]

        result = repo.create(
            user_id='user-uuid',
            name='Test Key',
            key_prefix='aa_abcde',
            key_hash='somehash',
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )

        assert result['id'] == 'key-uuid'
        assert result['name'] == 'Test Key'
        db.execute_query.assert_called_once()
        sql = db.execute_query.call_args[0][0]
        assert 'INSERT INTO api_keys' in sql


class TestGetByHash:
    def test_returns_key_row_when_found(self, repo, db):
        db.execute_query.return_value = [{'id': 'key-uuid', 'is_active': True}]
        result = repo.get_by_hash('somehash')
        assert result['id'] == 'key-uuid'

    def test_returns_none_when_not_found(self, repo, db):
        db.execute_query.return_value = []
        assert repo.get_by_hash('nope') is None


class TestListByUser:
    def test_returns_all_keys_for_user(self, repo, db):
        db.execute_query.return_value = [
            {'id': 'k1', 'name': 'Key 1'},
            {'id': 'k2', 'name': 'Key 2'},
        ]
        result = repo.list_all()
        assert len(result) == 2


class TestDelete:
    def test_delete_removes_key(self, repo, db):
        db.execute_query.return_value = [{'id': 'key-uuid'}]
        result = repo.delete('key-uuid')
        assert result is True
        sql = db.execute_query.call_args[0][0]
        assert 'DELETE FROM api_keys' in sql

    def test_delete_returns_false_when_not_found(self, repo, db):
        db.execute_query.return_value = []
        result = repo.delete('nope')
        assert result is False


class TestUpdate:
    def test_update_name(self, repo, db):
        db.execute_query.return_value = [{'id': 'key-uuid', 'name': 'Updated'}]
        result = repo.update('key-uuid', name='Updated')
        assert result['name'] == 'Updated'

    def test_update_is_active(self, repo, db):
        db.execute_query.return_value = [{'id': 'key-uuid', 'is_active': False}]
        result = repo.update('key-uuid', is_active=False)
        assert result['is_active'] is False

    def test_update_returns_none_when_not_found(self, repo, db):
        db.execute_query.return_value = []
        result = repo.update('nope', name='x')
        assert result is None


class TestUpdateLastUsed:
    def test_updates_timestamp_and_ip(self, repo, db):
        db.execute_query.return_value = []
        repo.update_last_used('key-uuid', '10.1.2.3')
        sql = db.execute_query.call_args[0][0]
        assert 'last_used_at' in sql
        assert 'last_used_ip' in sql
