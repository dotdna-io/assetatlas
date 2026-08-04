# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pure parser for user-typed commands."""

from __future__ import annotations

import enum
import re


class Command(enum.Enum):
    NORMAL = "normal"
    NEW = "new"


_NEW_PHRASES = re.compile(
    r"^\s*(/new|new\s+chat|start\s+over|start\s+a?\s*new\s+chat)\s*$",
    re.IGNORECASE,
)


def parse_command(text: str) -> Command:
    if _NEW_PHRASES.match(text or ""):
        return Command.NEW
    return Command.NORMAL
