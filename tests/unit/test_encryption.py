# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for encryption utilities.

Tests encrypt/decrypt round-trips, ENC: marker detection,
process_value tuples, and config field helpers.
"""

import pytest

from src.modules.integrations.shared.encryption import (
    encrypt,
    decrypt,
    is_encrypted,
    process_value,
    encrypt_config_field,
    decrypt_config_field,
)


@pytest.fixture(autouse=True)
def _clear_encryption_key_cache():
    """`get_encryption_key` is `@lru_cache`d. Reset around every test so env-var
    monkeypatching takes effect and one test's cached key doesn't leak into
    the next."""
    from src.modules.integrations.shared.encryption import get_encryption_key
    get_encryption_key.cache_clear()
    yield
    get_encryption_key.cache_clear()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

class TestEncryptDecryptRoundTrip:
    """encrypt -> decrypt returns the original plaintext."""

    def test_round_trip_simple(self):
        original = "MySecretPassword123!"
        encrypted = encrypt(original)
        assert decrypt(encrypted) == original

    def test_round_trip_unicode(self):
        original = "parolaMea-cu-diacritice"
        assert decrypt(encrypt(original)) == original

    def test_round_trip_long_string(self):
        original = "x" * 10_000
        assert decrypt(encrypt(original)) == original

    def test_empty_string_returns_as_is(self):
        """encrypt('') and decrypt('') both return '' without touching Fernet."""
        assert encrypt("") == ""
        assert decrypt("") == ""

    def test_none_returns_none(self):
        """encrypt(None) and decrypt(None) should return None (falsy guard)."""
        assert encrypt(None) is None
        assert decrypt(None) is None


# ---------------------------------------------------------------------------
# ENC: marker
# ---------------------------------------------------------------------------

class TestEncryptedMarker:
    """Encrypted values must carry the ENC: prefix."""

    def test_encrypted_value_starts_with_enc(self):
        encrypted = encrypt("secret")
        assert encrypted.startswith("ENC:")

    def test_double_encrypt_is_noop(self):
        """Encrypting an already-encrypted value returns it unchanged."""
        encrypted = encrypt("secret")
        double_encrypted = encrypt(encrypted)
        assert double_encrypted == encrypted


# ---------------------------------------------------------------------------
# is_encrypted
# ---------------------------------------------------------------------------

class TestIsEncrypted:

    def test_encrypted_value_detected(self):
        assert is_encrypted(encrypt("hello")) is True

    def test_plaintext_not_detected(self):
        assert is_encrypted("hello") is False

    def test_empty_string(self):
        assert is_encrypted("") is False

    def test_none_value(self):
        assert is_encrypted(None) is False

    def test_enc_prefix_only(self):
        assert is_encrypted("ENC:") is True


# ---------------------------------------------------------------------------
# process_value
# ---------------------------------------------------------------------------

class TestProcessValue:

    def test_encrypted_value_returns_decrypted_and_true(self):
        encrypted = encrypt("secret")
        plaintext, was_encrypted = process_value(encrypted)
        assert plaintext == "secret"
        assert was_encrypted is True

    def test_plaintext_returns_same_and_false(self):
        plaintext, was_encrypted = process_value("plain")
        assert plaintext == "plain"
        assert was_encrypted is False

    def test_empty_string(self):
        plaintext, was_encrypted = process_value("")
        assert plaintext == ""
        assert was_encrypted is False

    def test_none_value(self):
        plaintext, was_encrypted = process_value(None)
        assert plaintext is None
        assert was_encrypted is False


# ---------------------------------------------------------------------------
# Config field helpers
# ---------------------------------------------------------------------------

class TestConfigFieldHelpers:

    def test_encrypt_config_field(self):
        config = {"username": "admin", "password": "secret"}
        result = encrypt_config_field(config, "password")
        assert result is config  # modifies in-place
        assert config["password"].startswith("ENC:")
        assert config["username"] == "admin"  # untouched

    def test_decrypt_config_field(self):
        config = {"password": encrypt("secret")}
        decrypt_config_field(config, "password")
        assert config["password"] == "secret"

    def test_encrypt_missing_field_is_noop(self):
        config = {"username": "admin"}
        encrypt_config_field(config, "password")
        assert "password" not in config

    def test_decrypt_missing_field_is_noop(self):
        config = {"username": "admin"}
        decrypt_config_field(config, "password")
        assert "password" not in config

    def test_encrypt_empty_field_is_noop(self):
        config = {"password": ""}
        encrypt_config_field(config, "password")
        assert config["password"] == ""

    def test_decrypt_plaintext_field_is_noop(self):
        """decrypt_config_field on a non-encrypted value leaves it alone."""
        config = {"password": "plain"}
        decrypt_config_field(config, "password")
        assert config["password"] == "plain"

    def test_round_trip_config_field(self):
        config = {"api_key": "key_12345"}
        encrypt_config_field(config, "api_key")
        decrypt_config_field(config, "api_key")
        assert config["api_key"] == "key_12345"


# ---------------------------------------------------------------------------
# Required env var (BLOCKER-20 of #596)
# ---------------------------------------------------------------------------

class TestRequiredEnvKey:
    """ASSETATLAS_ENCRYPTION_KEY is required in every environment.

    Earlier behavior fell back to a deterministic SHA-256 of a hard-coded
    salt when the env var was missing — which let anyone with the source
    decrypt every credential in dev/test databases. The fallback is removed.
    """

    def test_get_encryption_key_raises_when_env_missing(self, monkeypatch):
        """Missing env var must raise — no fallback."""
        monkeypatch.delenv("ASSETATLAS_ENCRYPTION_KEY", raising=False)

        from src.modules.integrations.shared.encryption import get_encryption_key

        with pytest.raises(ValueError, match="ASSETATLAS_ENCRYPTION_KEY"):
            get_encryption_key()

    def test_get_encryption_key_raises_even_without_flask_env(self, monkeypatch):
        """Removal applies regardless of FLASK_ENV — dev/test/prod all fail."""
        monkeypatch.delenv("ASSETATLAS_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)

        from src.modules.integrations.shared.encryption import get_encryption_key

        with pytest.raises(ValueError, match="ASSETATLAS_ENCRYPTION_KEY"):
            get_encryption_key()
