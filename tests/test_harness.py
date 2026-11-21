"""Exercise real Deep Agents graphs without a network model dependency."""

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from famsun import harness
from famsun.memory import POLICY, PREFERENCES, SEED
from famsun.trace import excerpt, tool_result


class ScriptedChat(BaseChatModel):
    """Answer from messages so the same graph can fork two agents concurrently."""

    @property
    def _llm_type(self):
        return "deterministic-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        human = next(m for m in reversed(messages) if isinstance(m, HumanMessage))
        last = messages[-1]
        if human.content == "use time budget":
            if "Runtime budget reminder" in str(messages[0].content):
                reply = AIMessage(content="verified workspace listing")
            else:
                reply = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "ls",
                            "args": {"path": "/"},
                            "id": str(uuid4()),
                        }
                    ],
                )
        elif isinstance(last, ToolMessage):
            reply = AIMessage(content="validated")
        elif human.content == "save my unit preference":
            system = messages[0].text
            assert POLICY in system and PREFERENCES in system
            assert "read-only sandbox image" in system
            reply = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": str(uuid4()),
                        "name": "edit_file",
                        "args": {
                            "file_path": PREFERENCES,
                            "old_string": SEED.rstrip(),
                            "new_string": SEED.rstrip()
                            + "\nPreferred unit: micrometres.",
                        },
                    }
                ],
            )
        elif human.content == "read preferences from system":
            reply = AIMessage(
                content=(
                    "micrometres"
                    if "Preferred unit: micrometres." in messages[0].text
                    else "default"
                )
            )
        elif human.content == "delegate both":
            assert "mechanical-assembly" in str(messages[0].content)
            assert "FAMSUN workspace conventions" in str(messages[0].content)
            reply = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": str(uuid4()),
                        "name": "task",
                        "args": {
                            "description": role + " inspect",
                            "subagent_type": role,
                        },
                    }
                    for role in ("engineering", "reviewer")
                ],
            )
        elif human.content == "bad edits":
            reply = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": str(uuid4()),
                        "name": "edit_file",
                        "args": {
                            "file_path": "/workspace/source.txt",
                            "old_string": "absent",
                            "new_string": "updated",
                        },
                    },
                    {
                        "id": str(uuid4()),
                        "name": "read_file",
                        "args": {"file_path": "/workspace/missing.txt"},
                    },
                    {"id": str(uuid4()), "name": "unregistered_tool", "args": {}},
                ],
            )
        elif human.content.endswith(" inspect"):
            file_path = (
                "/opt/famsun/skills/mechanical-assembly/SKILL.md"
                if human.content.startswith("engineering")
                else "/missing"
            )
            reply = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": str(uuid4()),
                        "name": "read_file",
                        "args": {"file_path": file_path},
                    }
                ],
            )
        elif human.content == "check recovery":
            pending = next(
                m for m in messages if isinstance(m, AIMessage) and m.tool_calls
            )
            tool_message = next(m for m in messages if isinstance(m, ToolMessage))
            assert tool_message.tool_call_id == pending.tool_calls[0]["id"]
            assert tool_message.status == "error"
            assert messages.index(tool_message) < messages.index(human)
            reply = AIMessage(content="recovered")
        else:
            reply = AIMessage(
                content=", ".join(
                    str(m.content) for m in messages if isinstance(m, HumanMessage)
                )
            )
        return ChatResult(generations=[ChatGeneration(message=reply)])


