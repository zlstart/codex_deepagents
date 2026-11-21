"""Reserve a tool-free summary turn before the hard graph or service limit."""

import asyncio
from typing import Annotated

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import PrivateStateAttr
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.managed.is_last_step import RemainingStepsManager

from .runtime import run_deadline


class BudgetState(AgentState):
    # Managed by LangGraph on each superstep; excluded from public inputs/outputs.
    remaining_steps: Annotated[int, PrivateStateAttr, RemainingStepsManager]


SOFT_PROMPT = (
    "Runtime budget reminder: the execution deadline or graph limit is approaching. "
    "Compare the requested deliverables with the results you have already verified. "
    "If they meet the brief, give the final answer now. If a required check is missing, "
    "prioritize it. State any remaining work explicitly and report only observed successes."
)
SUMMARY_PROMPT = (
    "Final budget summary. Tool execution has stopped. Address the latest user request. "
    "Identify any earlier-turn evidence as such. Reply once in the user's language, "
    "in at most 8 short bullet points. List only artifacts and results evidenced by completed "
    "tool calls, distinguish unverified files from checked outcomes, and identify unfinished "
    "requirements. If no result was verified, say so. Never claim full completion."
)
EMPTY_SUMMARY = (
    "运行预算已用尽，未获得可用的收尾摘要。请在执行记录和会话工作区核对已生成的文件。"
)


class BudgetReminder(AgentMiddleware):
    state_schema = BudgetState

    def __init__(self, trace, step_limit, time_limit, *, allow_summary=True):
        self.trace = trace
        self.reserve = max(4, step_limit // 5)
        self.final_steps = min(10, max(3, step_limit // 10))
        self.deadline = (
            run_deadline.get() or asyncio.get_running_loop().time() + time_limit
        )
        self.remind_at = self.deadline - time_limit * 0.2
        self.final_at = self.deadline - time_limit * 0.1
        self.allow_summary = allow_summary
        self.notified = False
        self.force_summary = False
        self.summary_started = False
        self.summary = None
        self.work_timer: asyncio.Timeout | None = None

    async def notice(self, key, label, detail):
        await self.trace.emit(
            {
                "id": f"{self.trace.root}/{key}",
                "parent_id": self.trace.root,
                "kind": "notice",
                "label": label,
                "status": "completed",
                "detail": detail,
            }
        )

    async def awrap_model_call(self, request, handler):
        remaining = request.state["remaining_steps"]
        now = asyncio.get_running_loop().time()
        final = self.allow_summary and (
            self.force_summary or remaining <= self.final_steps or now >= self.final_at
        )
        if not final and remaining > self.reserve and now < self.remind_at:
            return await handler(request)

        if final:
            if self.summary_started:
                return AIMessage(content=self.summary or EMPTY_SUMMARY)
            self.summary_started = True
            # The outer service deadline still applies to this last provider call.
            if self.work_timer is not None and not self.work_timer.expired():
                self.work_timer.reschedule(None)
            await self.notice(
                "summary",
                "预算收尾",
                "已关闭工具调用，正在整理有记录的结果与未完成项。",
            )
        elif not self.notified:
            self.notified = True
            await self.notice(
                "budget",
                "接近运行预算",
                "已提醒智能体核对已生成的产物，汇报已验证结果与剩余工作。",
            )

        message = request.system_message
        blocks = list(message.content_blocks) if message else []
        blocks.append(
            {"type": "text", "text": "\n" + (SUMMARY_PROMPT if final else SOFT_PROMPT)}
        )
        request = request.override(system_message=SystemMessage(content_blocks=blocks))
        if not final:
            return await handler(request)

        response = await handler(
            request.override(tools=[], tool_choice=None, response_format=None)
        )
        last = response.result[-1] if response.result else None
        # A nonconforming provider may emit calls despite an empty tool list.
        # Returning plain text prevents dispatch and stores a usable graph checkpoint.
        if (
            isinstance(last, AIMessage)
            and not last.tool_calls
            and not last.invalid_tool_calls
        ):
            self.summary = self.trace.answer(last)[:8000]
        self.summary = self.summary or EMPTY_SUMMARY
        return AIMessage(content=self.summary)
