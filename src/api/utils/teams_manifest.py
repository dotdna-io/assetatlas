# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the Teams sideload zip in-memory from settings + public URL.

Reads:
  - teams-app/manifest.template.json (string substitution, not jinja)
  - teams-app/icons/color.png + outline.png

Output: bytes of the zip (manifest.json + two icons).
"""
from __future__ import annotations

import io
import os
import zipfile
from typing import Final

ORG_NAME: Final[str] = 'DOTDNA'

# Manifest template + icons travel with the Python package so they're present
# inside the prod Docker image (which only COPYs src/, not the top-level
# teams-app/). The CI-only teams-app/build.sh script also reads these same
# files via a relative path, keeping a single source of truth.
_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'teams_manifest_assets',
)
_TEMPLATE = os.path.join(_ASSETS_DIR, 'manifest.template.json')
_ICON_DIR = os.path.join(_ASSETS_DIR, 'icons')


class ManifestBuildError(ValueError):
    """Raised when inputs are insufficient or invalid for manifest build."""


def build_teams_manifest_zip(
    *, bot_app_id: str, public_base_url: str,
) -> bytes:
    """Render the manifest template + bundle icons into an in-memory zip.

    Raises ManifestBuildError when:
      - bot_app_id is empty
      - public_base_url is not HTTPS (Teams rejects http:// in manifest)
    """
    if not bot_app_id:
        raise ManifestBuildError("bot_app_id must be set in Teams settings")
    if not public_base_url.lower().startswith('https://'):
        raise ManifestBuildError(
            "Public base URL must be HTTPS for Teams to accept the "
            "manifest. Update it in System -> Security."
        )

    base = public_base_url.rstrip('/')
    # Strip protocol + path → host only, for validDomains.
    host = base.split('://', 1)[1].split('/', 1)[0]

    with open(_TEMPLATE, encoding='utf-8') as fh:
        template = fh.read()

    rendered = (
        template
        .replace('{{TEAMS_BOT_APP_ID}}', bot_app_id)
        .replace('{{ORG_NAME}}', ORG_NAME)
        .replace('{{ASSETATLAS_PUBLIC_BASE_URL}}', base)
        .replace('{{ASSETATLAS_PUBLIC_HOSTNAME}}', host)
    )

    with open(os.path.join(_ICON_DIR, 'color.png'), 'rb') as fh:
        color_png = fh.read()
    with open(os.path.join(_ICON_DIR, 'outline.png'), 'rb') as fh:
        outline_png = fh.read()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', rendered)
        zf.writestr('color.png', color_png)
        zf.writestr('outline.png', outline_png)
    return buf.getvalue()
