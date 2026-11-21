import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from famsun.trace_io import tool_arguments, tool_output


def redact(value):
    return value.replace("test-secret", "[已隐藏]")


def message(content, **kwargs):
    return ToolMessage(content=content, tool_call_id="tool-1", **kwargs)


def test_nested_typed_arguments_secret_keys_and_injected_fields():
    class PrivateObject:
        def __repr__(self):
            raise AssertionError("private object was inspected")

        __str__ = __repr__

    args = {
        "path": "/workspace/result.csv",
        "options": [
            {"temperature": 0.25, "enabled": True, "password": "hidden123"},
            {"headers": {"Authorization": "hidden456", "Accept": "text/csv"}},
        ],
        "key-test-secret": None,
        "context": {"private": "hidden789"},
        "limit": 4,
        "服务密钥": "hidden4",
        "runtime": PrivateObject(),
        "unsupported": PrivateObject(),
        "callbacks": [PrivateObject()],
        "config": {"another": "hidden321"},
    }
    payload = tool_arguments("convert", args, redact)
    assert payload == {
        "type": "tool",
        "arguments": {
            "path": "/workspace/result.csv",
            "options": [
                {"temperature": 0.25, "enabled": True, "password": "[已隐藏]"},
                {"headers": {"Authorization": "[已隐藏]", "Accept": "text/csv"}},
            ],
            "key-[已隐藏]": "[已隐藏]",
            "limit": 4,
            "服务密钥": "[已隐藏]",
        },
        "arguments_truncated": True,
    }


def test_structured_mcp_result_wins_over_message_and_private_artifact():
    response = message(
        [{"type": "text", "text": "unpublished alternate content"}],
        artifact={
            "structured_content": {
                "total": 5,
                "rows": [{"key": "test-secret"}, {"API-Key": "hidden"}],
            },
            "private": "internal",
        },
    )
    status, result = tool_output("search", response, redact)
    assert status == "completed"
    assert result == {
        "type": "tool",
        "result": {"total": 5, "rows": [{"key": "[已隐藏]"}, {"API-Key": "[已隐藏]"}]},
        "result_truncated": False,
    }


def test_message_filters_content_blocks_then_decodes_public_json():
    response = message(
        [
            {"type": "thinking", "thinking": "private-rationale"},
            {"type": "reasoning", "reasoning": "private-context"},
            {"type": "image_url", "image_url": {"url": "https://private.invalid/icon"}},
            {"type": "text", "text": '{"files": ["a","b"], "n": 2, "secret":"hidden"}'},
            {"type": "unrecognized", "text": "private vendor content"},
        ]
    )
    status, out = tool_output("search", response, redact)
    assert status == "completed" and out["result_truncated"]
    assert out["result"] == {"files": ["a", "b"], "n": 2, "secret": "[已隐藏]"}


@pytest.mark.parametrize(
    ("name", "content", "metadata", "state", "code"),
    [
        ("read_file", "Error: unavailable", {}, "failed", None),
        ("task", "We cannot invoke subagent xyz", {}, "failed", None),
        ("unknown", "invalid tool", {"status": "error"}, "failed", None),
        (
            "execute",
            "Error: application log",
            {"artifact": {"exit_code": 0}},
            "completed",
            0,
        ),
        ("execute", "failed\n[Command failed with exit code 7]", {}, "failed", 7),
        ("execute", "out", {"artifact": {"exit_code": 9}}, "failed", 9),
    ],
)
def test_tool_states_and_command_status(name, content, metadata, state, code):
    status, out = tool_output(name, message(content, name=name, **metadata), redact)
    assert status == state and out.get("exit_code") == code
    assert ("error" in out) == (state == "failed")


def test_command_exposes_only_its_result_message():
    response = message("saved test-secret", name="write_todos")
    output = Command(
        update={"messages": [AIMessage(content="hidden"), response], "state": "private"}
    )
    assert tool_output("write_todos", output, redact)[1]["result"] == "saved [已隐藏]"
    raw_state = Command(update={"messages": [{"role": "system", "content": "private"}]})
    assert tool_output("write_todos", raw_state, redact)[1]["result"] is None


@pytest.mark.parametrize("artifact", [{"truncated": True}, {"output_file": "/private"}])
def test_preserves_upstream_command_truncation(artifact):
    status, payload = tool_output(
        "execute", message("partial stdout", artifact=artifact), redact
    )
    assert status == "completed"
    assert payload == {
        "type": "tool",
        "result": "partial stdout",
        "result_truncated": True,
    }


def test_combined_unicode_nested_payload_has_serialized_byte_bound():
    branch = [{"label": ("文\0😀 " * 2000), "n": 42, "items": [1, 2]}] * 500
    secret = "test-secret"
    args = tool_arguments(
        "write_file", {"metadata": branch, "content": secret * 90}, redact
    )
    _, out = tool_output("read_file", branch, redact)
    combined = args | out
    assert args["arguments_truncated"] and out["result_truncated"]
    assert secret not in str(combined)
    for ensure_ascii in [False, True]:
        assert (
            len(
                json.dumps(
                    combined, ensure_ascii=ensure_ascii, allow_nan=False
                ).encode()
            )
            < 48 * 1024
        )


def test_bounded_cycles_media_and_non_json_values():
    items = [float("nan"), bytes(8), 2**3000, {"type": "input_audio", "data": "hidden"}]
    items.append(items)
    payload = tool_arguments(
        "inspect",
        {"items": items, "blob": "hidden", "thumb": "data:image/png;base64,YWI="},
        redact,
    )
    assert payload["arguments_truncated"]
    assert "hidden" not in json.dumps(payload, allow_nan=False)
    assert payload["arguments"]["thumb"] == "[已隐藏]"
    assert tool_output("arith", 0, redact)[1]["result"] == 0
    assert tool_output("string", "", redact)[1]["result"] == ""
