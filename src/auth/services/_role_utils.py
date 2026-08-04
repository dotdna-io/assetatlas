# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared role-mapping logic for external auth providers (LDAP, OIDC, ...)."""
import json
import logging
from typing import Any, Callable, List

logger = logging.getLogger(__name__)

ROLE_RANK = {'user': 0, 'admin': 1, 'super_admin': 2}


def resolve_role(
    groups: List[str],
    get_setting: Callable[[str, Any], Any],
    provider_label: str,
) -> str:
    """Pick the highest-rank role mapped from the user's groups.

    Args:
        groups: List of group identifiers from the IdP (DNs, UUIDs, etc.).
        get_setting: Callable taking (key, default) — typically ``self._get``.
        provider_label: For warning logs (e.g., 'LDAP', 'OIDC').

    Returns:
        Role name (one of ROLE_RANK keys), defaulting to default_role setting,
        falling back to 'user' if default_role is invalid.
    """
    mapping_raw = get_setting('group_to_role_mapping', {}) or {}
    if isinstance(mapping_raw, str):
        try:
            mapping_raw = json.loads(mapping_raw)
        except (json.JSONDecodeError, ValueError):
            mapping_raw = {}

    lc_mapping = {k.lower(): v for k, v in (mapping_raw or {}).items()}
    default_role = get_setting('default_role', 'user') or 'user'
    if default_role not in ROLE_RANK:
        logger.warning(
            "%s default_role %r not in {user, admin, super_admin}; falling back to 'user'",
            provider_label, default_role,
        )
        default_role = 'user'

    best_role = default_role
    best_rank = ROLE_RANK[default_role]
    for group in groups:
        mapped = lc_mapping.get(str(group).lower())
        if mapped and ROLE_RANK.get(mapped, -1) > best_rank:
            best_role = mapped
            best_rank = ROLE_RANK[mapped]
    return best_role
