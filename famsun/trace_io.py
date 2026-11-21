"""Bounded public previews of LangChain tool inputs and results for activity traces."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command

Redact = Callable[[str], str]
_HIDDEN = "[已隐藏]"
_OMIT = object()
# Two independently encoded previews plus lifecycle metadata fit a 48 KiB event.
_FIELD_BUDGET = 19 * 1024
_STRING_BUDGET = 6 * 1024
_COLLECTION_LIMIT = 48
_DEPTH_LIMIT = 7
_SCAN_LIMIT = 64 * 1024
_INJECTED = {
    "runtime",
    "toolruntime",
    "config",
    "runnableconfig",
    "configurable",
    "state",
    "store",
    "context",
    "callbacks",
    "callbackmanager",
    "runmanager",
    "toolcallid",
    "checkpointer",
    "checkpoint",
    "messages",
    "streamwriter",
    "toolconfig",
    "threadcontext",
}
_PRIVATE = {
    "reasoning",
    "thinking",
    "reasoningcontent",
    "reasoningtext",
    "reasoningdetails",
    "encryptedreasoning",
    "analysis",
    "reasoningsummary",
    "chainofthought",
    "signature",
    "encryptedcontent",
    "additionalkwargs",
    "responsemetadata",
    "base64",
    "b64json",
    "blob",
    "imagebase64",
    "audiobase64",
    "base64data",
    "imageurl",
    "inputaudio",
    "hiddenthinking",
    "thought",
    "thoughts",
    "reasoningtrace",
}
_PRIVATE_TYPES = {
    "thinking",
    "redactedthinking",
    "reasoning",
    "analysis",
    "image",
    "imageurl",
    "inputimage",
    "audio",
    "inputaudio",
    "video",
    "file",
    "inputfile",
    "document",
    "base64",
    "nonstandard",
    "reasoningcontent",
}
_NONPUBLIC_KEYS = _INJECTED | _PRIVATE
_SECRET_WORDS = ("密码", "密钥", "口令", "令牌", "凭据")
_SECRET_KEY = re.compile(
    r"(?:password|passwd|passphrase|secret|credential|authorization|cookie|token|"
    r"apikey|accesskey|privatekey|signingkey|encryptionkey|bearer|sessionid|"
    r"(?:^|client|proxy)auth$)"
)
_BINARY = re.compile(
    r"data:[^,\s]{0,120};base64,[A-Za-z0-9+/=_-]*|[A-Za-z0-9+/=_-]{192,}", re.IGNORECASE
)
_FILE_TOOLS = {"read_file", "edit_file", "write_file", "ls", "grep", "glob"}
_EXIT = re.compile(r"\[Command (?:succeeded|failed) with exit code (-?\d{1,9})\]\s*$")


def _name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


class _Preview:
    def __init__(self, redact: Redact):
        self.redact = redact
        self.remaining = _FIELD_BUDGET
        self.truncated = False

    def omit(self):
        self.truncated = True
        return _OMIT

    def text(self, value: str, limit: int = _STRING_BUDGET) -> str:
        if len(value) > _SCAN_LIMIT:
            value = value[:_SCAN_LIMIT]
            self.truncated = True
        value, binary_count = _BINARY.subn(_HIDDEN, value)
        if binary_count:
            self.truncated = True
        value = self.redact(value).encode("utf-8", errors="replace").decode()
        # Charge the escaped representation, including control chars and Unicode.
        room = min(limit, self.remaining)
        length = len(json.dumps(value))
        if length > room:
            self.truncated = True
            lo, hi = 0, min(len(value), room)
            while lo < hi:
                middle = (lo + hi + 1) // 2
                if len(json.dumps(value[:middle])) <= room:
                    lo = middle
                else:
                    hi = middle - 1
            value = value[:lo]
            length = len(json.dumps(value))
        self.remaining -= length
        return value

    def value(self, value: Any, depth: int = 0):
        if depth > _DEPTH_LIMIT or self.remaining < 40:
            return self.omit()
        self.remaining -= 6  # separators, container delimiters
        kind = type(value)
        if kind is str:
            return self.text(value)
        if value is None or kind is bool:
            self.remaining -= 5
            return value
        if kind is int:
            if value.bit_length() > 128:
                return self.omit()
            self.remaining -= len(str(value))
            return value
        if kind is float:
            if not math.isfinite(value):
                return self.omit()
            self.remaining -= len(str(value))
            return value
        if kind in (list, tuple):
            result = []
            for item in value[:_COLLECTION_LIMIT]:
                if self.remaining < 40:
                    self.truncated = True
                    break
                clean = self.value(item, depth + 1)
                if clean is not _OMIT:
                    result.append(clean)
            self.truncated |= len(value) > _COLLECTION_LIMIT
            return result
        if kind is not dict:
            return self.omit()
        marker = value.get("type")
        if type(marker) is str and _name(marker[:128]) in _PRIVATE_TYPES:
            return self.omit()
        mapping = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _COLLECTION_LIMIT or self.remaining < 64:
                self.truncated = True
                break
            # Never stringify arbitrary keys or serialize injected framework objects.
            if type(key) is not str or len(key) > 256:
                self.truncated = True
                continue
            normalized = _name(key)
            if normalized in _NONPUBLIC_KEYS:
                self.truncated = True
                continue
            safe_key = self.text(key, 512)
            if safe_key in mapping:
                self.truncated = True
                continue
            if _SECRET_KEY.search(normalized) or any(
                word in key for word in _SECRET_WORDS
            ):
                clean = self.value(_HIDDEN, depth + 1)
            else:
                clean = self.value(item, depth + 1)
            if clean is not _OMIT:
                mapping[safe_key] = clean
        return mapping


def tool_arguments(name: str, inputs: dict | None, redact: Redact) -> dict:
    """Return public typed arguments. Framework-injected fields never enter the trace."""
    preview = _Preview(redact)
    result = preview.value(inputs) if type(inputs) is dict else {}
    return {
        "type": "tool",
        "arguments": result if type(result) is dict else {},
        "arguments_truncated": preview.truncated,
    }


def _public_text(content: Any, preview: _Preview) -> str:
    if type(content) is str:
        return content
    if type(content) is not list:
        preview.truncated = True
        return ""
    lines: list[str] = []
    total = 0
    for block in content[:_COLLECTION_LIMIT]:
        if type(block) is str:
            text = block
        elif (
            type(block) is dict
            and block.get("type") == "text"
            and type(block.get("text")) is str
        ):
            text = block["text"]
        else:
            preview.truncated = True
            continue
        room = _SCAN_LIMIT - total
        lines.append(text[: max(0, room)])
        total += len(text) + 1
        if total > _SCAN_LIMIT:
            preview.truncated = True
            break
    preview.truncated |= len(content) > _COLLECTION_LIMIT
    return "\n".join(lines)


def _typed(value: Any, preview: _Preview):
    leading = value[:256].lstrip() if type(value) is str else ""
    if leading[:1] in ("{", "["):
        if len(value) <= _SCAN_LIMIT:
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                return preview.value(parsed)
        elif leading.startswith(("{", "[{", '["')):
            # Parsing unbounded JSON as text could bypass field-based redaction.
            return preview.omit()
    return preview.value(value)


def tool_output(name: str, output: Any, redact: Redact) -> tuple[str, dict]:
    """Extract a public result. Raw LangGraph state and arbitrary artifacts are private."""
    preview = _Preview(redact)
    if isinstance(output, Command):
        updates = output.update
        messages = updates.get("messages") if type(updates) is dict else None
        output = (
            messages[-1]
            if type(messages) in (list, tuple)
            and messages
            and isinstance(messages[-1], ToolMessage)
            else None
        )
    failed = False
    exit_code = None
    text = ""
    if isinstance(output, ToolMessage):
        text = _public_text(output.content, preview)
        value = text
        failed = output.status == "error"
        tool_name = output.name or name
        if type(output.artifact) is dict:
            code = output.artifact.get("exit_code")
            if type(code) is int and code.bit_length() <= 32:
                exit_code = code
            # Deep Agents can truncate or offload stdout before its ToolMessage.
            if tool_name == "execute" and (
                output.artifact.get("truncated") is True
                or isinstance(output.artifact.get("output_file"), str)
            ):
                preview.truncated = True
            structured = output.artifact.get("structured_content")
            if type(structured) is dict:
                value = structured
        if (
            tool_name == "execute"
            and exit_code is None
            and (match := _EXIT.search(text[-128:]))
        ):
            exit_code = int(match[1])
        failed |= (tool_name in _FILE_TOOLS and text.startswith("Error:")) or (
            tool_name == "task" and text.startswith("We cannot invoke subagent")
        )
    else:
        value = output
        if type(output) is str:
            text = output
            failed = text.startswith(("Error:", "We cannot invoke subagent"))
            if name == "execute" and (match := _EXIT.search(text[-128:])):
                exit_code = int(match[1])
                failed = exit_code != 0
    failed |= exit_code not in (None, 0)
    clean = _typed(value, preview)
    payload = {
        "type": "tool",
        "result": None if clean is _OMIT else clean,
        "result_truncated": preview.truncated,
    }
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if failed:
        # A public text result is already cleaned. Do not duplicate private text
        # skipped in favor of a structured result, or serialize an exception.
        payload["error"] = (
            clean[:400] if type(clean) is str and clean else "工具执行失败"
        )
    return ("failed" if failed else "completed"), payload
