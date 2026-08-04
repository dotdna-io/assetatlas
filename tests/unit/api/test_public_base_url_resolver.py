# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for src.api.utils.public_base_url.resolve_public_base_url.

The resolver is the single source of truth for "where is AssetAtlas reachable
from?" — it ranks security.public_base_url (DB) over the
ASSETATLAS_PUBLIC_BASE_URL env var, which only seeds first-boot installs.
"""
from unittest.mock import MagicMock

import pytest

from src.api.utils.public_base_url import resolve_public_base_url


def _sm(value):
    """Return a MagicMock SettingsManager whose .get returns `value` for
    security.public_base_url and `default` for everything else."""
    sm = MagicMock()

    def _get(category, key, default=None):
        if (category, key) == ('security', 'public_base_url'):
            return value
        return default
    sm.get.side_effect = _get
    return sm


class TestResolveOrder:
    def test_db_value_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv('ASSETATLAS_PUBLIC_BASE_URL', 'https://env.example.com')
        assert resolve_public_base_url(_sm('https://db.example.com')) == (
            'https://db.example.com'
        )

    def test_falls_back_to_env_when_db_empty(self, monkeypatch):
        monkeypatch.setenv('ASSETATLAS_PUBLIC_BASE_URL', 'https://env.example.com')
        assert resolve_public_base_url(_sm('')) == 'https://env.example.com'

    def test_falls_back_to_env_when_db_whitespace(self, monkeypatch):
        monkeypatch.setenv('ASSETATLAS_PUBLIC_BASE_URL', 'https://env.example.com')
        assert resolve_public_base_url(_sm('   ')) == 'https://env.example.com'

    def test_returns_empty_when_both_unset(self, monkeypatch):
        monkeypatch.delenv('ASSETATLAS_PUBLIC_BASE_URL', raising=False)
        assert resolve_public_base_url(_sm('')) == ''

    def test_handles_none_settings_manager(self, monkeypatch):
        monkeypatch.setenv('ASSETATLAS_PUBLIC_BASE_URL', 'https://env.example.com')
        assert resolve_public_base_url(None) == 'https://env.example.com'


class TestNormalization:
    def test_strips_trailing_slash_from_db_value(self, monkeypatch):
        monkeypatch.delenv('ASSETATLAS_PUBLIC_BASE_URL', raising=False)
        assert resolve_public_base_url(_sm('https://x.example.com/')) == (
            'https://x.example.com'
        )

    def test_strips_trailing_slash_from_env_value(self, monkeypatch):
        monkeypatch.setenv('ASSETATLAS_PUBLIC_BASE_URL', 'https://x.example.com/')
        assert resolve_public_base_url(_sm('')) == 'https://x.example.com'

    def test_strips_surrounding_whitespace(self, monkeypatch):
        monkeypatch.delenv('ASSETATLAS_PUBLIC_BASE_URL', raising=False)
        assert resolve_public_base_url(_sm('  https://x.example.com  ')) == (
            'https://x.example.com'
        )


class TestResilience:
    def test_settings_manager_exception_falls_through_to_env(self, monkeypatch):
        # If the DB blip / cache miss raises, resolver must not propagate.
        sm = MagicMock()
        sm.get.side_effect = RuntimeError('db unreachable')
        monkeypatch.setenv('ASSETATLAS_PUBLIC_BASE_URL', 'https://env.example.com')
        assert resolve_public_base_url(sm) == 'https://env.example.com'

    def test_non_string_db_value_falls_through_to_env(self, monkeypatch):
        # Defensive: if someone shoves an int into the row somehow, don't blow up.
        monkeypatch.setenv('ASSETATLAS_PUBLIC_BASE_URL', 'https://env.example.com')
        assert resolve_public_base_url(_sm(12345)) == 'https://env.example.com'
