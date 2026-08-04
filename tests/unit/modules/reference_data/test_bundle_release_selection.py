# SPDX-License-Identifier: AGPL-3.0-or-later
"""Choosing WHICH upstream release is a reference bundle.

The bundle repo doubles as the public host for installer artifacts
(release.yml publishes assetatlas-online-<ver>.sh there), so
`/releases/latest` normally returns an installer release. Using it made both
the installer's seeding step and the admin UI's "Check for updates" fail on
every deployment with "release tag 'v2026.30.14' does not start with
'reference-bundle-'" — while 17 valid bundles sat one page down.

Observed on a real 2026.30.14 install: mac_vendor_ranges landed with 0 rows.
"""

from unittest.mock import patch

import pytest

from src.modules.reference_data import sources


def _rel(tag, assets=True):
    return {
        'tag_name': tag,
        'assets': [{'name': f'{tag}.tar.gz',
                    'browser_download_url': f'https://x/{tag}.tar.gz'}] if assets else [],
    }


def test_installer_releases_are_skipped_for_bundles():
    """The exact production failure: newest release is an installer artifact."""
    listing = [_rel('v2026.30.14'), _rel('v2026.30.13'),
               _rel('reference-bundle-2026.30.0526'), _rel('v2026.30.12')]
    with patch.object(sources, '_github_get', return_value=listing):
        assert sources._latest_bundle_release('o/r')['tag_name'] == 'reference-bundle-2026.30.0526'


def test_newest_bundle_wins_when_several_exist():
    """GitHub returns newest-first, so the first prefix match is the newest."""
    listing = [_rel('reference-bundle-2026.30.0526'), _rel('reference-bundle-2026.29.0101')]
    with patch.object(sources, '_github_get', return_value=listing):
        assert sources._latest_bundle_release('o/r')['tag_name'] == 'reference-bundle-2026.30.0526'


def test_no_bundle_in_listing_says_why():
    """A repo of nothing but installer releases must produce an actionable error,
    not the old misleading 'does not start with' message about one wrong release."""
    with patch.object(sources, '_github_get', return_value=[_rel('v1'), _rel('v2')]):
        with pytest.raises(sources.UpstreamUnavailableError, match='100 most recent'):
            sources._latest_bundle_release('o/r')


def test_unexpected_payload_is_rejected():
    with patch.object(sources, '_github_get', return_value={'message': 'Not Found'}):
        with pytest.raises(sources.UpstreamUnavailableError, match='unexpected releases payload'):
            sources._latest_bundle_release('o/r')


def test_latest_does_not_use_the_releases_latest_endpoint():
    """Regression guard on the root cause itself: /releases/latest is repo-wide
    and cannot distinguish a bundle from an installer artifact."""
    calls = []

    def spy(url):
        calls.append(url)
        return [_rel('reference-bundle-2026.30.0526')]

    with patch.object(sources, '_github_get', side_effect=spy):
        sources._latest_bundle_release('o/r')
    assert calls, 'no GitHub call made'
    assert not any(u.endswith('/releases/latest') for u in calls), calls


def test_pinned_version_still_addresses_the_tag_directly():
    """Rollback to a known-good bundle must not pay for a 100-release listing."""
    assert sources._release_url('o/r', '2026.30.0526').endswith(
        '/releases/tags/reference-bundle-2026.30.0526')
