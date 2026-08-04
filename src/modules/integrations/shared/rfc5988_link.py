# SPDX-License-Identifier: AGPL-3.0-or-later
"""RFC 5988 Link-header parser — minimal, only `rel="next"` support.

Used by CustomHttpConnector when `pagination_type == 'link'`.
"""
from __future__ import annotations

import re
from typing import Optional

# `<url>; param=value; param="value", <url>; ...`
_LINK_RE = re.compile(r'<([^>]+)>\s*;\s*([^,]+)')
_REL_NEXT_RE = re.compile(r"\brel\s*=\s*[\"']?next[\"']?\b", re.IGNORECASE)


def parse_next_link(link_header: Optional[str]) -> Optional[str]:
    """Extract the URL of the `rel="next"` entry from an RFC 5988 Link header.

    Returns None if the header is empty, malformed, or contains no rel=next.
    """
    if not link_header:
        return None
    for url, params in _LINK_RE.findall(link_header):
        if _REL_NEXT_RE.search(params):
            return url.strip()
    return None
