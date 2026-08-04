# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pure parser for Microsoft Foundry endpoint URLs.

Three Foundry-exposed protocols. The parser splits a URL into
(sdk_base_url, api_version) so the openai-python / anthropic-python SDKs can
reconstruct the request URL: the SDK appends a protocol-specific suffix to
`base_url`, and any `api-version` is sent as a default query param.

Two URL shapes are accepted, because Foundry has offered both over time:
the older full request URL ending in the SDK suffix (…/chat/completions),
and the v1 API's bare base URL (…/openai/v1) which carries no suffix and
needs no api-version. See `parse_foundry_url` for how they're told apart.

Kept in its own module so the dispatch logic in `make_llm_client` stays
small and the validation rules are easy to unit-test in isolation.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


# Suffix the underlying SDK appends to `base_url` for each protocol.
# anthropic-python:  base_url + "/v1/messages"
# openai-python:     base_url + "/chat/completions"  (for chat.completions)
# openai-python:     base_url + "/responses"         (for responses.*)
_SUFFIX = {
    "anthropic": "/v1/messages",
    "openai_chat": "/chat/completions",
    "openai_responses": "/responses",
}


def parse_foundry_url(url: str, protocol: str) -> tuple[str, str | None]:
    """Split ``url`` into ``(sdk_base_url, api_version)``.

    Strips the SDK-appended suffix from the path when present (so
    ``base_url + suffix`` reconstructs the original), otherwise treats the URL
    as the bare base already. Lifts ``?api-version=…`` out of the query string
    when present; other query params are preserved on the returned base.

    Raises ``ValueError`` with a message suitable for surfacing to the admin
    when the URL is malformed for the requested protocol.
    """
    if protocol not in _SUFFIX:
        raise ValueError(f"Unknown Foundry protocol {protocol!r}")

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Target URL must start with http:// or https://")

    suffix = _SUFFIX[protocol]
    path = parsed.path
    if path.endswith(suffix):
        # Old-style full request URL (…/chat/completions): strip the suffix so
        # `base_url + suffix` reconstructs the original.
        path = path[:-len(suffix)]
    else:
        # Azure AI Foundry's v1 API (GA Aug 2025) hands out the bare base URL
        # (…/openai/v1) with no suffix; the SDK appends `suffix` itself, so
        # accept the path unchanged. Reject only a URL carrying a *different*
        # protocol's suffix — that means the wrong protocol was selected.
        foreign = next(
            (s for p, s in _SUFFIX.items()
             if p != protocol and path.endswith(s)),
            None,
        )
        if foreign:
            raise ValueError(
                f"Target URL path ends with {foreign!r}, not the {suffix!r} "
                f"expected for the {protocol!r} protocol. Pick the matching "
                f"protocol, or paste the base URL."
            )

    qs = parse_qs(parsed.query)
    api_version_list = qs.pop("api-version", None)
    api_version = api_version_list[0] if api_version_list else None

    new_query = urlencode(qs, doseq=True)
    base = urlunparse(
        (parsed.scheme, parsed.netloc, path, "", new_query, "")
    )
    return base.rstrip("/"), api_version
