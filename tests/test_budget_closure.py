"""Budget exhaustion must return real evidence and never execute final-turn calls."""

import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from deepagents.backends import FilesystemBackend
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from famsun import harness
from famsun.api import create_app
from famsun.budget import EMPTY_SUMMARY
from famsun.config import Settings
from famsun.runtime import BudgetClosed, run_deadline


class BudgetModel(BaseChatModel):
    _calls: list = PrivateAttr(default_factory=list)
    _stalled: bool = PrivateAttr(default=False)

    @property
    def _llm_type(self):
        return "budget-test"

    def bind_tools(self, tools, **kwargs):
        return self.bind(bound_tool_count=len(tools))

    def _generate(self, *args, **kwargs):
        raise AssertionError("Async test model only")

    async def _agenerate(self, messages, *, bound_tool_count=0, **kwargs):
        prompt = next(m.text for m in reversed(messages) if isinstance(m, HumanMessage))
        final = "Final budget summary." in messages[0].text
        self._calls.append((final, bound_tool_count))
        if prompt == "recall partial":
            assert any(
                isinstance(m, AIMessage) and "remains unverified" in m.text
                for m in messages
            )
            reply = AIMessage(content="Continued from the previous partial answer.")
        elif final:
            assert bound_tool_count == 0
            if prompt == "summary stalls":
                await asyncio.Future()
            if prompt == "ignore disabled tools":
                reply = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"file_path": "/leaked", "content": "bad"},
                            "id": "bad",
                        }
                    ],
                )
            else:
                assert any(
                    isinstance(m, ToolMessage) and m.name == "ls" for m in messages
                )
                reply = AIMessage(
                    content="Workspace listing checked; required export remains unverified."
                )
        elif prompt == "stall" and isinstance(messages[-1], ToolMessage):
            self._stalled = True
            await asyncio.Future()
        else:
            assert bound_tool_count > 0
            reply = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ls",
                        "args": {"path": "/"},
                        "id": f"ls-{len(self._calls)}",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=reply)])


@pytest.mark.parametrize("prompt", ["loop", "ignore disabled tools", "stall"])
async def test_one_unbound_summary_and_no_last_tool(tmp_path, monkeypatch, prompt):
    model = BudgetModel()
    monkeypatch.setattr(harness, "build_model", lambda settings: model)
    cfg = SimpleNamespace(
        checkpoint_path=tmp_path / "state.sqlite",
        mcp_enabled=False,
        graph_recursion_limit=160 if prompt == "stall" else 36,
        run_timeout=30,
    )
    events = []

    async def emit(event):
        events.append(event)

    # Simulate time already spent on sandbox provisioning. This leaves roughly one
    # second of normal work and three seconds for the final model response.
    token = run_deadline.set(
        asyncio.get_running_loop().time() + 4 if prompt == "stall" else None
    )
    try:
        with pytest.raises(BudgetClosed) as result:
            await harness.run_agent(
                user_id="u",
                session_id="s",
                run_id="budget",
                prompt=prompt,
                sandbox=FilesystemBackend(root_dir=tmp_path),
                emit=emit,
                settings=cfg,
            )
    finally:
        run_deadline.reset(token)
    assert result.value.answer == (
        EMPTY_SUMMARY
        if prompt == "ignore disabled tools"
        else "Workspace listing checked; required export remains unverified."
    )
    assert sum(final for final, _ in model._calls) == 1
    assert model._calls[-1] == (True, 0)
    if prompt == "stall":
        assert model._stalled
    index = next(i for i, e in enumerate(events) if e["label"] == "预算收尾")
    assert not any(
        e["kind"] == "tool" and e["status"] == "running" for e in events[index:]
    )
    assert events[-1]["status"] == "failed"
    assert not (tmp_path / "leaked").exists()


def test_degraded_answer_and_error_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "build_model", lambda settings: BudgetModel())
    cfg = replace(
        Settings(),
        data_dir=tmp_path,
        static_dir=tmp_path / "unused",
        secure_cookies=False,
        allow_signup=True,
        mcp_enabled=False,
        graph_recursion_limit=36,
    )

    class Workspace(FilesystemBackend):
        def start(self):
            pass

        def stop(self):
            pass

    def factory(**kwargs):
        return Workspace(root_dir=tmp_path / "sandbox")

    with TestClient(create_app(cfg, sandbox_factory=factory)) as client:
        response = client.post(
            "/api/auth/register",
            json={"username": "budget-tester", "password": "test-budget-secret-445"},
        )
        assert response.status_code == 201
        session = client.post("/api/sessions", json={}).json()["id"]
        url = f"/api/sessions/{session}/runs"
        run = client.post(url, json={"prompt": "loop"}).json()["id"]
        for _ in range(100):
            record = client.get(f"{url}/{run}").json()
            if record["status"] in {"failed", "completed"}:
                break
            time.sleep(0.05)
        assert record["status"] == "failed"
        assert (
            record["answer"]
            == "Workspace listing checked; required export remains unverified."
        )
        assert record["error"] == BudgetClosed.public_message
        assert client.get(url).json()[0]["answer"] == record["answer"]
        next_run = client.post(url, json={"prompt": "recall partial"}).json()["id"]
        for _ in range(100):
            follow_up = client.get(f"{url}/{next_run}").json()
            if follow_up["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        assert follow_up["status"] == "completed"
        assert follow_up["answer"] == "Continued from the previous partial answer."


async def test_summary_must_finish_within_original_hard_deadline(tmp_path, monkeypatch):
    model = BudgetModel()
    monkeypatch.setattr(harness, "build_model", lambda settings: model)
    cfg = SimpleNamespace(
        checkpoint_path=tmp_path / "state.sqlite",
        mcp_enabled=False,
        graph_recursion_limit=160,
        run_timeout=30,
    )

    async def emit(event):
        pass

    token = run_deadline.set(asyncio.get_running_loop().time() + 1.5)
    try:
        with pytest.raises(TimeoutError):
            await harness.run_agent(
                user_id="u",
                session_id="s",
                run_id="hard",
                prompt="summary stalls",
                sandbox=FilesystemBackend(root_dir=tmp_path),
                emit=emit,
                settings=cfg,
            )
    finally:
        run_deadline.reset(token)
    assert model._calls == [(True, 0)]
