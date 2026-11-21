"""Give the official Langfuse LangChain handler only public, bounded observations."""

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langfuse.langchain import CallbackHandler

from .trace_io import tool_arguments, tool_output


class ProjectCallback(CallbackHandler):
    # Official OTEL span contexts are task-local. Preserve LangChain callback order.
    run_inline = True
    raise_error = False
    ignore_retriever = True

    def __init__(self, *, config, root, ids, redact, model):
        super().__init__(
            public_key=config.public_key,
            trace_context={"trace_id": root.trace_id, "parent_span_id": root.id},
        )
        self.capture = config.capture_content
        self.redact = redact
        self.model = redact(model)[:128]
        self.identity = {
            "langfuse_user_id": ids["user_id"],
            "langfuse_session_id": ids["session_id"],
            "langfuse_trace_name": "FAMSUN Agent",
        }
        self.tools = {}

    def on_chain_start(
        self, serialized, inputs, *, run_id, parent_run_id=None, name=None, **kwargs
    ):
        # LangGraph inputs include checkpoints, hidden messages and framework objects.
        super().on_chain_start(
            None,
            None,
            run_id=run_id,
            parent_run_id=parent_run_id,
            name=self.redact(name or "chain")[:128],
            metadata=self.identity if parent_run_id is None else None,
        )

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
        super().on_chain_end(None, run_id=run_id, parent_run_id=parent_run_id)

    def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, **kwargs
    ):
        super().on_chat_model_start(
            {},
            [[]],
            run_id=run_id,
            parent_run_id=parent_run_id,
            name="model",
            metadata={"ls_model_name": self.model},
            invocation_params={},
        )

    def on_llm_start(self, serialized, prompts, **kwargs):
        self.on_chat_model_start(None, [], **kwargs)

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):
        usage = None
        if response.generations and response.generations[0]:
            message = getattr(response.generations[0][0], "message", None)
            counts = getattr(message, "usage_metadata", None) or {}
            fields = ("input_tokens", "output_tokens", "total_tokens")
            if all(type(counts.get(k)) is int and counts[k] >= 0 for k in fields):
                usage = {k: counts[k] for k in fields}
        # Do not pass raw model generations, provider metadata, reasoning or tool arguments.
        result = LLMResult(
            generations=[
                [ChatGeneration(message=AIMessage(content="", usage_metadata=usage))]
            ]
        )
        super().on_llm_end(result, run_id=run_id, parent_run_id=parent_run_id)

    def on_tool_start(
        self,
        serialized,
        input_str,
        *,
        run_id,
        parent_run_id=None,
        inputs=None,
        **kwargs,
    ):
        tool = (serialized or {}).get("name") or "tool"
        self.tools[run_id] = tool
        name = self.redact(tool)[:128]
        if tool == "task" and isinstance(inputs, dict):
            kind = inputs.get("subagent_type")
            if kind in ("reviewer", "engineering", "general-purpose"):
                name = "task/" + kind
        data = tool_arguments(tool, inputs, self.redact) if self.capture else None
        super().on_tool_start(
            {"name": name},
            "",
            run_id=run_id,
            parent_run_id=parent_run_id,
            inputs=data,
        )

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        name = self.tools.pop(run_id, "")
        status, data = tool_output(name, output, self.redact)
        if status == "failed":
            code = data.get("exit_code")
            reason = f"exit_code={code}" if code is not None else "tool_failed"
            super().on_tool_error(
                RuntimeError(reason), run_id=run_id, parent_run_id=parent_run_id
            )
        else:
            super().on_tool_end(
                data if self.capture else None,
                run_id=run_id,
                parent_run_id=parent_run_id,
            )

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self.tools.pop(run_id, None)
        super().on_tool_error(
            RuntimeError(type(error).__name__),
            run_id=run_id,
            parent_run_id=parent_run_id,
        )

    def on_chain_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        super().on_chain_error(
            RuntimeError(type(error).__name__),
            run_id=run_id,
            parent_run_id=parent_run_id,
        )

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        super().on_llm_error(
            RuntimeError(type(error).__name__),
            run_id=run_id,
            parent_run_id=parent_run_id,
        )

    # Legacy AgentAction/AgentFinish payloads contain the complete scratchpad.
    def on_agent_action(self, action, **kwargs):
        pass

    def on_agent_finish(self, finish, **kwargs):
        pass
