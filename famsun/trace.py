"""Project public LangChain callbacks onto the user-visible activity tree."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import ToolMessage
from langgraph.types import Command

Emit = Callable[[dict[str, Any]], Awaitable[None]]

_SECRET = re.compile(
    r"(?i)\b(?:bearer\s+|(?:api[_-]?key|token|password|secret)\s*[=:]\s*[\"']?)"
    r"[^\s,;\"']+|\bsk-[\w-]{12,}"
)


def excerpt(value: str, limit: int = 550) -> str:
    value = _SECRET.sub("[已隐藏]", value.strip())
    return value[:limit] + ("…" if len(value) > limit else "")


def call_summary(name: str, arguments: dict[str, Any]) -> str:
    # Inputs such as file bodies and injected ToolRuntime/state stay private.
    if name == "write_todos":
        todos = arguments.get("todos", [])
        return excerpt(
            "\n".join(
                f"{t.get('status', '')} · {t.get('content', '')}"
                for t in todos
                if isinstance(t, dict)
            )
        )
    for key in ("description", "command", "file_path", "query", "pattern", "path"):
        value = arguments.get(key)
        if isinstance(value, str):
            return excerpt(value)
    return ""


def tool_result(output: Any) -> tuple[str, str]:
    if isinstance(output, Command) and isinstance(output.update, dict):
        messages = output.update.get("messages") or []
        output = messages[-1] if messages else None
    if isinstance(output, ToolMessage):
        content = output.text
        artifact = output.artifact if isinstance(output.artifact, dict) else {}
        failed = (
            output.status == "error"
            or artifact.get("exit_code") not in (None, 0)
            or (
                output.name
                in {"read_file", "edit_file", "write_file", "ls", "grep", "glob"}
                and content.startswith("Error:")
            )
            or (
                output.name == "task"
                and content.startswith("We cannot invoke subagent")
            )
        )
        return ("failed" if failed else "completed"), excerpt(content)
    content = output if isinstance(output, str) else ""
    failed = content.startswith(("Error:", "We cannot invoke subagent"))
    return ("failed" if failed else "completed"), excerpt(content)


class ActivityTrace(AsyncCallbackHandler):
    """Keep concurrent subagent branches separate using callback ancestry."""

    raise_error = True
    run_inline = True

    def __init__(self, run_id: str, emit: Emit):
        self.root = f"{run_id}/agent"
        self.emit = emit
        self.ancestors: dict[UUID, str] = {}
        self.active: dict[UUID, dict[str, Any]] = {}
        self.dispatch_nodes: set[UUID] = set()
        self.observed_calls: set[tuple[str, str]] = set()

    async def on_chain_start(
        self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs
    ):
        self.ancestors[run_id] = self.ancestors.get(parent_run_id, self.root)
        if (
            kwargs.get("name") == "tools"
            and (kwargs.get("metadata") or {}).get("langgraph_node") == "tools"
        ):
            self.dispatch_nodes.add(run_id)

    async def on_chain_end(self, outputs, *, run_id, **kwargs):
        parent = self.ancestors.pop(run_id, self.root)
        if run_id not in self.dispatch_nodes:
            return
        self.dispatch_nodes.discard(run_id)
        # Unknown tools and pre-dispatch middleware refusals never invoke BaseTool.
        messages = outputs.get("messages", []) if isinstance(outputs, dict) else []
        for index, msg in enumerate(messages):
            if (
                isinstance(msg, ToolMessage)
                and msg.status == "error"
                and (parent, msg.tool_call_id) not in self.observed_calls
            ):
                await self.emit(
                    {
                        "id": f"{run_id}/rejected/{index}",
                        "parent_id": parent,
                        "kind": "tool",
                        "label": msg.name or "tool",
                        "status": "failed",
                        "detail": excerpt(msg.text),
                    }
                )

    async def on_chain_error(self, error, *, run_id, **kwargs):
        self.ancestors.pop(run_id, None)
        self.dispatch_nodes.discard(run_id)

    async def start(self, run_id, parent_run_id, kind, label, detail=""):
        event = {
            "id": str(run_id),
            "parent_id": self.ancestors.get(parent_run_id, self.root),
            "kind": kind,
            "label": label,
            "status": "running",
            "detail": detail,
        }
        self.ancestors[run_id] = str(run_id)
        self.active[run_id] = event
        await self.emit(event.copy())

    async def finish(self, run_id, status, detail=""):
        self.ancestors.pop(run_id, None)
        event = self.active.pop(run_id, None)
        if event is not None:
            event["status"] = status
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
        arguments = inputs or {}
        is_task = name == "task"
        if is_task:
            label = str(arguments.get("subagent_type") or "subagent")
        else:
            label = name
        await self.start(
            run_id,
            parent_run_id,
            "subagent" if is_task else "tool",
            label,
            call_summary(name, arguments),
        )

    async def on_tool_end(self, output, *, run_id, **kwargs):
        status, detail = tool_result(output)
        await self.finish(run_id, status, detail)

    async def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, **kwargs
    ):
        await self.start(run_id, parent_run_id, "model", "模型响应")

    async def on_llm_end(self, response, *, run_id, **kwargs):
        for batch_index, batch in enumerate(response.generations):
            for gen_index, generation in enumerate(batch):
                message = getattr(generation, "message", None)
                for i, call in enumerate(getattr(message, "invalid_tool_calls", [])):
                    await self.emit(
                        {
                            "id": f"{run_id}/invalid/{batch_index}/{gen_index}/{i}",
                            "parent_id": str(run_id),
                            "kind": "tool",
                            "label": call.get("name") or "tool",
                            "status": "failed",
                            "detail": "模型返回了无法解析的工具参数",
                        }
                    )
        await self.finish(run_id, "completed")

    async def _exception(self, run_id, error):
        cancelled = isinstance(error, asyncio.CancelledError)
        await self.finish(
            run_id, "cancelled" if cancelled else "failed", type(error).__name__
        )

    async def on_llm_error(self, error, *, run_id, **kwargs):
        await self._exception(run_id, error)

    async def on_tool_error(self, error, *, run_id, **kwargs):
        await self._exception(run_id, error)

    async def close_pending(self, status: str):
        for node_id in reversed(list(self.active)):
            await self.finish(node_id, status)
