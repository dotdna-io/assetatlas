# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unfinished chat setup must not be reported as a fault (#1104).

`get_effective_config` raises on a fresh install — before an admin has entered
an API key, which they could not have done before the install finished. The old
handler in `init_chat_service` caught that with a bare `except Exception` and
`logger.exception`, so every clean boot emitted an ERROR with a full traceback,
once per gunicorn worker, for a state that is entirely normal.

ERROR is the level operators alert on. These pin the distinction that fixes it:
"nobody configured this yet" is a WARNING, and anything else is still loud.
"""

import re
from pathlib import Path

import pytest

from src.api.blueprints.chat.admin_config.repository import ProviderRow
from src.api.blueprints.chat.admin_config.service import (
    ChatProvidersService,
    ProviderNotConfiguredError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _service(row):
    class _Repo:
        def get(self, provider):
            return row

    class _Settings:
        def get(self, *_a, **_k):
            return None

    return ChatProvidersService(_Repo(), _Settings())


def _row(provider="anthropic", **over):
    fields = dict(
        provider=provider, model="m", api_key_encrypted="", base_url="",
        prompt_cache=False, thinking_enabled=False,
    )
    fields.update(over)
    return ProviderRow(**fields)


def test_missing_api_key_raises_provider_not_configured():
    """The fresh-install case — the one that was logging an ERROR traceback."""
    with pytest.raises(ProviderNotConfiguredError):
        _service(_row()).get_effective_config("anthropic")


def test_missing_bedrock_region_raises_provider_not_configured():
    with pytest.raises(ProviderNotConfiguredError):
        _service(_row("aws_bedrock")).get_effective_config("aws_bedrock")


def test_missing_foundry_fields_raise_provider_not_configured():
    """Foundry validates in a separate helper, so it needs its own coverage —
    converting only the main function would leave a foundry install still
    logging a traceback on every boot."""
    with pytest.raises(ProviderNotConfiguredError):
        _service(_row("foundry")).get_effective_config("foundry")


def test_it_is_still_a_ValueError():
    """Several callers already catch ValueError. Narrowing the signal must not
    change anyone's control flow — that is why it subclasses rather than
    replaces."""
    assert issubclass(ProviderNotConfiguredError, ValueError)
    with pytest.raises(ValueError):
        _service(_row()).get_effective_config("anthropic")


def test_a_malformed_credential_blob_stays_a_plain_error():
    """The distinction has to cut both ways or it is worthless.

    Stored Bedrock credentials that cannot be parsed are a real fault — a
    corrupt or wrongly-encrypted value — not an unfinished form. If this also
    became ProviderNotConfiguredError it would be downgraded to a one-line
    WARNING and lose its traceback, which is the opposite of the intent.
    """
    row = _row("aws_bedrock", base_url="us-east-1", api_key_encrypted="not-json")
    with pytest.raises(ValueError) as exc:
        _service(row).get_effective_config("aws_bedrock")
    assert not isinstance(exc.value, ProviderNotConfiguredError)


def test_factory_handles_the_unconfigured_case_before_the_broad_except():
    """Ordering is the whole fix.

    `except Exception` placed first would swallow ProviderNotConfiguredError and
    restore the ERROR+traceback, with every test above still green — the failure
    would be invisible except in the logs, which is exactly how #1104 survived.
    Asserted against the real source because there is no way to observe log
    LEVEL from the exception classification alone.
    """
    src = (PROJECT_ROOT / "src" / "api" / "factory.py").read_text()
    body = src[src.index("def init_chat_service"):]
    body = body[: body.index("\ndef ")] if "\ndef " in body[1:] else body

    specific = body.find("except ProviderNotConfiguredError")
    broad = body.find("except Exception")
    assert specific != -1, "init_chat_service must handle ProviderNotConfiguredError"
    assert broad != -1, "the broad handler must remain for genuine faults"
    assert specific < broad, (
        "ProviderNotConfiguredError must be caught BEFORE `except Exception`, "
        "or it is swallowed and logged as an ERROR with a traceback again"
    )

    handler = body[specific:broad]
    assert re.search(r"logger\.warning", handler), (
        "the unconfigured case must log at WARNING"
    )
    assert not re.search(r"logger\.(exception|error)", handler), (
        "the unconfigured case must not log at ERROR or emit a traceback"
    )
