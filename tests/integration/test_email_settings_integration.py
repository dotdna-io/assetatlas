# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration test for email settings.

Uses the session-scoped `db_connection` fixture from tests/conftest.py.
Applies migration 082 on first use, then exercises SettingsManager +
EmailService against the real DB. smtplib is mocked — this test never
opens a real SMTP socket.

Requires DATABASE_URL to point at a reachable Postgres (test default:
postgresql://test:test@localhost:5434/assetatlas_test).
"""
import pathlib
from unittest.mock import MagicMock, patch

import pytest

# Intentionally do NOT set ASSETATLAS_ENCRYPTION_KEY — the encryption module
# has a deterministic SHA-256 fallback derived from an internal salt, which
# is stable and sufficient for tests.

pytestmark = pytest.mark.integration

MIGRATION_PATH = (
    pathlib.Path(__file__).parent.parent.parent
    / 'src' / 'database' / 'migrations' / '082_add_email_settings.sql'
)


@pytest.fixture(scope='module')
def migrated_db(db_connection):
    """Apply migration 082 idempotently, then yield the connection.

    The migration uses ON CONFLICT DO NOTHING, so re-running is safe.
    """
    with db_connection.cursor() as cur:
        cur.execute(MIGRATION_PATH.read_text())
    db_connection.commit()
    yield db_connection


class _ConnectionAdapter:
    """Minimal connection_manager facade over a single psycopg2 connection.

    SettingsManager only calls .get_cursor(cursor_factory=...) which yields
    a (cursor, conn) tuple. This tiny adapter is enough to drive it inside
    a live-DB integration test without booting the whole app.
    """

    def __init__(self, conn):
        self._conn = conn

    def get_cursor(self, cursor_factory=None):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            cur = (
                self._conn.cursor(cursor_factory=cursor_factory)
                if cursor_factory else self._conn.cursor()
            )
            try:
                yield cur, self._conn
            finally:
                cur.close()

        return _cm()


def test_email_settings_round_trip(migrated_db):
    """Service-layer round-trip: write encrypted, read plaintext via EmailService."""
    from src.api.modules.settings import SettingsManager
    from src.auth.services.email_service import EmailService
    from src.modules.integrations.shared.encryption import decrypt, is_encrypted
    from src.api.blueprints.settings.category_hooks import EmailSettingsHooks

    cm = _ConnectionAdapter(migrated_db)
    sm = SettingsManager(cm)
    hooks = EmailSettingsHooks()

    # Route equivalent: validate + pre-store (encrypt), then sm.set().
    stored_value = hooks.pre_store('smtp_password', 'integration-secret')
    success, err = sm.set(
        category='email', key='smtp_password', value=stored_value,
        user_id='test-user', username='test-admin',
        reason='integration test', ip_address='127.0.0.1',
    )
    assert success, err

    # 1. The DB row is ENC:-prefixed.
    with migrated_db.cursor() as cur:
        cur.execute(
            "SELECT value FROM system_settings WHERE category='email' AND key='smtp_password'",
        )
        stored = cur.fetchone()[0]
    assert is_encrypted(stored)
    assert decrypt(stored) == 'integration-secret'

    # 2. EmailService loads it and _load_config returns decrypted plaintext.
    svc = EmailService(sm)
    cfg = svc._load_config()
    assert cfg.smtp_password == 'integration-secret'

    # 3. Simulate the route's post-hoc audit-redact UPDATE and confirm it took.
    with migrated_db.cursor() as cur:
        cur.execute(
            """
            UPDATE settings_audit_log
            SET old_value = '<redacted>', new_value = '<redacted>'
            WHERE id = (
                SELECT id FROM settings_audit_log
                WHERE category=%s AND key=%s
                ORDER BY created_at DESC, id DESC LIMIT 1
            )
            """,
            ('email', 'smtp_password'),
        )
    migrated_db.commit()

    with migrated_db.cursor() as cur:
        cur.execute(
            """
            SELECT old_value, new_value FROM settings_audit_log
            WHERE category='email' AND key='smtp_password'
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
        )
        row = cur.fetchone()
    assert row == ('<redacted>', '<redacted>')

    # Cleanup: wipe test-written rows so subsequent runs are clean.
    with migrated_db.cursor() as cur:
        cur.execute(
            "DELETE FROM settings_audit_log WHERE category='email' AND changed_by='test-user'",
        )
        cur.execute(
            "UPDATE system_settings SET value='' WHERE category='email' AND key='smtp_password'",
        )
    migrated_db.commit()


def test_email_service_send_test_with_mocked_smtp(migrated_db):
    """Confirm EmailService.send_test_email builds a valid message when SMTP is mocked."""
    from src.api.modules.settings import SettingsManager
    from src.auth.services.email_service import EmailService

    cm = _ConnectionAdapter(migrated_db)
    sm = SettingsManager(cm)

    # Configure minimal fields.
    for key, val in [
        ('enabled', True),
        ('smtp_host', 'smtp.example.com'),
        ('from_address', 'no-reply@example.com'),
    ]:
        ok, err = sm.set(
            category='email', key=key, value=val,
            user_id='test-user', username='test-admin',
            reason='integration test', ip_address='127.0.0.1',
        )
        assert ok, err

    svc = EmailService(sm)
    with patch('src.auth.services.email_service.smtplib.SMTP') as mock_smtp:
        inst = MagicMock()
        inst.__enter__ = MagicMock(return_value=inst)
        inst.__exit__ = MagicMock(return_value=False)
        mock_smtp.return_value = inst
        ok, err = svc.send_test_email('admin@example.com')
    assert ok is True, err
    assert mock_smtp.called

    # Cleanup.
    with migrated_db.cursor() as cur:
        cur.execute(
            "DELETE FROM settings_audit_log WHERE category='email' AND changed_by='test-user'",
        )
        cur.execute(
            "UPDATE system_settings SET value=default_value "
            "WHERE category='email' AND key IN ('enabled', 'smtp_host', 'from_address')",
        )
    migrated_db.commit()