def test_nested_tools_skills_and_thread_isolation(tmp_path, monkeypatch):
    backend = FilesystemBackend(root_dir=tmp_path / "sandbox")
    shutil.copytree(
        Path(harness.__file__).parent / "skills", tmp_path / "sandbox/opt/famsun/skills"
    )
    monkeypatch.setattr(harness, "build_model", lambda settings: ScriptedChat())
    events = []

    async def emit(event):
        events.append(event)

    async def run(user, thread, text):
        return await harness.run_agent(
            user_id=user,
            session_id=thread,
            run_id=str(uuid4()),
            prompt=text,
            settings=SimpleNamespace(
                checkpoint_path=tmp_path / "state.sqlite", mcp_enabled=False
            ),
            sandbox=backend,
            emit=emit,
        )

    assert asyncio.run(run("alice", "thread", "delegate both")) == "validated"
    terminal = {e["id"]: e for e in events}
    assert not any(e["kind"] == "notice" for e in terminal.values())
    delegates = {e["label"]: e for e in terminal.values() if e["kind"] == "subagent"}
    assert set(delegates) == {"engineering", "reviewer"}
    for role in delegates:
        children = [
            e for e in terminal.values() if e["parent_id"] == delegates[role]["id"]
        ]
        assert sum(e["kind"] == "model" for e in children) == 2
        reads = [e for e in children if e["label"] == "read_file"]
        assert len(reads) == 1
        assert reads[0]["status"] == (
            "completed" if role == "engineering" else "failed"
        )
    assert all(e["status"] != "running" for e in terminal.values())
    assert "delegate both" in asyncio.run(run("alice", "thread", "recall"))
    assert asyncio.run(run("alice", "other", "recall")) == "recall"
    assert asyncio.run(run("bob", "thread", "recall")) == "recall"


def test_shell_exit_code_and_private_tokens():
    msg = ToolMessage(
        content="command failed",
        status="success",
        tool_call_id="cmd-1",
        artifact={"exit_code": 3},
    )
    assert tool_result(msg)[0] == "failed"
    assert "secret-value" not in excerpt("token=secret-value")
    assert "secret-value" not in excerpt("Bearer secret-value")


async def test_cancel_during_tool_and_continue_thread(tmp_path, monkeypatch):
    blocker = asyncio.Event()
    events = []

    async def emit(event):
        events.append(event)
        if event["label"] == "read_file" and event["status"] == "running":
            blocker.set()
            await asyncio.Future()

    monkeypatch.setattr(harness, "build_model", lambda settings: ScriptedChat())
    settings = SimpleNamespace(
        checkpoint_path=tmp_path / "state.sqlite", mcp_enabled=True
    )

    from famsun import docs_mcp

    def unexpected_connection():
        pytest.fail("An ordinary run must not connect to the documentation service")

    monkeypatch.setattr(docs_mcp, "docs_client", unexpected_connection)
    backend = FilesystemBackend(root_dir=tmp_path / "session")

    async def turn(prompt):
        return await harness.run_agent(
            user_id="u",
            session_id="s",
            run_id=str(uuid4()),
            prompt=prompt,
            settings=settings,
            sandbox=backend,
            emit=emit,
        )

    task = asyncio.create_task(turn("first inspect"))
    await asyncio.wait_for(blocker.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    terminal = {e["id"]: e for e in events}
    assert {e["status"] for e in terminal.values() if e["label"] == "read_file"} == {
        "cancelled"
    }
    assert not any(e["label"] == "search_docs_by_lang_chain" for e in events)
    assert all(e["status"] != "running" for e in terminal.values())
    assert await turn("check recovery") == "recovered"


async def test_retry_read_after_cold_database_lock():
    import sqlite3
    from unittest.mock import AsyncMock

    lock = sqlite3.OperationalError("database is locked")
    lock.sqlite_errorcode = sqlite3.SQLITE_BUSY
    graph = SimpleNamespace(aget_state=AsyncMock(side_effect=[lock, "saved"]))
    assert await harness.load_state(graph, {}) == "saved"
    assert graph.aget_state.await_count == 2


async def test_concurrent_graphs_open_shared_new_database(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "build_model", lambda settings: ScriptedChat())
    settings = SimpleNamespace(
        checkpoint_path=tmp_path / "fresh.sqlite", mcp_enabled=False
    )

    async def emit(event):
        return None

    async def run(i):
        backend = FilesystemBackend(root_dir=tmp_path / str(i))
        return await harness.run_agent(
            user_id=str(i),
            session_id="conversation",
            run_id=str(uuid4()),
            prompt=f"turn {i}",
            sandbox=backend,
            emit=emit,
            settings=settings,
        )

    assert await asyncio.gather(*(run(i) for i in range(4))) == [
        f"turn {i}" for i in range(4)
    ]


async def test_failed_file_tools_and_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "build_model", lambda settings: ScriptedChat())
    root = tmp_path / "sandbox"
    target = root / "workspace/source.txt"
    target.parent.mkdir(parents=True)
    target.write_text("source text", encoding="utf-8")
    events = []

    async def emit(event):
        events.append(event)

    answer = await harness.run_agent(
        user_id="u",
        session_id="s",
        run_id=str(uuid4()),
        prompt="bad edits",
        sandbox=FilesystemBackend(root_dir=root),
        emit=emit,
        settings=SimpleNamespace(
            checkpoint_path=tmp_path / "db.sqlite", mcp_enabled=False
        ),
    )
    assert answer == "validated"
    assert target.read_text() == "source text"
    terminal = {e["id"]: e for e in events}
    failures = sorted(e["label"] for e in terminal.values() if e["status"] == "failed")
    assert failures == ["edit_file", "read_file", "unregistered_tool"]
    # Legacy/adapted error strings can arrive in a default-success ToolMessage.
    adapted = ToolMessage(name="edit_file", content="Error: no match", tool_call_id="c")
    assert tool_result(adapted)[0] == "failed"
    stdout = ToolMessage(
        name="execute",
        content="Error: is a normal log",
        tool_call_id="c",
        artifact={"exit_code": 0},
    )
    assert tool_result(stdout)[0] == "completed"


