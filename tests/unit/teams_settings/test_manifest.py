# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the in-memory Teams manifest zip builder."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from src.api.utils.teams_manifest import (
    ManifestBuildError,
    build_teams_manifest_zip,
    ORG_NAME,
)


_APP_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'


class TestManifestBuilder:
    def test_org_name_constant(self):
        assert ORG_NAME == 'DOTDNA'

    def test_zip_contains_manifest_and_two_icons(self):
        data = build_teams_manifest_zip(
            bot_app_id=_APP_ID,
            public_base_url='https://assetatlas.acme.com',
        )
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = set(zf.namelist())
        assert names == {'manifest.json', 'color.png', 'outline.png'}

    def test_manifest_substitutes_app_id(self):
        data = build_teams_manifest_zip(
            bot_app_id=_APP_ID,
            public_base_url='https://assetatlas.acme.com',
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            manifest = json.loads(zf.read('manifest.json'))
        assert manifest['id'] == _APP_ID
        assert manifest['bots'][0]['botId'] == _APP_ID

    def test_manifest_developer_block_uses_public_url(self):
        data = build_teams_manifest_zip(
            bot_app_id=_APP_ID,
            public_base_url='https://assetatlas.acme.com',
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            manifest = json.loads(zf.read('manifest.json'))
        assert manifest['developer']['name'] == 'DOTDNA'
        assert manifest['developer']['websiteUrl'] == (
            'https://assetatlas.acme.com'
        )
        assert manifest['developer']['privacyUrl'] == (
            'https://assetatlas.acme.com/privacy'
        )
        assert manifest['developer']['termsOfUseUrl'] == (
            'https://assetatlas.acme.com/terms'
        )

    def test_manifest_valid_domains_uses_hostname(self):
        data = build_teams_manifest_zip(
            bot_app_id=_APP_ID,
            public_base_url='https://assetatlas.acme.com',
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            manifest = json.loads(zf.read('manifest.json'))
        assert 'assetatlas.acme.com' in manifest['validDomains']

    def test_strips_trailing_slash_and_path(self):
        data = build_teams_manifest_zip(
            bot_app_id=_APP_ID,
            public_base_url='https://shared.example.com/assetatlas/',
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            manifest = json.loads(zf.read('manifest.json'))
        # websiteUrl/path keep the path:
        assert manifest['developer']['websiteUrl'] == (
            'https://shared.example.com/assetatlas'
        )
        # validDomains hostname is just the host:
        assert 'shared.example.com' in manifest['validDomains']

    def test_rejects_http_public_url(self):
        with pytest.raises(ManifestBuildError, match='HTTPS'):
            build_teams_manifest_zip(
                bot_app_id=_APP_ID,
                public_base_url='http://localhost:5001',
            )

    def test_rejects_missing_app_id(self):
        with pytest.raises(ManifestBuildError, match='bot_app_id'):
            build_teams_manifest_zip(
                bot_app_id='',
                public_base_url='https://assetatlas.acme.com',
            )

    def test_no_unsubstituted_placeholders_in_manifest(self):
        # Defensive: ensure no `{{...}}` tokens remain in the rendered manifest.
        data = build_teams_manifest_zip(
            bot_app_id=_APP_ID,
            public_base_url='https://assetatlas.acme.com',
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            raw = zf.read('manifest.json').decode('utf-8')
        assert '{{' not in raw
        assert '}}' not in raw
