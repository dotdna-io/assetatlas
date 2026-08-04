# SPDX-License-Identifier: AGPL-3.0-or-later
"""AWS credential resolution for the AWS Bedrock chat provider.

Shared by:
- ``make_llm_client`` (chat/llm/__init__.py) — builds the bedrock-runtime
  client used for inference.
- ``_bedrock_control_client`` (chat/routes.py) — builds the bedrock control-
  plane client used by ``GET /admin/bedrock/models``.

Both call paths follow the same auth-mode dispatch (access_keys / assume_role)
and the same optional-fields shape (ExternalId, explicit base creds for
assume_role). Centralising here means one place to fix when assume-role
behaviour changes (e.g. ``DurationSeconds``, retry policy).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, TypedDict

from src.api.blueprints.chat.admin_config.models import BedrockAuthMode

import boto3
from botocore.config import Config as _BotoConfig
from botocore.exceptions import BotoCoreError, ClientError


class BedrockAuthDict(TypedDict, total=False):
    auth_mode: BedrockAuthMode
    access_key_id: str
    secret_access_key: str
    role_arn: str
    external_id: str
    base_access_key_id: str
    base_secret_access_key: str


# Bound boto3 socket waits so a hung AWS endpoint can't pin a worker thread
# or block the asyncio loop indefinitely on user disconnect. read_timeout is
# inter-chunk for streaming responses (Converse chunks arrive frequently, so
# 120s is generous); connect_timeout caps TCP handshake.
_BEDROCK_BOTO_CONFIG = _BotoConfig(read_timeout=120, connect_timeout=10)


def resolve_aws_credentials(
    region: str, *, auth: BedrockAuthDict, session_name_prefix: str,
) -> Dict[str, Any]:
    """Return the kwargs dict to pass to ``boto3.client`` for either the
    bedrock-runtime or bedrock (control plane) client.

    Always includes ``region_name`` and ``config`` (timeouts). For
    ``access_keys`` mode it adds ``aws_access_key_id`` and
    ``aws_secret_access_key``. For ``assume_role`` mode it performs the STS
    hop and adds the temporary credentials plus ``aws_session_token``."""
    auth_mode = auth.get("auth_mode")

    common: Dict[str, Any] = {"region_name": region, "config": _BEDROCK_BOTO_CONFIG}

    if auth_mode == "access_keys":
        access_key_id = auth.get("access_key_id") or ""
        secret = auth.get("secret_access_key") or ""
        if not access_key_id or not secret:
            raise ValueError(
                "AWS Bedrock access key and secret are required for "
                "access-keys mode."
            )
        common["aws_access_key_id"] = access_key_id
        common["aws_secret_access_key"] = secret
        return common

    if auth_mode == "assume_role":
        role_arn = auth.get("role_arn") or ""
        if not role_arn:
            raise ValueError(
                "AWS Bedrock role ARN is required for assume-role mode."
            )
        sts_kwargs: Dict[str, Any] = {"region_name": region, "config": _BEDROCK_BOTO_CONFIG}
        base_kid = (auth.get("base_access_key_id") or "").strip()
        base_secret = (auth.get("base_secret_access_key") or "").strip()
        if base_kid and base_secret:
            sts_kwargs["aws_access_key_id"] = base_kid
            sts_kwargs["aws_secret_access_key"] = base_secret
        sts = boto3.client("sts", **sts_kwargs)

        session_name = (
            f"{session_name_prefix}-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )
        assume_kwargs: Dict[str, Any] = {
            "RoleArn": role_arn, "RoleSessionName": session_name,
        }
        external_id = (auth.get("external_id") or "").strip()
        if external_id:
            assume_kwargs["ExternalId"] = external_id
        try:
            assumed = sts.assume_role(**assume_kwargs)
        except (BotoCoreError, ClientError) as exc:
            raise ValueError(
                f"Failed to assume role {role_arn!r}: {exc}"
            ) from exc
        creds = assumed["Credentials"]
        common["aws_access_key_id"] = creds["AccessKeyId"]
        common["aws_secret_access_key"] = creds["SecretAccessKey"]
        if creds.get("SessionToken"):
            common["aws_session_token"] = creds["SessionToken"]
        return common

    raise ValueError(
        f"Unknown AWS Bedrock auth_mode {auth_mode!r}; "
        f"expected 'access_keys' or 'assume_role'."
    )
