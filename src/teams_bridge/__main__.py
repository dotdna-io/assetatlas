# SPDX-License-Identifier: AGPL-3.0-or-later
"""`python -m src.teams_bridge` runs the bridge."""

from __future__ import annotations

import logging
import os
from typing import Any

from aiohttp import web

from src.teams_bridge.app import attach_refresh_loop, build_app
from src.teams_bridge.config import BridgeConfig


# A deliberate copy of src/modules/shared/log_levels.py, which this package
# cannot import: Dockerfile.teams-bridge:34 copies only src/teams_bridge/ into
# the image, so `from src.modules.shared.log_levels import ...` would resolve in
# dev and in the test suite and then raise ModuleNotFoundError at container
# start. Reach for the Dockerfile before reaching for the import. The two copies
# are held in step by tests/unit/teams_bridge/test_log_level_guard.py.
#
# An allow-list rather than `getattr(logging, level.upper(), logging.INFO)`,
# because the logging module carries public attributes that are not levels and
# getattr resolves those *successfully*: NOTSET -> 0, which silences the bridge,
# and BASIC_FORMAT -> a format *string*, which makes basicConfig(level=...)
# raise ValueError and the container fail to start. LOG_LEVEL is operator input
# (compose passes ${LOG_LEVEL:-INFO}), so both are one typo away.
_LEVELS_BY_NAME = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,      # alias the stdlib keeps for compatibility
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "FATAL": logging.CRITICAL,    # ditto
    "CRITICAL": logging.CRITICAL,
}


def _resolve_log_level(value: Any) -> int:
    """Resolve a configured level name to its logging level number.

    Matched case-insensitively with surrounding whitespace stripped. Anything
    else -- an unrecognised name, 'NOTSET', 'BASIC_FORMAT', a non-string, None
    -- yields INFO, so a bad setting leaves the bridge audible rather than
    silent or dead.
    """
    if isinstance(value, str):
        level = _LEVELS_BY_NAME.get(value.strip().upper())
        if level is not None:
            return level

    return logging.INFO


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=_resolve_log_level(level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    # Configure logging BEFORE config load so a missing env var produces
    # a formatted log line instead of a bare traceback.
    _setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    cfg = BridgeConfig.from_api()
    app = build_app(cfg)
    attach_refresh_loop(app)
    web.run_app(app, host=cfg.bind_host, port=cfg.bind_port)


if __name__ == "__main__":
    main()
