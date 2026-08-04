# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock, patch

import pytest

from src.modules.reference_data.sources import (
    UploadedBytesSource,
    GitHubReleaseSource,
    UpstreamUnavailableError,
    fetch_release_bytes,
)


def test_uploaded_bytes_source_returns_bytes_and_label():
    s = UploadedBytesSource(data=b'BUNDLE', uploaded_filename='b-2026.17.1.tar.gz')
    assert s.label() == 'upload'
    assert s.fetch() == (b'BUNDLE', 'b-2026.17.1.tar.gz')


@patch('src.modules.reference_data.sources.requests.get')
def test_github_source_fetches_latest(mock_get):
    # The listing deliberately leads with an INSTALLER release: the bundle repo
    # also hosts assetatlas-online-<ver>.sh, so this is what production looks
    # like. This test previously mocked a single bundle at /releases/latest,
    # which is why it passed while every real install failed.
    mock_get.side_effect = [
        MagicMock(status_code=200, json=lambda: [
            {'tag_name': 'v2026.30.14',
             'assets': [{'name': 'assetatlas-online-2026.30.14.sh',
                         'browser_download_url': 'https://github.com/.../installer.sh'}]},
            {'tag_name': 'reference-bundle-2026.17.2',
             'assets': [{'name': 'reference-bundle-2026.17.2.tar.gz',
                         'browser_download_url': 'https://github.com/.../bundle.tar.gz'}]},
        ], raise_for_status=lambda: None),
        MagicMock(status_code=200, content=b'BUNDLE',
                  raise_for_status=lambda: None),
    ]
    src = GitHubReleaseSource(repo='org/repo')
    data, version = src.fetch()
    assert data == b'BUNDLE'
    assert version == '2026.17.2'
    # Must LIST releases and filter, never /releases/latest — that endpoint is
    # repo-wide and cannot tell a bundle from an installer artifact.
    first = mock_get.call_args_list[0].args[0]
    assert '/releases?' in first and '/releases/latest' not in first, first


@patch('src.modules.reference_data.sources.requests.get')
def test_github_source_fetches_specific_version_for_rollback(mock_get):
    mock_get.side_effect = [
        MagicMock(status_code=200, json=lambda: {
            'tag_name': 'reference-bundle-2026.17.1',
            'assets': [{
                'name': 'reference-bundle-2026.17.1.tar.gz',
                'browser_download_url': 'https://github.com/.../old.tar.gz',
            }],
        }, raise_for_status=lambda: None),
        MagicMock(status_code=200, content=b'OLD_BUNDLE',
                  raise_for_status=lambda: None),
    ]
    src = GitHubReleaseSource(repo='org/repo', version='2026.17.1')
    data, version = src.fetch()
    assert data == b'OLD_BUNDLE'
    assert version == '2026.17.1'
    # First call should target the specific tag, not /releases/latest
    assert '/releases/tags/reference-bundle-2026.17.1' in mock_get.call_args_list[0].args[0]


@patch('src.modules.reference_data.sources.requests.get')
def test_github_source_raises_on_network_error(mock_get):
    import requests
    mock_get.side_effect = requests.ConnectionError('no route')
    src = GitHubReleaseSource(repo='org/repo')
    with pytest.raises(UpstreamUnavailableError):
        src.fetch()


@patch('src.modules.reference_data.sources.requests.get')
def test_fetch_release_bytes_helper(mock_get):
    mock_get.side_effect = [
        MagicMock(status_code=200, json=lambda: [
            {'tag_name': 'v9.9.9-installer', 'assets': []},
            {'tag_name': 'reference-bundle-9.9.9',
             'assets': [{'name': 'b.tar.gz', 'browser_download_url': 'https://x'}]},
        ], raise_for_status=lambda: None),
        MagicMock(status_code=200, content=b'X', raise_for_status=lambda: None),
    ]
    data, version = fetch_release_bytes('org/repo', version=None)
    assert data == b'X'
    assert version == '9.9.9'
