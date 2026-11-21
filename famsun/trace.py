"""Project public LangChain callbacks onto the user-visible activity tree."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import ToolMessage

from .public_text import VisibleMarkup, public_text, redact_text
from .trace_io import tool_arguments, tool_output
from .trace_stream import RUN_CHARS, Transcript

Emit = Callable[[dict[str, Any]], Awaitable[None]]


def excerpt(value: str, limit: int = 550) -> str:
    value = redact_text(VisibleMarkup().feed(value)).strip()
    return value[:limit] + ("…" if len(value) > limit else "")


def brief(value) -> str:
    if value is None:
        return ""
    return excerpt(
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    )


def call_summary(arguments) -> str:
    for key in (
        "description",
        "command",
        "file_path",
        "query",
        "pattern",
        "path",
        "todos",
    ):
        if key in arguments:
            return brief(arguments[key])
    return ""


def tool_result(output) -> tuple[str, str]:
    """Compatibility for consumers of the original compact text summary."""
    status, preview = tool_output(
        getattr(output, "name", "") or "", output, redact_text
    )
    return status, brief(preview["result"])


class ActivityTrace(AsyncCallbackHandler):
    """Keep concurrent subagent branches separate using callback ancestry."""

    raise_error = True
    run_inline = True

    def __init__(self, run_id: str, emit: Emit, *, secrets: tuple[str, ...] = ()):
        self.secrets = tuple(s for s in secrets if isinstance(s, str) and s)
        self.remaining_text = RUN_CHARS
        self.streams: dict[UUID, Transcript] = {}
        self.root = f"{run_id}/agent"
        self.emit = emit
        self.ancestors: dict[UUID, str] = {}
        self.active: dict[UUID, dict[str, Any]] = {}
        self.dispatch_nodes: dict[UUID, dict[str, dict]] = {}
        self.observed_calls: set[tuple[str, str]] = set()

    def redact(self, text):
        return redact_text(VisibleMarkup().feed(text), self.secrets)

    def answer(self, message):
        return public_text(message, self.secrets).strip()

    async def on_chain_start(
        self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs
    ):
        self.ancestors[run_id] = self.ancestors.get(parent_run_id, self.root)
        if (
            kwargs.get("name") == "tools"
            and (kwargs.get("metadata") or {}).get("langgraph_node") == "tools"
        ):
            self.dispatch_nodes[run_id] = {
                item["id"]: item["args"]
                for item in (inputs if isinstance(inputs, list) else [])
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and isinstance(item.get("args"), dict)
            }

    async def on_chain_end(self, outputs, *, run_id, **kwargs):
        parent = self.ancestors.pop(run_id, self.root)
        if run_id not in self.dispatch_nodes:
            return
        calls = self.dispatch_nodes.pop(run_id)
        # Unknown tools and pre-dispatch middleware refusals never invoke BaseTool.
        messages = outputs.get("messages", []) if isinstance(outputs, dict) else []
        for index, msg in enumerate(messages):
            if (
                isinstance(msg, ToolMessage)
                and msg.status == "error"
                and (parent, msg.tool_call_id) not in self.observed_calls
            ):
                _, output = tool_output(msg.name or "", msg, self.redact)
                await self.emit(
                    {
                        "id": f"{run_id}/rejected/{index}",
                        "parent_id": parent,
                        "kind": "tool",
                        "label": self.redact(msg.name or "tool"),
                        "status": "failed",
                        "detail": brief(output["result"]),
                        "payload": {
                            **tool_arguments(
                                msg.name or "", calls.get(msg.tool_call_id), self.redact
                            ),
                            **output,
                        },
                    }
                )

    async def on_chain_error(self, error, *, run_id, **kwargs):
        self.ancestors.pop(run_id, None)
        self.dispatch_nodes.pop(run_id, None)

    async def start(self, run_id, parent_run_id, kind, label, detail="", payload=None):
        event = {
            "id": str(run_id),
            "parent_id": self.ancestors.get(parent_run_id, self.root),
            "kind": kind,
            "label": label[:160],
            "status": "running",
            "detail": detail,
        }
        if payload is not None:
            event["payload"] = payload
        self.ancestors[run_id] = str(run_id)
        self.active[run_id] = event
        await self.emit(event.copy())

    async def finish(self, run_id, status, detail="", payload=None):
        self.ancestors.pop(run_id, None)
        event = self.active.pop(run_id, None)
        if event is not None:
            event["status"] = status
            if payload is not None:
                event["payload"] = {**event.get("payload", {}), **payload}
            if detail:
                event["detail"] = "\n\n".join(filter(None, (event["detail"], detail)))
            await self.emit(event)

    async def on_tool_start(
        self,
        serialized,
        input_str,
        *,
        run_id,
        parent_run_id=None,
        inputs=None,
        **kwargs,
    ):
        name = serialized.get("name", "tool")
        if tool_call_id := kwargs.get("tool_call_id"):
            self.observed_calls.add(
                (self.ancestors.get(parent_run_id, self.root), tool_call_id)
            )
        parent = self.ancestors.get(parent_run_id, self.root)
        for stream in tuple(self.streams.values()):
            if stream.event["parent_id"] == parent:
                await stream.drain()
        payload = tool_arguments(name, inputs, self.redact)
        arguments = payload["arguments"]
        is_task = name == "task"
        if is_task:
            label = str(arguments.get("subagent_type") or "subagent")
        else:
            label = self.redact(name)
        await self.start(
            run_id,
            parent_run_id,
            "subagent" if is_task else "tool",
            label,
            call_summary(arguments),
            payload,
        )

    async def on_tool_end(self, output, *, run_id, **kwargs):
        event = self.active.get(run_id) or {}
        name = "task" if event.get("kind") == "subagent" else event.get("label", "")
        status, payload = tool_output(name, output, self.redact)
        await self.finish(run_id, status, brief(payload["result"]), payload)

    async def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, **kwargs
    ):
        tags = set(kwargs.get("tags") or [])
        metadata = kwargs.get("metadata") or {}
        hidden = (
            bool(tags & {"nostream", "langsmith:hidden"})
            or metadata.get("lc_source") == "summarization"
            or bool(metadata.get("lc_internal_call"))
        )
        await self.start(
            run_id, parent_run_id, "model", "内部模型调用" if hidden else "模型响应"
        )
        if not hidden:
            self.streams[run_id] = Transcript(self, self.active[run_id])

    async def on_llm_new_token(self, token, *, run_id, chunk=None, **kwargs):
        stream = self.streams.get(run_id)
        if stream is not None and chunk is not None:
            # Untyped provider token strings may represent thinking or tool JSON.
            stream.add(getattr(chunk, "message", None))

    async def end_stream(self, run_id, message=None):
        stream = self.streams.get(run_id)
        if stream is not None:
            await stream.close(message)
            self.streams.pop(run_id, None)

    async def on_llm_end(self, response, *, run_id, **kwargs):
        if run_id not in self.streams:
            await self.finish(run_id, "completed")
            return
        first = next((gen for batch in response.generations for gen in batch), None)
        reply = getattr(first, "message", None)
        await self.end_stream(run_id, reply)
        for batch_index, batch in enumerate(response.generations):
            for gen_index, generation in enumerate(batch):
                message = getattr(generation, "message", None)
                for i, call in enumerate(getattr(message, "invalid_tool_calls", [])):
                    await self.emit(
                        {
                            "id": f"{run_id}/invalid/{batch_index}/{gen_index}/{i}",
                            "parent_id": str(run_id),
                            "kind": "tool",
                            "label": self.redact(call.get("name") or "tool"),
                            "status": "failed",
                            "detail": "模型返回了无法解析的工具参数",
                            "payload": {
                                **tool_arguments("", {}, self.redact),
                                "result": None,
                                "result_truncated": False,
                                "error": "模型返回了无法解析的工具参数",
                            },
                        }
                    )
        await self.finish(
            run_id,
            "completed",
            payload={
                "type": "model_end",
                "has_tool_calls": bool(
                    getattr(reply, "tool_calls", None)
                    or getattr(reply, "invalid_tool_calls", None)
                ),
            },
        )

    async def _exception(self, run_id, error):
        cancelled = isinstance(error, asyncio.CancelledError)
        event = self.active.get(run_id) or {}
        error_type = type(error).__name__
        io = (
            {"result": None, "result_truncated": False, "error": error_type}
            if event.get("kind") in {"tool", "subagent"}
            else None
        )
        await self.end_stream(run_id)
        await self.finish(
            run_id, "cancelled" if cancelled else "failed", error_type, io
        )

    async def on_llm_error(self, error, *, run_id, **kwargs):
        await self._exception(run_id, error)

    async def on_tool_error(self, error, *, run_id, **kwargs):
        await self._exception(run_id, error)

    async def close_pending(self, status: str):
        for node_id in reversed(list(self.active)):
            await self.end_stream(node_id)
            event = self.active.get(node_id, {})
            payload = None
            if event.get("kind") in {"tool", "subagent"}:
                payload = {
                    "result": None,
                    "result_truncated": False,
                    "error": "调用已中断",
                }
            await self.finish(node_id, status, payload=payload)
