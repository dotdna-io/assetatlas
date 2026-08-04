# SPDX-License-Identifier: AGPL-3.0-or-later
def test_bearer_token_is_in_sensitive_fields():
    from src.modules.integrations.repositories.integration_repository import (
        IntegrationRepository,
    )
    assert "bearer_token" in IntegrationRepository.SENSITIVE_FIELDS
