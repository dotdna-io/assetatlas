# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ApiKeyService."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

from src.modules.api_keys.service import ApiKeyService


@pytest.fixture
def repo():
    return MagicMock()


@pytest.fixture
def service(repo):
    return ApiKeyService(repo)


class TestCreateKey:
    def test_generates_key_with_aa_prefix(self, service, repo):
        repo.create.return_value = {
            'id': 'key-uuid',
            'user_id': 'user-uuid',
            'name': 'Test',
            'key_prefix': 'aa_abcde',
            'key_hash': 'hash',
            'expires_at': datetime(2027, 1, 1, tzinfo=timezone.utc),
            'is_active': True,
            'last_used_at': None,
            'last_used_ip': None,
            'created_at': datetime(2026, 4, 2, tzinfo=timezone.utc),
        }

        result = service.create_key(
            user_id='user-uuid',
            name='Test',
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )

        assert result['key'].startswith('aa_')
        assert len(result['key']) == 43  # aa_ + 40 hex chars
        assert result['id'] == 'key-uuid'

    def test_stores_hashed_key(self, service, repo):
        repo.create.return_value = {
            'id': 'key-uuid', 'user_id': 'u', 'name': 'T',
            'key_prefix': 'aa_abcde', 'key_hash': 'h',
            'expires_at': datetime(2027, 1, 1, tzinfo=timezone.utc),
            'is_active': True, 'last_used_at': None,
            'last_used_ip': None,
            'created_at': datetime(2026, 4, 2, tzinfo=timezone.utc),
        }

        service.create_key(
            user_id='u', name='T',
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )

        call_kwargs = repo.create.call_args[1]
        # key_hash must NOT be the plaintext key
        assert not call_kwargs['key_hash'].startswith('aa_')
        # key_prefix is first 8 chars of the key
        assert len(call_kwargs['key_prefix']) == 8


class TestValidateKey:
    def test_valid_key_returns_key_data(self, service, repo):
        repo.get_by_hash.return_value = {
            'id': 'key-uuid',
            'is_active': True,
            'expires_at': datetime.now(timezone.utc) + timedelta(days=30),
            'user_is_active': True,
            'user_id': 'user-uuid',
            'username': 'admin',
            'role': 'admin',
        }
        result = service.validate_key('aa_somekeyvalue1234567890abcdef1234567890')
        assert result is not None
        assert result['id'] == 'key-uuid'

    def test_inactive_key_returns_none(self, service, repo):
        repo.get_by_hash.return_value = {
            'id': 'k', 'is_active': False,
            'expires_at': datetime.now(timezone.utc) + timedelta(days=30),
            'user_is_active': True,
        }
        result = service.validate_key('aa_somekeyvalue1234567890abcdef1234567890')
        assert result is None

    def test_expired_key_returns_none(self, service, repo):
        repo.get_by_hash.return_value = {
            'id': 'k', 'is_active': True,
            'expires_at': datetime.now(timezone.utc) - timedelta(days=1),
            'user_is_active': True,
        }
        result = service.validate_key('aa_somekeyvalue1234567890abcdef1234567890')
        assert result is None

    def test_inactive_user_returns_none(self, service, repo):
        repo.get_by_hash.return_value = {
            'id': 'k', 'is_active': True,
            'expires_at': datetime.now(timezone.utc) + timedelta(days=30),
            'user_is_active': False,
        }
        result = service.validate_key('aa_somekeyvalue1234567890abcdef1234567890')
        assert result is None

    def test_unknown_key_returns_none(self, service, repo):
        repo.get_by_hash.return_value = None
        result = service.validate_key('aa_somekeyvalue1234567890abcdef1234567890')
        assert result is None


class TestListKeys:
    def test_returns_list(self, service, repo):
        repo.list_all.return_value = [{'id': 'k1'}, {'id': 'k2'}]
        result = service.list_keys()
        assert len(result) == 2



class TestDeleteKey:
    def test_delegates_to_repo(self, service, repo):
        repo.delete.return_value = True
        assert service.delete_key('k') is True
