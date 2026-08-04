# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared Adaptive Card envelope helpers.

Both `artifacts.py` and `linking.py` build Adaptive Cards. The envelope
(type/version) lives here so a schema bump only happens in one place.

We omit `$schema` because some Teams clients trip on the URL reference
and fall back to the "card unsupported" placeholder instead of
rendering. Teams + the Adaptive Cards spec both treat $schema as
informational only; leaving it out is the recommended path.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

AC_VERSION = "1.5"


def adaptive_card(
    body: List[Dict[str, Any]],
    *,
    actions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    card: Dict[str, Any] = {
        "type": "AdaptiveCard",
        "version": AC_VERSION,
        "body": body,
    }
    if actions:
        card["actions"] = actions
    return card


def text_block(text: str, **opts) -> Dict[str, Any]:
    block = {"type": "TextBlock", "wrap": True, "text": text}
    block.update(opts)
    return block
