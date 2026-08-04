# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for UserRepository — uses unittest.mock for cursor."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.auth.repositories.user_repository import UserRepository


class TestGetOrCreateLdapUser:
    """get_or_create_ldap_user provisions or matches an LDAP-authenticated user."""

    UNUSABLE_PREFIX = 'LDAP:'

    def test_creates_new_user_with_unusable_password(self, mock_conn_manager):
        cm, cursor, conn = mock_conn_manager
        # First lookup misses; insert returns id; second lookup succeeds
        cursor.fetchone.side_effect = [
            None,  # lookup by username
            None,  # lookup by email
            {'id': 'new-uuid'},  # insert RETURNING id
            {'id': 'new-uuid', 'username': 'alice', 'email': 'alice@corp.com', 'role': 'admin'},
        ]
        repo = UserRepository(cm)
        result = repo.get_or_create_ldap_user(
            username='alice',
            email='alice@corp.com',
            full_name='Alice Smith',
            role='admin',
        )
        assert result['username'] == 'alice'
        assert result['role'] == 'admin'
        # Verify INSERT call had LDAP: prefix
        insert_call = next(c for c in cursor.execute.call_args_list if 'INSERT INTO users' in c.args[0])
        assert insert_call.args[1]['password_hash'].startswith('LDAP:')

    def test_provisioning_emits_user_created_audit(self, mock_conn_manager):
        """A newly LDAP-provisioned account must emit a user_created
        auth audit event carrying source='ldap' + the assigned role. SSO
        first-login provisioning is the primary production account-creation
        vector; without this event the seeded auth-user-created alert never
        sees those accounts (#826)."""
        cm, cursor, _ = mock_conn_manager
        cursor.fetchone.side_effect = [
            None,                  # username lookup miss
            None,                  # email lookup miss
            {'id': 'new-uuid'},    # insert RETURNING id
            {'id': 'new-uuid', 'username': 'alice', 'email': 'alice@corp.com', 'role': 'admin'},
        ]
        repo = UserRepository(cm)
        # The repo does a function-local `from ...audit_service import AuditService`,
        # so patch it at the source module (resolved at call time).
        with patch('src.auth.services.audit_service.AuditService') as AS:
            repo.get_or_create_ldap_user(
                username='alice', email='alice@corp.com',
                full_name='Alice Smith', role='admin',
                ip='10.1.2.3', user_agent='agent/1.0',
            )
        AS.return_value.log_auth.assert_called_once()
        kwargs = AS.return_value.log_auth.call_args.kwargs
        assert kwargs['action'] == 'user_created'
        assert kwargs['success'] is True
        assert kwargs['username'] == 'alice'
        assert kwargs['user_id'] == 'new-uuid'
        assert kwargs['metadata'] == {'source': 'ldap', 'role': 'admin'}
        # Source IP/UA must ride along so an SSO-provisioned account carries the
        # same source detail as its sibling login_ldap event (audit fidelity).
        assert kwargs['ip'] == '10.1.2.3'
        assert kwargs['user_agent'] == 'agent/1.0'

    def test_matched_existing_user_emits_no_audit(self, mock_conn_manager):
        """Matching an already-provisioned user is not an account creation, so it
        must NOT emit a user_created event — only true first-login provisioning
        is audited (guards against false 'account created' alerts on every SSO
        login)."""
        cm, cursor, _ = mock_conn_manager
        existing = {
            'id': 'existing', 'username': 'alice', 'email': 'a@a.com',
            'role': 'user', 'is_active': True,
        }
        cursor.fetchone.side_effect = [existing]  # username lookup hits
        repo = UserRepository(cm)
        with patch('src.auth.services.audit_service.AuditService') as AS:
            repo.get_or_create_ldap_user('alice', 'a@a.com', 'Alice', 'admin')
        AS.return_value.log_auth.assert_not_called()

    def test_matches_existing_user_by_username(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        existing = {
            'id': 'existing', 'username': 'alice', 'email': 'a@a.com',
            'role': 'user', 'is_active': True,
        }
        cursor.fetchone.side_effect = [existing]  # username lookup hits
        repo = UserRepository(cm)
        result = repo.get_or_create_ldap_user('alice', 'a@a.com', 'Alice', 'admin')
        assert result['id'] == 'existing'
        # Existing user is returned as-is — no role update
        assert result['role'] == 'user'

    def test_matches_existing_user_by_email_when_username_misses(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        existing = {
            'id': 'existing', 'username': 'olduser', 'email': 'a@a.com',
            'role': 'user', 'is_active': True,
        }
        cursor.fetchone.side_effect = [None, existing]  # username miss, email hit
        repo = UserRepository(cm)
        result = repo.get_or_create_ldap_user('alice', 'a@a.com', 'Alice', 'admin')
        assert result['id'] == 'existing'

    def test_rejects_inactive_existing_user(self, mock_conn_manager):
        """An existing inactive user blocks LDAP login — admin must re-activate."""
        cm, cursor, _ = mock_conn_manager
        existing = {
            'id': 'existing', 'username': 'alice', 'email': 'a@a.com',
            'role': 'user', 'is_active': False,
        }
        cursor.fetchone.side_effect = [existing]
        repo = UserRepository(cm)
        with pytest.raises(ValueError, match='inactive'):
            repo.get_or_create_ldap_user('alice', 'a@a.com', 'Alice', 'user')

    def test_concurrent_insert_race_returns_existing_user(self, mock_conn_manager):
        """Two simultaneous first-logins for the same user — the loser of the race
        must catch IntegrityError and return the row the winner just created,
        not bubble a 500 to the user."""
        from psycopg2 import IntegrityError
        cm, cursor, _ = mock_conn_manager

        # fetchone sequence:
        #   1: username miss (initial check)
        #   2: email miss
        #   3: post-IntegrityError refetch by username — race winner's row
        existing = {
            'id': 'race-winner', 'username': 'alice', 'email': 'a@a.com',
            'role': 'user', 'is_active': True,
        }
        cursor.fetchone.side_effect = [None, None, existing]

        # execute sequence: 2 SELECTs return None (the SELECTs don't actually
        # need a return value — they just need not to raise), then INSERT raises
        # IntegrityError, then 1 SELECT for the refetch.
        cursor.execute.side_effect = [
            None,
            None,
            IntegrityError('duplicate key value violates unique constraint'),
            None,
        ]

        repo = UserRepository(cm)
        result = repo.get_or_create_ldap_user('alice', 'a@a.com', 'Alice', 'user')
        assert result['id'] == 'race-winner'


class TestGetOrCreateOidcUser:
    """get_or_create_oidc_user provisions or matches an OIDC-authenticated user."""

    def test_creates_new_user_with_unusable_oidc_password(self, mock_conn_manager):
        cm, cursor, conn = mock_conn_manager
        cursor.fetchone.side_effect = [
            None,  # email lookup miss (OIDC matches by email primarily)
            None,  # username collision check: 'alice' is free
            {'id': 'new-uuid'},  # insert RETURNING id
            {'id': 'new-uuid', 'username': 'alice', 'email': 'alice@corp.com', 'role': 'admin', 'is_active': True},
        ]
        repo = UserRepository(cm)
        result = repo.get_or_create_oidc_user(
            email='alice@corp.com',
            full_name='Alice Smith',
            role='admin',
        )
        assert result['email'] == 'alice@corp.com'
        assert result['role'] == 'admin'
        insert_call = next(c for c in cursor.execute.call_args_list if 'INSERT INTO users' in c.args[0])
        assert insert_call.args[1]['password_hash'].startswith('OIDC:')

    def test_provisioning_emits_user_created_audit(self, mock_conn_manager):
        """A newly OIDC-provisioned account must emit a user_created
        auth audit event carrying source='oidc' + the assigned role, so
        IdP-driven account creation is visible to the seeded auth alert
        rules (#826)."""
        cm, cursor, _ = mock_conn_manager
        cursor.fetchone.side_effect = [
            None,                  # email lookup miss (OIDC matches by email)
            None,                  # username collision check: 'alice' is free
            {'id': 'new-uuid'},    # insert RETURNING id
            {'id': 'new-uuid', 'username': 'alice', 'email': 'alice@corp.com',
             'role': 'admin', 'is_active': True},
        ]
        repo = UserRepository(cm)
        with patch('src.auth.services.audit_service.AuditService') as AS:
            repo.get_or_create_oidc_user(
                email='alice@corp.com', full_name='Alice Smith', role='admin',
                ip='10.1.2.3', user_agent='agent/1.0',
            )
        AS.return_value.log_auth.assert_called_once()
        kwargs = AS.return_value.log_auth.call_args.kwargs
        assert kwargs['action'] == 'user_created'
        assert kwargs['success'] is True
        assert kwargs['username'] == 'alice'
        assert kwargs['metadata'] == {'source': 'oidc', 'role': 'admin'}
        assert kwargs['ip'] == '10.1.2.3'
        assert kwargs['user_agent'] == 'agent/1.0'

    def test_matches_existing_user_by_email(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        existing = {
            'id': 'existing', 'username': 'olduser', 'email': 'a@a.com',
            'role': 'user', 'is_active': True,
        }
        cursor.fetchone.side_effect = [existing]
        repo = UserRepository(cm)
        result = repo.get_or_create_oidc_user('a@a.com', 'Alice', 'admin')
        assert result['id'] == 'existing'
        assert result['role'] == 'user'  # existing role preserved

    def test_rejects_inactive_existing_user(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.fetchone.side_effect = [{
            'id': 'existing', 'username': 'olduser', 'email': 'a@a.com',
            'role': 'user', 'is_active': False,
        }]
        repo = UserRepository(cm)
        with pytest.raises(ValueError, match='inactive'):
            repo.get_or_create_oidc_user('a@a.com', 'Alice', 'user')

    def test_username_derived_from_email_local_part(self, mock_conn_manager):
        """OIDC users get username='alice' from email='alice@corp.com'."""
        cm, cursor, _ = mock_conn_manager
        cursor.fetchone.side_effect = [
            None,  # email lookup miss
            None,  # username collision check: 'alice' is free
            {'id': 'new-uuid'},  # insert RETURNING id
            {'id': 'new-uuid', 'username': 'alice', 'email': 'alice@corp.com', 'role': 'user', 'is_active': True},
        ]
        repo = UserRepository(cm)
        repo.get_or_create_oidc_user('alice@corp.com', 'Alice', 'user')
        insert_call = next(c for c in cursor.execute.call_args_list if 'INSERT INTO users' in c.args[0])
        assert insert_call.args[1]['username'] == 'alice'

    def test_concurrent_insert_race_returns_existing_user(self, mock_conn_manager):
        """Two simultaneous OIDC first-logins for the same email — the loser of
        the race must catch IntegrityError and return the existing row."""
        from psycopg2 import IntegrityError
        cm, cursor, _ = mock_conn_manager

        existing = {
            'id': 'race-winner', 'username': 'alice', 'email': 'a@a.com',
            'role': 'user', 'is_active': True,
        }
        # fetchone sequence:
        #   1: email lookup miss (initial check)
        #   2: username 'alice' free (collision-suffix loop check)
        #   3: post-IntegrityError refetch by email — race winner's row
        cursor.fetchone.side_effect = [None, None, existing]

        # execute sequence: 2 SELECTs, then INSERT raises, then the refetch SELECT
        cursor.execute.side_effect = [
            None,
            None,
            IntegrityError('duplicate key value violates unique constraint'),
            None,
        ]

        repo = UserRepository(cm)
        result = repo.get_or_create_oidc_user('a@a.com', 'Alice', 'user')
        assert result['id'] == 'race-winner'


class TestIsExternalUserHelper:
    def test_is_oidc_user_true_for_oidc_prefix(self):
        assert UserRepository.is_oidc_user({'password_hash': 'OIDC:xyz'}) is True
        assert UserRepository.is_oidc_user({'password_hash': 'LDAP:abc'}) is False
        assert UserRepository.is_oidc_user({'password_hash': '$2b$bcrypt'}) is False
        assert UserRepository.is_oidc_user({}) is False

    def test_is_external_user_true_for_either_prefix(self):
        assert UserRepository.is_external_user({'password_hash': 'OIDC:xyz'}) is True
        assert UserRepository.is_external_user({'password_hash': 'LDAP:abc'}) is True
        assert UserRepository.is_external_user({'password_hash': '$2b$bcrypt'}) is False


class TestOnboardingPendingFlag:
    """`onboarding_pending` is derived in SQL from the INVITE: sentinel that
    InviteService writes onto password_hash. The Settings UI uses it to gate
    the "Send Invite" menu, and the resend-invite endpoint refuses requests
    against onboarded users. Lock the SQL contract so a query-rewrite cannot
    silently strip the flag."""

    INVITE_USER_ID = "11111111-1111-1111-1111-111111111111"

    def test_constant_matches_invite_service_sentinel(self):
        from src.auth.services.invite_service import InviteService
        assert (
            UserRepository.UNUSABLE_HASH_PREFIX_INVITE
            == InviteService.UNUSABLE_HASH_PREFIX
        )

    def test_get_user_by_id_selects_onboarding_pending(self, mock_conn_manager):
        from uuid import UUID
        cm, cursor, _ = mock_conn_manager
        cursor.fetchone.return_value = {"id": self.INVITE_USER_ID}

        UserRepository(cm).get_user_by_id(UUID(self.INVITE_USER_ID))

        executed_sql = cursor.execute.call_args[0][0]
        assert "AS onboarding_pending" in executed_sql
        params = cursor.execute.call_args[0][1]
        assert params["invite_prefix"] == "INVITE:%"

    def test_list_users_selects_onboarding_pending(self, mock_conn_manager):
        cm, cursor, _ = mock_conn_manager
        cursor.fetchone.side_effect = [{"total": 0}]
        cursor.fetchall.return_value = []

        UserRepository(cm).list_users(page=1, limit=10)

        # 2nd execute is the users query (1st is the count)
        users_sql = cursor.execute.call_args_list[1][0][0]
        assert "AS onboarding_pending" in users_sql
        users_params = cursor.execute.call_args_list[1][0][1]
        assert users_params["invite_prefix"] == "INVITE:%"


class TestIsAccountLocked:
    """is_account_locked must accept BOTH naive (test fixtures) and TZ-aware
    datetimes (psycopg2 returns from PG TIMESTAMP WITH TIME ZONE) — comparing
    a naive utcnow() against an aware locked_until raises TypeError, which
    surfaces as a 500 instead of a clean 401."""

    def _repo(self):
        return UserRepository(MagicMock())

    def test_no_locked_until_returns_false(self):
        assert self._repo().is_account_locked({'locked_until': None}) is False
        assert self._repo().is_account_locked({}) is False

    def test_naive_locked_until_in_future_returns_true(self):
        future = datetime.utcnow() + timedelta(minutes=10)
        assert self._repo().is_account_locked({'locked_until': future}) is True

    def test_naive_locked_until_in_past_returns_false(self):
        past = datetime.utcnow() - timedelta(minutes=10)
        assert self._repo().is_account_locked({'locked_until': past}) is False

    def test_tzaware_locked_until_in_future_returns_true(self):
        # psycopg2 returns TZ-aware datetimes from TIMESTAMP WITH TIME ZONE columns.
        # The pre-fix inline check (`locked_until > datetime.utcnow()`) raised TypeError.
        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        assert self._repo().is_account_locked({'locked_until': future}) is True

    def test_tzaware_locked_until_in_past_returns_false(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=10)
        assert self._repo().is_account_locked({'locked_until': past}) is False


class TestUpdatePassword:
    """update_password must reset password_changed_at AND clear must_change_password."""

    def test_update_password_resets_changed_at_and_clears_flag(self, mock_conn_manager):
        from uuid import UUID

        cm, cursor, conn = mock_conn_manager
        repo = UserRepository(cm)
        user_id = UUID("11111111-1111-1111-1111-111111111111")

        repo.update_password(user_id, "$2b$12$newhash")

        executed_sql = cursor.execute.call_args[0][0]
        assert "password_changed_at = NOW()" in executed_sql
        assert "must_change_password = false" in executed_sql
        assert "password_hash = %(password_hash)s" in executed_sql


class TestMarkPasswordExpiredIfOld:
    """mark_password_expired_if_old — single-statement age-gated UPDATE.
    LDAP/OIDC stub-hash users are excluded; non-positive max_age_days
    short-circuits without touching the DB."""

    def test_returns_false_when_max_age_zero(self, mock_conn_manager):
        from uuid import UUID
        from src.auth.repositories.user_repository import UserRepository

        cm, cursor, _conn = mock_conn_manager
        repo = UserRepository(cm)
        result = repo.mark_password_expired_if_old(
            UUID("11111111-1111-1111-1111-111111111111"), 0
        )
        assert result is False
        cursor.execute.assert_not_called()

    def test_returns_false_when_max_age_negative(self, mock_conn_manager):
        from uuid import UUID
        from src.auth.repositories.user_repository import UserRepository

        cm, cursor, _conn = mock_conn_manager
        repo = UserRepository(cm)
        result = repo.mark_password_expired_if_old(
            UUID("11111111-1111-1111-1111-111111111111"), -1
        )
        assert result is False
        cursor.execute.assert_not_called()

    def test_executes_age_gated_update_and_returns_true_when_row_returned(self, mock_conn_manager):
        from uuid import UUID
        from src.auth.repositories.user_repository import UserRepository

        cm, cursor, _conn = mock_conn_manager
        cursor.fetchone.return_value = ("11111111-1111-1111-1111-111111111111",)

        repo = UserRepository(cm)
        result = repo.mark_password_expired_if_old(
            UUID("11111111-1111-1111-1111-111111111111"), 180
        )

        executed_sql = cursor.execute.call_args[0][0]
        assert "must_change_password = true" in executed_sql
        assert "password_changed_at" in executed_sql
        params = cursor.execute.call_args[0][1]
        assert params["days"] == 180
        assert params["ldap_prefix"] == "LDAP:%"
        assert params["oidc_prefix"] == "OIDC:%"
        assert result is True

    def test_returns_false_when_no_row_updated(self, mock_conn_manager):
        from uuid import UUID
        from src.auth.repositories.user_repository import UserRepository

        cm, cursor, _conn = mock_conn_manager
        cursor.fetchone.return_value = None

        repo = UserRepository(cm)
        result = repo.mark_password_expired_if_old(
            UUID("11111111-1111-1111-1111-111111111111"), 180
        )
        assert result is False
