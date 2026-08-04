# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bundle acquisition sources: uploaded bytes (air-gapped) or GitHub release fetch."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

GITHUB_API = 'https://api.github.com'
TAG_PREFIX = 'reference-bundle-'
HTTP_TIMEOUT = 30
USER_AGENT = 'AssetAtlas-ReferenceDataUpdater'


class UpstreamUnavailableError(RuntimeError):
    """GitHub API/asset download could not be reached."""


class BundleSource(ABC):
    @abstractmethod
    def label(self) -> str:
        """Return 'upload' or 'github' for audit/log purposes."""

    @abstractmethod
    def fetch(self) -> Tuple[bytes, str]:
        """Return (bundle_bytes, version_or_filename_for_logs)."""


class UploadedBytesSource(BundleSource):
    def __init__(self, data: bytes, uploaded_filename: str = ''):
        self._data = data
        self._filename = uploaded_filename

    def label(self) -> str:
        return 'upload'

    def fetch(self) -> Tuple[bytes, str]:
        return self._data, self._filename or 'uploaded.tar.gz'


def _github_get(url: str) -> dict:
    """GET a JSON resource from GitHub, raising UpstreamUnavailableError on failure."""
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': USER_AGENT,
    }
    try:
        resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.ConnectionError, requests.Timeout) as e:
        raise UpstreamUnavailableError(f"GitHub API unreachable: {e}") from e
    except requests.HTTPError as e:
        status = getattr(getattr(e, 'response', None), 'status_code', '?')
        body = getattr(getattr(e, 'response', None), 'text', '')[:200]
        raise UpstreamUnavailableError(f"GitHub API error {status}: {body}") from e


def _release_url(repo: str, version: str) -> str:
    return f'{GITHUB_API}/repos/{repo}/releases/tags/{TAG_PREFIX}{version}'


def _latest_bundle_release(repo: str) -> dict:
    """Newest release whose tag is actually a reference bundle.

    NOT /releases/latest. The bundle repo doubles as the public host for the
    installer artifacts (release.yml publishes assetatlas-online-<ver>.sh there),
    so "latest" is normally an installer release like v2026.30.14 — and the
    bundle fetch then died with a baffling "release tag 'v2026.30.14' does not
    start with 'reference-bundle-'". That broke BOTH the installer's seeding step
    and the admin UI's Check for updates, on every deployment, while 17 perfectly
    good bundles sat one page down.

    GitHub returns releases newest-first, so the first prefix match wins.
    """
    releases = _github_get(f'{GITHUB_API}/repos/{repo}/releases?per_page=100')
    if not isinstance(releases, list):
        raise UpstreamUnavailableError(
            f"unexpected releases payload from {repo}: {type(releases).__name__}"
        )
    for release in releases:
        if str(release.get('tag_name', '')).startswith(TAG_PREFIX):
            return release
    raise UpstreamUnavailableError(
        f"no {TAG_PREFIX}* release in the 100 most recent releases of {repo}. "
        "If the repo also hosts frequent non-bundle releases, bundles may have "
        "been pushed off the first page."
    )


def _extract_version_and_asset_url(release: dict) -> Tuple[str, str]:
    tag = release.get('tag_name', '')
    if not tag.startswith(TAG_PREFIX):
        raise UpstreamUnavailableError(
            f"release tag {tag!r} does not start with {TAG_PREFIX!r}"
        )
    version = tag[len(TAG_PREFIX):]
    assets = release.get('assets') or []
    if not assets:
        raise UpstreamUnavailableError(f"release {tag} has no assets")
    asset = next((a for a in assets if a['name'].endswith('.tar.gz')), assets[0])
    url = asset.get('browser_download_url') or asset.get('url')
    if not url:
        raise UpstreamUnavailableError(f"release {tag} asset has no download URL")
    return version, url


def fetch_release_bytes(repo: str, version: Optional[str]) -> Tuple[bytes, str]:
    """Download the bundle tarball for `version` (or latest if None). Returns (bytes, version)."""
    release = (_github_get(_release_url(repo, version)) if version
               else _latest_bundle_release(repo))
    actual_version, asset_url = _extract_version_and_asset_url(release)
    try:
        dl = requests.get(asset_url, timeout=HTTP_TIMEOUT,
                          headers={'User-Agent': USER_AGENT})
        dl.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
        raise UpstreamUnavailableError(f"asset download failed: {e}") from e
    return dl.content, actual_version


class GitHubReleaseSource(BundleSource):
    """Fetch a release (latest if version=None) from a PUBLIC GitHub repo. No auth."""

    def __init__(self, repo: str, version: Optional[str] = None):
        self._repo = repo
        self._version = version

    def label(self) -> str:
        return 'github'

    def fetch(self) -> Tuple[bytes, str]:
        return fetch_release_bytes(self._repo, self._version)
