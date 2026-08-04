# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the bridge's credential probe (POST /test-credentials)."""
from __future__ import annotations

import asyncio
import socket
from unittest.mock import MagicMock, patch

import pytest

from src.teams_bridge.credentials_check import probe_bot_credentials


def _run(coro):
    return asyncio.run(coro)


class TestProbe:
    @patch('src.teams_bridge.credentials_check.MicrosoftAppCredentials')
    def test_returns_ok_on_token_success(self, mock_creds_cls):
        instance = mock_creds_cls.return_value
        instance.get_access_token.return_value = 'token-x'

        result = _run(probe_bot_credentials('id', 'secret', 'tenant'))

        assert result == {'ok': True}
        # Confirm we constructed the credentials with the right kwargs.
        mock_creds_cls.assert_called_once_with(
            app_id='id', password='secret', channel_auth_tenant='tenant',
        )

    @patch('src.teams_bridge.credentials_check.MicrosoftAppCredentials')
    def test_returns_aadsts_error_message(self, mock_creds_cls):
        instance = mock_creds_cls.return_value
        instance.get_access_token.side_effect = Exception(
            'AADSTS7000215: Invalid client secret'
        )

        result = _run(probe_bot_credentials('id', 'secret', 'tenant'))

        assert result['ok'] is False
        assert 'AADSTS7000215' in result['error']

    @patch('src.teams_bridge.credentials_check.MicrosoftAppCredentials')
    def test_returns_unreachable_on_network_error(self, mock_creds_cls):
        instance = mock_creds_cls.return_value
        instance.get_access_token.side_effect = socket.gaierror(
            'name resolution failed',
        )

        result = _run(probe_bot_credentials('id', 'secret', 'tenant'))

        assert result == {'ok': False, 'error': 'unreachable'}

    @patch('src.teams_bridge.credentials_check.MicrosoftAppCredentials')
    def test_returns_unreachable_on_connection_error(self, mock_creds_cls):
        instance = mock_creds_cls.return_value
        instance.get_access_token.side_effect = ConnectionError('refused')

        result = _run(probe_bot_credentials('id', 'secret', 'tenant'))

        assert result == {'ok': False, 'error': 'unreachable'}

    @patch('src.teams_bridge.credentials_check.MicrosoftAppCredentials')
    def test_returns_unreachable_on_timeout_error(self, mock_creds_cls):
        instance = mock_creds_cls.return_value
        instance.get_access_token.side_effect = TimeoutError('timed out')

        result = _run(probe_bot_credentials('id', 'secret', 'tenant'))

        assert result == {'ok': False, 'error': 'unreachable'}

    @patch('src.teams_bridge.credentials_check.MicrosoftAppCredentials')
    def test_sanitizes_long_error_messages(self, mock_creds_cls):
        instance = mock_creds_cls.return_value
        instance.get_access_token.side_effect = Exception('x' * 1000)

        result = _run(probe_bot_credentials('id', 'secret', 'tenant'))

        assert result['ok'] is False
        # Sanitized to <=240 chars
        assert len(result['error']) <= 240


class TestRouteHandler:
    def test_route_returns_400_on_bad_json(self):
        async def _go():
            from aiohttp.test_utils import TestClient, TestServer
            from src.teams_bridge.app import build_app
            from src.teams_bridge.config import BridgeConfig
            cfg = BridgeConfig(
                assetatlas_api_url='http://api:5001',
                assetatlas_bridge_api_key='K' * 48,
                enabled=False, bot_app_id='', bot_app_password='',
                bot_app_tenant_id='',
            )
            app = build_app(cfg)
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    '/test-credentials',
                    data='not-json',
                    headers={'Content-Type': 'application/json'},
                )
                assert resp.status == 400
        asyncio.run(_go())

    def test_route_returns_200_ok_false_when_credentials_missing(self):
        async def _go():
            from aiohttp.test_utils import TestClient, TestServer
            from src.teams_bridge.app import build_app
            from src.teams_bridge.config import BridgeConfig
            cfg = BridgeConfig(
                assetatlas_api_url='http://api:5001',
                assetatlas_bridge_api_key='K' * 48,
                enabled=False, bot_app_id='', bot_app_password='',
                bot_app_tenant_id='',
            )
            app = build_app(cfg)
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    '/test-credentials',
                    json={'bot_app_id': 'only-id'},
                )
                assert resp.status == 200  # endpoint returns 200 with ok:false
                body = await resp.json()
                assert body == {
                    'ok': False,
                    'error': 'all three credentials are required',
                }
        asyncio.run(_go())

    @patch('src.teams_bridge.credentials_check.MicrosoftAppCredentials')
    def test_route_returns_ok_on_valid_creds(self, mock_creds_cls):
        async def _go():
            mock_creds_cls.return_value.get_access_token.return_value = 'tok'
            from aiohttp.test_utils import TestClient, TestServer
            from src.teams_bridge.app import build_app
            from src.teams_bridge.config import BridgeConfig
            cfg = BridgeConfig(
                assetatlas_api_url='http://api:5001',
                assetatlas_bridge_api_key='K' * 48,
                enabled=False, bot_app_id='', bot_app_password='',
                bot_app_tenant_id='',
            )
            app = build_app(cfg)
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    '/test-credentials',
                    json={
                        'bot_app_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                        'bot_app_password': 'secret',
                        'bot_app_tenant_id': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
                    },
                )
                assert resp.status == 200
                body = await resp.json()
                assert body == {'ok': True}
        asyncio.run(_go())
