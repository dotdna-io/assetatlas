# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for BridgeConfig.from_api() — bootstrap from env, fetch from API."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.teams_bridge.config import BridgeConfig


_VALID = {
    'enabled': True,
    'bot_app_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    'bot_app_password': 'secret',
    'bot_app_tenant_id': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
}


def _env(api_url='http://api:5001', api_key='K' * 48):
    return {
        'ASSETATLAS_API_URL': api_url,
        'ASSETATLAS_BRIDGE_API_KEY': api_key,
    }


class TestBridgeConfigFromApi:
    def test_active_when_enabled_and_complete(self):
        with patch.dict('os.environ', _env(), clear=True), \
             patch(
                 'src.teams_bridge.config._fetch_remote',
                 return_value=_VALID,
             ):
            cfg = BridgeConfig.from_api()
        assert cfg.is_active is True
        assert cfg.bot_app_id == _VALID['bot_app_id']
        assert cfg.bot_app_password == _VALID['bot_app_password']
        assert cfg.bot_app_tenant_id == _VALID['bot_app_tenant_id']

    def test_idle_when_disabled(self):
        with patch.dict('os.environ', _env(), clear=True), \
             patch(
                 'src.teams_bridge.config._fetch_remote',
                 return_value={**_VALID, 'enabled': False},
             ):
            cfg = BridgeConfig.from_api()
        assert cfg.is_active is False

    def test_idle_when_credential_empty(self):
        broken = {**_VALID, 'bot_app_id': ''}
        with patch.dict('os.environ', _env(), clear=True), \
             patch(
                 'src.teams_bridge.config._fetch_remote',
                 return_value=broken,
             ):
            cfg = BridgeConfig.from_api()
        assert cfg.is_active is False

    def test_idle_when_api_unreachable(self):
        with patch.dict('os.environ', _env(), clear=True), \
             patch(
                 'src.teams_bridge.config._fetch_remote',
                 side_effect=ConnectionError('refused'),
             ):
            cfg = BridgeConfig.from_api()
        # Idle, not crash. Bridge survives a temporary API outage.
        assert cfg.is_active is False
        assert cfg.enabled is False
        assert cfg.bot_app_id == ''

    def test_missing_required_env_raises(self):
        with patch.dict('os.environ', {}, clear=True):
            with pytest.raises(
                RuntimeError, match='ASSETATLAS_BRIDGE_API_KEY',
            ):
                BridgeConfig.from_api()

    def test_credential_tuple_helper(self):
        with patch.dict('os.environ', _env(), clear=True), \
             patch(
                 'src.teams_bridge.config._fetch_remote',
                 return_value=_VALID,
             ):
            cfg = BridgeConfig.from_api()
        assert cfg.credential_tuple() == (
            True, _VALID['bot_app_id'], _VALID['bot_app_password'],
            _VALID['bot_app_tenant_id'],
        )

    def test_refresh_interval_default(self):
        with patch.dict('os.environ', _env(), clear=True), \
             patch(
                 'src.teams_bridge.config._fetch_remote',
                 return_value=_VALID,
             ):
            cfg = BridgeConfig.from_api()
        assert cfg.refresh_interval_seconds == 30


class TestInsecureTransportWarning:
    def test_http_url_emits_warning_once(self, caplog, monkeypatch):
        # Reset the module-level dedupe set so the test is independent of order.
        from src.teams_bridge import config as cfg_mod
        monkeypatch.setattr(cfg_mod, '_INSECURE_TRANSPORT_WARNED', set())

        # Direct test: call _fetch_remote twice with a stub for urlopen.
        with caplog.at_level('WARNING'):
            with patch('urllib.request.urlopen') as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = b'{"data": {"enabled": false}}'
                cfg_mod._fetch_remote('http://api:5001', 'k' * 48)
                cfg_mod._fetch_remote('http://api:5001', 'k' * 48)

        warnings = [r for r in caplog.records
                    if 'plaintext HTTP' in r.getMessage()]
        assert len(warnings) == 1, f"expected exactly 1 warning, got {len(warnings)}"

    def test_https_url_emits_no_warning(self, caplog, monkeypatch):
        from src.teams_bridge import config as cfg_mod
        monkeypatch.setattr(cfg_mod, '_INSECURE_TRANSPORT_WARNED', set())

        with caplog.at_level('WARNING'):
            with patch('urllib.request.urlopen') as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = b'{"data": {"enabled": false}}'
                cfg_mod._fetch_remote('https://api.example.com', 'k' * 48)

        assert not [r for r in caplog.records
                    if 'plaintext HTTP' in r.getMessage()]
