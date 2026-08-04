# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Credential Encryption

Provides encryption/decryption for sensitive configuration values using Fernet (symmetric encryption).

Created: 2025-11-04
"""

import logging
import os
from functools import lru_cache
from cryptography.fernet import Fernet
from typing import Tuple

logger = logging.getLogger(__name__)

# Environment variable for encryption key
ENV_ENCRYPTION_KEY = "ASSETATLAS_ENCRYPTION_KEY"

# Marker to identify encrypted values
_ENCRYPTED_MARKER = "ENC:"


@lru_cache(maxsize=1)
def get_encryption_key() -> bytes:
    """
    Get the integration-credential encryption key from the environment.

    The key is REQUIRED in every environment (production, dev, tests). Earlier
    versions fell back to a deterministic SHA-256 of a hard-coded salt for
    non-production paths, which let anyone with the source decrypt every
    integration credential in dev/test databases — see BLOCKER-20 of the OSS
    release-readiness review (issue #596). The fallback is gone.

    Configure the key in .env (also see ProductionConfig.init_app and
    DevelopmentConfig.init_app, which validate at app boot for defense in
    depth).

    Returns:
        32-byte url-safe base64-encoded Fernet key (44 ASCII characters).
    """
    env_key = os.getenv(ENV_ENCRYPTION_KEY)
    if env_key:
        return env_key.encode()

    raise ValueError(
        f"{ENV_ENCRYPTION_KEY} must be set. "
        "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
    )


def encrypt(value: str) -> str:
    """
    Encrypt a value.

    Args:
        value: Plaintext value to encrypt

    Returns:
        Encrypted value with ENC: prefix

    Example:
        encrypted = encrypt("my_secret_password")
        # Returns: "ENC:gAAAAABh..."
    """
    if not value:
        return value

    # If already encrypted, return as-is
    if value.startswith(_ENCRYPTED_MARKER):
        return value

    # Get encryption key
    key = get_encryption_key()
    cipher = Fernet(key)

    # Encrypt
    encrypted_bytes = cipher.encrypt(value.encode())
    encrypted_str = encrypted_bytes.decode()

    # Add marker prefix
    return f"{_ENCRYPTED_MARKER}{encrypted_str}"


def decrypt(encrypted_value: str) -> str:
    """
    Decrypt an encrypted value.

    Args:
        encrypted_value: Encrypted value (with or without ENC: prefix)

    Returns:
        Decrypted plaintext value

    Raises:
        ValueError: If decryption fails

    Example:
        plaintext = decrypt("ENC:gAAAAABh...")
        # Returns: "my_secret_password"
    """
    if not encrypted_value:
        return encrypted_value

    # Remove marker prefix if present
    if encrypted_value.startswith(_ENCRYPTED_MARKER):
        encrypted_value = encrypted_value[len(_ENCRYPTED_MARKER):]

    # Get encryption key
    key = get_encryption_key()
    cipher = Fernet(key)

    try:
        # Decrypt
        decrypted_bytes = cipher.decrypt(encrypted_value.encode())
        return decrypted_bytes.decode()
    except Exception as e:
        raise ValueError(f"Failed to decrypt value: {e}")


def is_encrypted(value: str) -> bool:
    """
    Check if a value is encrypted.

    Args:
        value: Value to check

    Returns:
        True if value has ENC: prefix, False otherwise
    """
    return value.startswith(_ENCRYPTED_MARKER) if value else False


def process_value(value: str) -> Tuple[str, bool]:
    """
    Process a value - decrypt if encrypted, return as-is if plaintext.

    Args:
        value: Value (encrypted or plaintext)

    Returns:
        Tuple of (plaintext_value, was_encrypted)

    Example:
        plaintext, was_encrypted = process_value("ENC:gAAAAABh...")
        # Returns: ("my_secret_password", True)

        plaintext, was_encrypted = process_value("my_password")
        # Returns: ("my_password", False)
    """
    if not value:
        return value, False

    if is_encrypted(value):
        return decrypt(value), True
    else:
        return value, False


def encrypt_config_field(config: dict, field_name: str) -> dict:
    """
    Encrypt a specific field in a configuration dictionary.

    Modifies the config in-place and returns it.

    Args:
        config: Configuration dictionary
        field_name: Field name to encrypt

    Returns:
        Modified configuration dictionary

    Example:
        config = {'password': 'my_secret'}
        config = encrypt_config_field(config, 'password')
        # config = {'password': 'ENC:gAAAAABh...'}
    """
    if field_name in config and config[field_name]:
        config[field_name] = encrypt(config[field_name])
    return config


def decrypt_config_field(config: dict, field_name: str) -> dict:
    """
    Decrypt a specific field in a configuration dictionary.

    Modifies the config in-place and returns it.

    Args:
        config: Configuration dictionary
        field_name: Field name to decrypt

    Returns:
        Modified configuration dictionary

    Example:
        config = {'password': 'ENC:gAAAAABh...'}
        config = decrypt_config_field(config, 'password')
        # config = {'password': 'my_secret'}
    """
    if field_name in config and config[field_name]:
        if is_encrypted(config[field_name]):
            config[field_name] = decrypt(config[field_name])
    return config


def decrypt_config(config: dict, sensitive_fields: list = None) -> dict:
    """
    Decrypt all encrypted fields in a configuration dictionary.

    Args:
        config: Configuration dictionary
        sensitive_fields: List of field names that might be encrypted
                         (if None, checks all string values)

    Returns:
        Configuration dictionary with decrypted values

    Example:
        config = {
            'username': 'admin',
            'password': 'ENC:gAAAAABh...',
            'api_key': 'ENC:gBBBBBBh...'
        }
        decrypted = decrypt_config(config, ['password', 'api_key'])
    """
    decrypted_config = config.copy()

    if sensitive_fields:
        # Only decrypt specified fields
        for field in sensitive_fields:
            decrypt_config_field(decrypted_config, field)
    else:
        # Decrypt all encrypted string values
        for key, value in decrypted_config.items():
            if isinstance(value, str) and is_encrypted(value):
                decrypted_config[key] = decrypt(value)

    return decrypted_config


# Legacy aliases for backward compatibility
encode_value = encrypt
decode_value = decrypt
is_transformed = is_encrypted
handle_value = process_value


if __name__ == '__main__':
    # Simple test
    test_value = "MySecretPassword123!"

    print("Encryption Test")
    print("=" * 50)
    print(f"Original: {test_value}")

    encrypted = encrypt(test_value)
    print(f"Encrypted: {encrypted}")

    decrypted = decrypt(encrypted)
    print(f"Decrypted: {decrypted}")

    print(f"\nMatch: {test_value == decrypted}")
    print(f"Is encrypted: {is_encrypted(encrypted)}")
    print(f"Is plaintext encrypted: {is_encrypted(test_value)}")

    # Test with config
    print("\nConfig Test")
    print("=" * 50)
    config = {
        'username': 'admin',
        'password': 'super_secret',
        'api_key': 'key_12345'
    }
    print(f"Original config: {config}")

    # Encrypt sensitive fields
    encrypt_config_field(config, 'password')
    encrypt_config_field(config, 'api_key')
    print(f"Encrypted config: {config}")

    # Decrypt
    decrypted_config = decrypt_config(config, ['password', 'api_key'])
    print(f"Decrypted config: {decrypted_config}")
