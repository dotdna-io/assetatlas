# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Bedrock-specific helpers in chat/llm/base.py."""

from src.api.blueprints.chat.llm.base import (
    LLMMessage, ToolSpec, to_bedrock_spec, messages_to_bedrock,
    _EMPTY_ASSISTANT_PLACEHOLDER,
)


def test_to_bedrock_spec_wraps_tools_in_toolconfig_shape():
    tools = [
        ToolSpec(
            name="search_assets",
            description="Search assets by hostname.",
            input_schema={"type": "object",
                          "properties": {"q": {"type": "string"}}},
        ),
    ]
    result = to_bedrock_spec(tools)
    assert result == {
        "tools": [{
            "toolSpec": {
                "name": "search_assets",
                "description": "Search assets by hostname.",
                "inputSchema": {
                    "json": {"type": "object",
                             "properties": {"q": {"type": "string"}}},
                },
            },
        }],
    }


def test_to_bedrock_spec_empty_list_returns_empty_tools():
    assert to_bedrock_spec([]) == {"tools": []}


def test_messages_to_bedrock_user_and_assistant_text():
    msgs = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(role="assistant", content="hello there"),
    ]
    assert messages_to_bedrock(msgs) == [
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [{"text": "hello there"}]},
    ]


def test_messages_to_bedrock_assistant_tool_call():
    msgs = [LLMMessage(
        role="assistant",
        content="checking",
        tool_calls=[{"id": "tc_1", "name": "search_assets",
                     "arguments": {"q": "foo"}}],
    )]
    assert messages_to_bedrock(msgs) == [{
        "role": "assistant",
        "content": [
            {"text": "checking"},
            {"toolUse": {"toolUseId": "tc_1",
                         "name": "search_assets",
                         "input": {"q": "foo"}}},
        ],
    }]


def test_messages_to_bedrock_tool_result():
    msgs = [LLMMessage(role="tool", tool_call_id="tc_1",
                       content="42 results")]
    assert messages_to_bedrock(msgs) == [{
        "role": "user",
        "content": [{
            "toolResult": {
                "toolUseId": "tc_1",
                "content": [{"text": "42 results"}],
            },
        }],
    }]


def test_messages_to_bedrock_empty_assistant_gets_placeholder():
    """Bedrock Converse rejects a message with an empty content list. A model
    can end a turn with no text and no tool calls (e.g. a reasoning model that
    spent its whole budget on a hidden think block); that turn is persisted
    with content="" and replayed on the next turn. The serializer must emit a
    non-empty placeholder block instead of content=[]."""
    msgs = [LLMMessage(role="assistant", content="", tool_calls=None)]
    assert messages_to_bedrock(msgs) == [{
        "role": "assistant",
        "content": [{"text": _EMPTY_ASSISTANT_PLACEHOLDER}],
    }]


def test_messages_to_bedrock_coalesces_parallel_tool_results():
    """When the model emits multiple toolUse blocks in one assistant turn
    (parallel tool use), Bedrock Converse requires ALL matching toolResult
    blocks in the single user message that immediately follows. Each
    tool result is a separate tool-role LLMMessage, so they must be merged
    into one user message — emitting one user message per result splits them
    across consecutive user turns and Bedrock rejects the request with
    ``Expected toolResult blocks at messages.N.content for the following Ids``
    (see production, issue: parallel get_compliance_matrix + query_assets)."""
    msgs = [
        LLMMessage(role="user",
                   content="compliant workstations in servicenow?"),
        LLMMessage(
            role="assistant", content="checking",
            tool_calls=[
                {"id": "tu_1", "name": "get_compliance_matrix",
                 "arguments": {}},
                {"id": "tu_2", "name": "query_assets",
                 "arguments": {"source": "servicenow"}},
            ],
        ),
        LLMMessage(role="tool", tool_call_id="tu_1", content="matrix data"),
        LLMMessage(role="tool", tool_call_id="tu_2", content="120 assets"),
    ]
    assert messages_to_bedrock(msgs) == [
        {"role": "user",
         "content": [{"text": "compliant workstations in servicenow?"}]},
        {"role": "assistant", "content": [
            {"text": "checking"},
            {"toolUse": {"toolUseId": "tu_1",
                         "name": "get_compliance_matrix", "input": {}}},
            {"toolUse": {"toolUseId": "tu_2", "name": "query_assets",
                         "input": {"source": "servicenow"}}},
        ]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "tu_1",
                            "content": [{"text": "matrix data"}]}},
            {"toolResult": {"toolUseId": "tu_2",
                            "content": [{"text": "120 assets"}]}},
        ]},
    ]
