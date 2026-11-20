"""Ask the model to finish verified work before the hard graph or service limit."""

import time
from typing import Annotated

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import PrivateStateAttr
from langchain_core.messages import SystemMessage
from langgraph.managed.is_last_step import RemainingStepsManager


class BudgetState(AgentState):
    # Managed by LangGraph on each superstep; excluded from public inputs/outputs.
    remaining_steps: Annotated[int, PrivateStateAttr, RemainingStepsManager]


class BudgetReminder(AgentMiddleware):
    state_schema = BudgetState

    def __init__(self, trace, step_limit, time_limit):
        self.trace = trace
        self.reserve = max(4, step_limit // 5)
        self.remind_at = time.monotonic() + time_limit * 0.8
        self.notified = False

    async def awrap_model_call(self, request, handler):
        remaining = request.state["remaining_steps"]
        if remaining > self.reserve and time.monotonic() < self.remind_at:
            return await handler(request)

        if not self.notified:
            self.notified = True
            await self.trace.emit(
                {
                    "id": f"{self.trace.root}/budget",
                    "parent_id": self.trace.root,
                    "kind": "notice",
                    "label": "接近运行预算",
                    "status": "completed",
                    "detail": "已提醒智能体核对已生成的产物，汇报已验证结果与剩余工作。",
                }
            )
        message = request.system_message
        blocks = list(message.content_blocks) if message else []
        blocks.append(
            {
                "type": "text",
                "text": (
                    "\nRuntime budget reminder: the execution deadline or graph limit is approaching. "
                    "Compare the requested deliverables with the results you have already verified. "
                    "If they meet the brief, give the final answer now. If a required check is missing, "
                    "prioritize it. State any remaining work explicitly and report only observed successes."
                ),
            }
        )
        return await handler(
            request.override(system_message=SystemMessage(content_blocks=blocks))
        )
