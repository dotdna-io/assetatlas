# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for LLM tool-spec adapters."""

from src.api.blueprints.chat.llm.base import ToolSpec, to_anthropic_spec, to_openai_spec


SPEC = ToolSpec(
    name="list_assets",
    description="List assets with optional filters",
    input_schema={
        "type": "object",
        "properties": {"page": {"type": "integer"}, "search": {"type": "string"}},
        "required": [],
    },
)


def test_to_anthropic_spec_shape():
    out = to_anthropic_spec([SPEC])
    assert out == [{
        "name": "list_assets",
        "description": "List assets with optional filters",
        "input_schema": SPEC.input_schema,
    }]


def test_to_openai_spec_shape():
    out = to_openai_spec([SPEC])
    assert out == [{
        "type": "function",
        "function": {
            "name": "list_assets",
            "description": "List assets with optional filters",
            "parameters": SPEC.input_schema,
        },
    }]


def test_to_anthropic_spec_empty_list():
    assert to_anthropic_spec([]) == []


def test_to_openai_spec_empty_list():
    assert to_openai_spec([]) == []


def test_adapters_preserve_order():
    a = ToolSpec(name="a", description="A", input_schema={"type": "object"})
    b = ToolSpec(name="b", description="B", input_schema={"type": "object"})
    assert [x["name"] for x in to_anthropic_spec([a, b])] == ["a", "b"]
    assert [x["function"]["name"] for x in to_openai_spec([a, b])] == ["a", "b"]


def test_to_responses_spec_uses_flat_function_shape():
    from src.api.blueprints.chat.llm.base import ToolSpec, to_responses_spec

    tools = [
        ToolSpec(
            name="list_assets",
            description="List assets",
            input_schema={"type": "object",
                          "properties": {"page": {"type": "integer"}}},
        ),
    ]
    out = to_responses_spec(tools)
    assert out == [
        {
            "type": "function",
            "name": "list_assets",
            "description": "List assets",
            "parameters": {
                "type": "object",
                "properties": {"page": {"type": "integer"}},
            },
        }
    ]