async def test_malformed_model_tool_arguments_are_visible():
    from langchain_core.outputs import LLMResult

    from famsun.trace import ActivityTrace

    events = []

    async def emit(event):
        events.append(event)

    trace = ActivityTrace("r", emit)
    run_id = uuid4()
    await trace.on_chat_model_start({}, [], run_id=run_id)
    malformed = AIMessage(
        content="",
        invalid_tool_calls=[
            {
                "name": "execute",
                "args": "{broken",
                "id": "bad",
                "error": "bad json",
            }
        ],
    )
    await trace.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=malformed)]]), run_id=run_id
    )
    assert [
        (e["label"], e["status"], e["parent_id"]) for e in events if e["kind"] == "tool"
    ] == [("execute", "failed", str(run_id))]


async def test_soft_budget_in_live_graph_before_hard_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "build_model", lambda settings: ScriptedChat())
    events = []

    async def emit(event):
        events.append(event)

    result = await harness.run_agent(
        user_id="u",
        session_id="s",
        run_id="limited",
        prompt="use time budget",
        sandbox=FilesystemBackend(root_dir=tmp_path),
        emit=emit,
        settings=SimpleNamespace(
            checkpoint_path=tmp_path / "budget.db",
            mcp_enabled=False,
            graph_recursion_limit=36,
            run_timeout=60,
        ),
    )
    assert result == "verified workspace listing"
    assert any(e["label"] == "ls" and e["status"] == "completed" for e in events)
    assert (
        sum(e["kind"] == "notice" and e["label"] == "接近运行预算" for e in events) == 1
    )
    assert events[-1]["status"] == "completed"


async def test_writable_memory_reloads_after_checkpoint_and_is_private(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(harness, "build_model", lambda settings: ScriptedChat())
    settings = SimpleNamespace(
        checkpoint_path=tmp_path / "memory.db", mcp_enabled=False
    )
    events = []

    async def emit(event):
        events.append(event)

    async def turn(owner, session, prompt):
        directory = tmp_path / owner / session
        if not directory.exists():
            target = directory / POLICY.lstrip("/")
            target.parent.mkdir(parents=True)
            target.write_text("Read-only engineering policy")
        return await harness.run_agent(
            user_id=owner,
            session_id=session,
            run_id=str(uuid4()),
            prompt=prompt,
            sandbox=FilesystemBackend(root_dir=directory),
            emit=emit,
            settings=settings,
        )

    assert await turn("one", "thread", "save my unit preference") == "validated"
    assert any(e["label"] == "edit_file" and e["status"] == "completed" for e in events)
    root = tmp_path / "one/thread"
    assert "micrometres" in (root / PREFERENCES.lstrip("/")).read_text()
    assert (root / POLICY.lstrip("/")).read_text() == "Read-only engineering policy"
    assert await turn("one", "thread", "read preferences from system") == "micrometres"
    assert await turn("one", "other", "read preferences from system") == "default"
    assert await turn("two", "thread", "read preferences from system") == "default"
