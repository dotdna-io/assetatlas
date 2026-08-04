# SPDX-License-Identifier: AGPL-3.0-or-later
"""make_llm_client dispatch tests for AWS Bedrock."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.api.blueprints.chat.llm import make_llm_client
from src.api.blueprints.chat.llm.bedrock_client import BedrockClient


_BASE_CONFIG = {
    "CHAT_LLM_PROVIDER": "aws_bedrock",
    "CHAT_LLM_MODEL": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "CHAT_LLM_API_KEY": "",
    "CHAT_LLM_BASE_URL": "",
    "CHAT_PROMPT_CACHE": True,
    "CHAT_LLM_THINKING_ENABLED": False,
    "CHAT_LLM_AWS_REGION": "us-east-1",
}


@pytest.fixture
def mock_boto3():
    """``make_llm_client`` and the ``resolve_aws_credentials`` helper each
    hold their own ``boto3`` module reference (separate ``import boto3``
    statements). Bind the same MagicMock to BOTH module globals so existing
    call-arg assertions still see every ``boto3.client(...)`` call regardless
    of which module made it."""
    m = MagicMock(name="boto3")
    with patch("src.api.blueprints.chat.llm.boto3", new=m, create=True), \
            patch("src.api.blueprints.chat.llm.aws_credentials.boto3",
                  new=m, create=True):
        yield m


def test_access_keys_mode_builds_runtime_client_with_static_creds(mock_boto3):
    runtime = MagicMock(name="bedrock-runtime")
    mock_boto3.client.return_value = runtime

    cfg = {**_BASE_CONFIG, "CHAT_LLM_AWS_AUTH": {
        "auth_mode": "access_keys",
        "access_key_id": "AKIA1",
        "secret_access_key": "s1",
    }}
    client = make_llm_client(cfg)

    assert isinstance(client, BedrockClient)
    mock_boto3.client.assert_called_once()
    args, kwargs = mock_boto3.client.call_args
    assert args == ("bedrock-runtime",)
    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["aws_access_key_id"] == "AKIA1"
    assert kwargs["aws_secret_access_key"] == "s1"
    # botocore Config is injected for timeouts; just confirm it's present.
    assert "config" in kwargs


def test_assume_role_mode_calls_sts_then_builds_runtime_client(mock_boto3):
    sts = MagicMock(name="sts")
    sts.assume_role.return_value = {"Credentials": {
        "AccessKeyId": "ASIA1", "SecretAccessKey": "ssec",
        "SessionToken": "tok",
    }}
    runtime = MagicMock(name="bedrock-runtime")
    mock_boto3.client.side_effect = lambda svc, **_kw: (
        sts if svc == "sts" else runtime
    )

    cfg = {**_BASE_CONFIG, "CHAT_LLM_AWS_AUTH": {
        "auth_mode": "assume_role",
        "role_arn": "arn:aws:iam::1:role/x",
    }}
    client = make_llm_client(cfg)

    assert isinstance(client, BedrockClient)
    sts.assume_role.assert_called_once()
    call = sts.assume_role.call_args.kwargs
    assert call["RoleArn"] == "arn:aws:iam::1:role/x"
    assert call["RoleSessionName"].startswith("assetatlas-chat")
    bedrock_call = [c for c in mock_boto3.client.call_args_list
                    if c.args[0] == "bedrock-runtime"][0]
    assert bedrock_call.kwargs["aws_access_key_id"] == "ASIA1"
    assert bedrock_call.kwargs["aws_secret_access_key"] == "ssec"
    assert bedrock_call.kwargs["aws_session_token"] == "tok"


def test_missing_region_raises_value_error():
    cfg = {**_BASE_CONFIG, "CHAT_LLM_AWS_REGION": "",
           "CHAT_LLM_AWS_AUTH": {"auth_mode": "access_keys",
                                 "access_key_id": "a", "secret_access_key": "b"}}
    with pytest.raises(ValueError, match="AWS region"):
        make_llm_client(cfg)


def test_unknown_auth_mode_raises_value_error():
    cfg = {**_BASE_CONFIG, "CHAT_LLM_AWS_AUTH": {"auth_mode": "magic"}}
    with pytest.raises(ValueError, match="auth_mode"):
        make_llm_client(cfg)


def test_assume_role_with_external_id_passes_to_sts(mock_boto3):
    sts = MagicMock(name="sts")
    sts.assume_role.return_value = {"Credentials": {
        "AccessKeyId": "ASIA1", "SecretAccessKey": "ssec",
        "SessionToken": "tok",
    }}
    runtime = MagicMock(name="bedrock-runtime")
    mock_boto3.client.side_effect = lambda svc, **_kw: (
        sts if svc == "sts" else runtime
    )

    cfg = {**_BASE_CONFIG, "CHAT_LLM_AWS_AUTH": {
        "auth_mode": "assume_role",
        "role_arn": "arn:aws:iam::1:role/x",
        "external_id": "my-ext-id",
    }}
    make_llm_client(cfg)
    call = sts.assume_role.call_args.kwargs
    assert call["ExternalId"] == "my-ext-id"


def test_assume_role_without_external_id_omits_kwarg(mock_boto3):
    sts = MagicMock(name="sts")
    sts.assume_role.return_value = {"Credentials": {
        "AccessKeyId": "ASIA1", "SecretAccessKey": "ssec",
        "SessionToken": "tok",
    }}
    runtime = MagicMock(name="bedrock-runtime")
    mock_boto3.client.side_effect = lambda svc, **_kw: (
        sts if svc == "sts" else runtime
    )

    cfg = {**_BASE_CONFIG, "CHAT_LLM_AWS_AUTH": {
        "auth_mode": "assume_role",
        "role_arn": "arn:aws:iam::1:role/x",
        # no external_id at all
    }}
    make_llm_client(cfg)
    call = sts.assume_role.call_args.kwargs
    assert "ExternalId" not in call


def test_assume_role_with_base_creds_constructs_sts_with_them(mock_boto3):
    sts = MagicMock(name="sts")
    sts.assume_role.return_value = {"Credentials": {
        "AccessKeyId": "ASIA1", "SecretAccessKey": "ssec",
        "SessionToken": "tok",
    }}
    runtime = MagicMock(name="bedrock-runtime")
    mock_boto3.client.side_effect = lambda svc, **_kw: (
        sts if svc == "sts" else runtime
    )

    cfg = {**_BASE_CONFIG, "CHAT_LLM_AWS_AUTH": {
        "auth_mode": "assume_role",
        "role_arn": "arn:aws:iam::1:role/x",
        "base_access_key_id": "AKIABASE",
        "base_secret_access_key": "basesecret",
    }}
    make_llm_client(cfg)
    # STS client should be built with the explicit base creds.
    sts_call = [c for c in mock_boto3.client.call_args_list
                if c.args[0] == "sts"][0]
    assert sts_call.kwargs["aws_access_key_id"] == "AKIABASE"
    assert sts_call.kwargs["aws_secret_access_key"] == "basesecret"
    assert sts_call.kwargs["region_name"] == "us-east-1"


def test_assume_role_without_base_creds_omits_them_from_sts_kwargs(mock_boto3):
    sts = MagicMock(name="sts")
    sts.assume_role.return_value = {"Credentials": {
        "AccessKeyId": "ASIA1", "SecretAccessKey": "ssec",
        "SessionToken": "tok",
    }}
    runtime = MagicMock(name="bedrock-runtime")
    mock_boto3.client.side_effect = lambda svc, **_kw: (
        sts if svc == "sts" else runtime
    )

    cfg = {**_BASE_CONFIG, "CHAT_LLM_AWS_AUTH": {
        "auth_mode": "assume_role",
        "role_arn": "arn:aws:iam::1:role/x",
    }}
    make_llm_client(cfg)
    sts_call = [c for c in mock_boto3.client.call_args_list
                if c.args[0] == "sts"][0]
    assert "aws_access_key_id" not in sts_call.kwargs
    assert "aws_secret_access_key" not in sts_call.kwargs
    assert sts_call.kwargs["region_name"] == "us-east-1"
