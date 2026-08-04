# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared resolver for the public AssetAtlas URL.

Resolution order:
  1. ``security.public_base_url`` setting (DB), if non-empty after strip.
  2. ``ASSETATLAS_PUBLIC_BASE_URL`` env var (bootstrap default for fresh
     installs that haven't set the DB row yet).
  3. Empty string — callers decide whether that's an error.

The DB value is the operational source of truth; the env var only seeds
first-boot behavior so an installer can set a sensible default before the
operator visits the Settings UI. Trailing slashes are stripped so callers
don't need to defend against them.
"""
from __future__ import annotations

import os
from typing import Any, Optional


def resolve_public_base_url(settings_manager: Optional[Any]) -> str:
    """Return the resolved public base URL with trailing slash stripped.

    Returns ``''`` if neither the setting nor the env var is configured;
    callers should treat that as "not yet configured" and either fall back
    to a relative path or raise their own user-facing error.
    """
    if settings_manager is not None:
        try:
            value = settings_manager.get('security', 'public_base_url', '')
        except Exception:  # pragma: no cover - defensive; cache miss / DB blip
            value = ''
        if isinstance(value, str) and value.strip():
            return value.strip().rstrip('/')

    env_value = os.environ.get('ASSETATLAS_PUBLIC_BASE_URL', '')
    return env_value.strip().rstrip('/')
