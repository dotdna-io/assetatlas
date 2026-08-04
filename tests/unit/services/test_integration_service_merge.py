# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for IntegrationService.merge_candidate_config.

This helper is what makes the wizard's edit-mode "Test Connection" honour
unsaved edits (e.g. a new SIEM query) without forcing the user to re-type
credentials they never see. The semantics matter enough to pin in unit
tests — getting them wrong either leaks stale config into the test (which
is the original bug) or sends placeholder/encrypted strings to the
connector as if they were real credentials.
"""

from src.modules.integrations.services.integration_service import IntegrationService
from src.modules.integrations.shared.encryption import encrypt


def test_merge_returns_saved_when_no_candidate():
    saved = {"url": "https://api.example.com", "api_key": "real-key"}
    assert IntegrationService.merge_candidate_config(None, saved) is saved


def test_candidate_overrides_non_sensitive_fields():
    """Editing the query/URL/limit must reach the connector."""
    candidate = {
        "url": "https://api.example.com",
        "powerquery": "dataSource.name='Sophos XGS' | filter model = *",
        "limit": 5000,
    }
    saved = {
        "url": "https://api.example.com",
        "powerquery": "old query",
        "limit": 100,
        "api_key": "real-key",
    }
    merged = IntegrationService.merge_candidate_config(candidate, saved)
    assert merged["powerquery"] == "dataSource.name='Sophos XGS' | filter model = *"
    assert merged["limit"] == 5000
    # Sensitive fields not in the candidate still come from saved.
    assert merged["api_key"] == "real-key"


def test_empty_string_sensitive_field_falls_back_to_saved():
    """The wizard often POSTs '' for masked credentials the user didn't touch."""
    candidate = {"url": "https://x", "api_key": ""}
    saved = {"url": "https://x", "api_key": "real-key"}
    merged = IntegrationService.merge_candidate_config(candidate, saved)
    assert merged["api_key"] == "real-key"


def test_missing_sensitive_field_falls_back_to_saved():
    candidate = {"url": "https://x"}
    saved = {"url": "https://x", "client_secret": "real-secret"}
    merged = IntegrationService.merge_candidate_config(candidate, saved)
    assert merged["client_secret"] == "real-secret"


def test_encrypted_sensitive_field_falls_back_to_saved():
    """Edit-mode loads the integration WITHOUT decrypting — so the wizard
    holds the Fernet token verbatim and posts it back. The merger must
    detect that and substitute the decrypted saved value."""
    plain = "real-secret"
    encrypted = encrypt(plain)
    candidate = {"url": "https://x", "client_secret": encrypted}
    saved = {"url": "https://x", "client_secret": plain}
    merged = IntegrationService.merge_candidate_config(candidate, saved)
    assert merged["client_secret"] == plain


def test_fresh_plaintext_sensitive_field_wins():
    """User typed a new credential — must reach the connector, not fall back."""
    candidate = {"url": "https://x", "api_key": "freshly-typed"}
    saved = {"url": "https://x", "api_key": "old-key"}
    merged = IntegrationService.merge_candidate_config(candidate, saved)
    assert merged["api_key"] == "freshly-typed"


def test_merge_does_not_mutate_candidate():
    candidate = {"url": "https://x", "api_key": ""}
    saved = {"url": "https://x", "api_key": "real"}
    IntegrationService.merge_candidate_config(candidate, saved)
    assert candidate["api_key"] == ""  # unchanged


def test_api_token_treated_as_sensitive():
    """api_token is the credential field used by every sentinelone_siem-backed
    integration (Sophos/Palo/Fortinet/etc. in production) and by direct SentinelOne,
    Ranger, and Juniper Mist. If it isn't in SENSITIVE_FIELDS the wizard's
    edit-mode merge silently drops it whenever the user doesn't re-type it,
    and the connector test runs unauthenticated."""
    candidate = {"url": "https://x", "powerquery": "new query"}
    saved = {"url": "https://x", "powerquery": "old", "api_token": "real-token"}
    merged = IntegrationService.merge_candidate_config(candidate, saved)
    assert merged["api_token"] == "real-token"
