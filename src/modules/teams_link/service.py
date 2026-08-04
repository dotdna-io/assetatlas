# SPDX-License-Identifier: AGPL-3.0-or-later
"""TeamsLinkService.

Owns the link-token lifecycle: mint a short-lived signed JWT carrying the
Teams identity, verify it on confirmation, mark its JTI consumed so it
can't be replayed, and write the link row.

Design notes:
  - The link token is HS256-signed with a service secret distinct from
    the user JWT secret to keep blast radius small. The service refuses
    to start with a missing or weak secret.
  - JTI is a URL-safe random string. Used-JTI tracking goes through an
    injected `used_jti_store` so tests don't need a live DB.
  - On replay, verify_link_token raises LinkTokenError; the page renders
    a generic "expired or already used" error.
"""

from __future__ import annotations

import datetime as _dt
import logging
import secrets
from typing import Any, Callable, Dict, Optional, Tuple, Union
from urllib.parse import quote
from uuid import UUID

import jwt as pyjwt

from src.modules.teams_link.models import TeamsAccountLink
from src.modules.teams_link.repository import (TeamsLinkRepository,
                                                UsedJtiStore)

logger = logging.getLogger(__name__)

_MIN_SECRET_KEY_LENGTH = 32

# Accept either a static string (tests, simple wiring) or a zero-arg callable
# that resolves the URL on each mint. The callable form is what production
# uses so a runtime change to security.public_base_url propagates without
# restarting the API container.
PublicBaseUrlSource = Union[str, Callable[[], str]]


class LinkTokenError(Exception):
    """Raised when a link token is invalid, expired, or already consumed."""


class TeamsLinkService:
    def __init__(
        self,
        *,
        repo: TeamsLinkRepository,
        used_jti_store: UsedJtiStore,
        secret_key: str,
        public_base_url: PublicBaseUrlSource,
        token_ttl_seconds: int = 300,
    ):
        if not secret_key or len(secret_key) < _MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                "TEAMS_LINK_SECRET_KEY must be at least "
                f"{_MIN_SECRET_KEY_LENGTH} characters; got "
                f"{len(secret_key)}",
            )
        self.repo = repo
        self.used = used_jti_store
        self._secret = secret_key
        self._base_source = public_base_url
        self._ttl = token_ttl_seconds

    def _resolve_base(self) -> str:
        raw = (
            self._base_source()
            if callable(self._base_source)
            else self._base_source
        )
        return (raw or '').rstrip('/')

    def mint_link_token(
        self, *, aad_object_id: str, tenant_id: str, display_name: str,
    ) -> Tuple[str, _dt.datetime]:
        """Mint a single-use link token. Returns (url, expires_at)."""
        now = _dt.datetime.now(tz=_dt.timezone.utc)
        exp = now + _dt.timedelta(seconds=self._ttl)
        jti = secrets.token_urlsafe(16)
        claims = {
            "aad_object_id": aad_object_id,
            "tenant_id": tenant_id,
            "display_name": display_name,
            "jti": jti,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
            "purpose": "teams_link",
        }
        token = pyjwt.encode(claims, self._secret, algorithm="HS256")
        url = f"{self._resolve_base()}/teams/link?token={quote(token)}"
        return url, exp

    def verify_link_token(self, token: str) -> Dict[str, Any]:
        """Verify signature/expiry/single-use. Returns claims on success."""
        try:
            claims = pyjwt.decode(token, self._secret, algorithms=["HS256"])
        except pyjwt.PyJWTError as exc:
            raise LinkTokenError(f"invalid token: {exc}") from exc
        if claims.get("purpose") != "teams_link":
            raise LinkTokenError("wrong-purpose token")
        if not all(k in claims for k in ("aad_object_id", "tenant_id", "jti")):
            raise LinkTokenError("missing required claims")
        if self.used.is_used(claims["jti"]):
            raise LinkTokenError("token already consumed")
        return claims

    def consume_link_token(
        self, token: str, *, user_id: UUID, client_ip: Optional[str],
    ) -> Dict[str, Any]:
        """Verify, mark JTI used, write link row. Returns the verified claims."""
        claims = self.verify_link_token(token)
        # Race: mark-used BEFORE writing the link, so concurrent confirms
        # for the same token race-lose at the JTI check on the second one.
        if not self.used.mark_used(claims["jti"]):
            raise LinkTokenError("token already consumed")
        self.repo.upsert_link(
            aad_object_id=claims["aad_object_id"],
            tenant_id=claims["tenant_id"],
            user_id=user_id,
            linked_via_ip=client_ip,
        )
        return claims

    def find_link(
        self, aad_object_id: str, tenant_id: str,
    ) -> Optional[TeamsAccountLink]:
        """Lookup the link row for a Teams identity. Returns None if
        the user hasn't completed the linking flow yet."""
        return self.repo.find_link(aad_object_id, tenant_id)
