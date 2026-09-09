# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ApiKeyService."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

from src.modules.api_keys.service import ApiKeyService, LAST_USED_STAMP_INTERVAL_S


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


class TestRecordUsageThrottle:
    def test_none_last_used_at_stamps(self, service, repo):
        """A key that was never stamped (NULL) always stamps."""
        service.record_usage('key-uuid', '10.1.2.3', None)
        repo.update_last_used.assert_called_once_with(
            'key-uuid', '10.1.2.3', stale_after_s=LAST_USED_STAMP_INTERVAL_S
        )

    def test_default_last_used_at_stamps(self, service, repo):
        """Callers that pass only (key_id, ip) keep stamping (backward compat)."""
        service.record_usage('key-uuid', '10.1.2.3')
        repo.update_last_used.assert_called_once()

    def test_fresh_stamp_skips_the_repository(self, service, repo):
        """Stamped 10 s ago: inside the 60 s window, zero SQL."""
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        service.record_usage('key-uuid', '10.1.2.3', fresh)
        repo.update_last_used.assert_not_called()

    def test_stale_stamp_writes(self, service, repo):
        """Stamped 120 s ago: outside the window, the UPDATE runs."""
        stale = datetime.now(timezone.utc) - timedelta(seconds=120)
        service.record_usage('key-uuid', '10.1.2.3', stale)
        repo.update_last_used.assert_called_once_with(
            'key-uuid', '10.1.2.3', stale_after_s=LAST_USED_STAMP_INTERVAL_S
        )

    def test_naive_datetime_is_read_as_utc(self, service, repo):
        """A naive stamp 10 s old must still skip, not crash or stamp."""
        fresh_naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10)
        service.record_usage('key-uuid', '10.1.2.3', fresh_naive)
        repo.update_last_used.assert_not_called()

    def test_naive_stale_datetime_still_stamps(self, service, repo):
        stale_naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=120)
        service.record_usage('key-uuid', '10.1.2.3', stale_naive)
        repo.update_last_used.assert_called_once()

    def test_interval_constant_drives_the_sql_interval(self, service, repo):
        """The one module constant feeds both the Python gate and the SQL."""
        assert LAST_USED_STAMP_INTERVAL_S == 60
        service.record_usage('key-uuid', '10.1.2.3', None)
        assert repo.update_last_used.call_args[1]['stale_after_s'] == 60
