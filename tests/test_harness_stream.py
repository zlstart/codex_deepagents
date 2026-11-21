"""Native ChatOpenAI callbacks and safe ordered streaming in the actual Deep Agent."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, LLMResult
from langchain_openai import ChatOpenAI

from famsun import harness, trace_stream
from famsun.public_text import public_text
from famsun.trace import ActivityTrace


def texts(events, run_id=None):
    return "".join(
        p["delta"]
        for e in events
        if (p := e.get("payload", {})).get("type") == "model_text"
        and (run_id is None or e["id"] == str(run_id))
    )


def final(text):
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content=text))]])


def chunk(content, **kwargs):
    return ChatGenerationChunk(message=AIMessageChunk(content=content, **kwargs))


async def test_native_sse_model_interleaves_public_text_parallel_tools_and_final(
    tmp_path, monkeypatch
):
    """Yield SSE via the real OpenAI client, pausing upstream until a delta is visible."""
    gate, first_public = asyncio.Event(), asyncio.Event()
    events = []
    secret = "opaque-test-credential"
    calls = 0

    class TransportStream(httpx.AsyncByteStream):
        def __init__(self, first):
            self.first = first

        async def __aiter__(self):
            def packet(delta, reason=None):
                obj = {
                    "id": "chat-1",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "gpt-4o-mini",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
                }
                return ("data: " + json.dumps(obj) + "\n\n").encode()

            if self.first:
                yield packet(
                    {
                        "role": "assistant",
                        "reasoning_content": "PRIVATE_PROVIDER",
                        "content": "<th",
                    }
                )
                yield packet({"content": "ink>PRIVATE_INLINE</thi"})
                yield packet({"content": "nk>开始核对。 "})
                await gate.wait()
                yield packet(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "a",
                                "type": "function",
                                "function": {"name": "ls", "arguments": '{"path"'},
                            },
                            {
                                "index": 1,
                                "id": "b",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"file_path"',
                                },
                            },
                        ]
                    }
                )
                yield packet(
                    {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": ':"/"}'}},
                            {"index": 1, "function": {"arguments": ':"/spec.txt"}'}},
                        ]
                    },
                    "tool_calls",
                )
            else:
                yield packet({"role": "assistant", "content": "已核验。 token=some-"})
                yield packet({"content": "secret-value。 code " + secret[:8]})
                yield packet({"content": secret[8:] + " finished."}, "stop")
            yield b"data: [DONE]\n\n"

    async def api(request):
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        assert body["stream"] is True
        if calls == 2:
            assert {
                m["tool_call_id"] for m in body["messages"] if m["role"] == "tool"
            } == {"a", "b"}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=TransportStream(calls == 1),
        )

    async def emit(e):
        events.append(e)
        if "开始核对" in texts([e]):
            first_public.set()

    (tmp_path / "spec.txt").write_text("measured", encoding="utf-8")
    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
        model = ChatOpenAI(
            api_key=secret,
            base_url="https://model.invalid/v1",
            model="gpt-4o-mini",
            http_async_client=client,
            streaming=True,
            use_responses_api=False,
            max_retries=0,
        )
        monkeypatch.setattr(harness, "build_model", lambda _: model)
        job = asyncio.create_task(
            harness.run_agent(
                user_id="u",
                session_id="s",
                run_id="r",
                prompt="check",
                sandbox=FilesystemBackend(root_dir=tmp_path),
                emit=emit,
                settings=SimpleNamespace(
                    mcp_enabled=False,
                    checkpoint_path=tmp_path / "state.sqlite",
                    llm_api_key=secret,
                ),
            )
        )
        try:
            await asyncio.wait_for(first_public.wait(), 4)
            assert not job.done()
        finally:
            gate.set()
        answer = await asyncio.wait_for(job, 8)
    assert calls == 2
    raw = json.dumps([events, answer], ensure_ascii=False)
    for hidden in (
        "PRIVATE_PROVIDER",
        "PRIVATE_INLINE",
        "<think>",
        "some-secret-value",
        secret,
    ):
        assert hidden not in raw
    ends = [e for e in events if e.get("payload", {}).get("type") == "model_end"]
    assert [e["payload"]["has_tool_calls"] for e in ends] == [True, False]
    assert texts(events, ends[0]["id"]) == "开始核对。 "
    assert texts(events, ends[1]["id"]).strip() == answer
    tools = [e for e in events if e["kind"] == "tool" and e["status"] == "completed"]
    assert {e["label"] for e in tools} == {"ls", "read_file"}
    assert all(
        e["parent_id"] == "r/agent"
        and "arguments" in e["payload"]
        and "result" in e["payload"]
        for e in tools
    )
    assert events.index(ends[0]) < min(
        i for i, e in enumerate(events) if e["kind"] == "tool"
    )
    assert max(events.index(t) for t in tools) < events.index(ends[1])


async def test_fallback_and_private_calls_and_blocks():
    events = []

    async def emit(e):
        events.append(e)

    trace = ActivityTrace("r", emit)
    for metadata, tags in [
        ({"lc_source": "summarization"}, []),
        ({"lc_internal_call": "marker"}, []),
        ({}, ["nostream"]),
    ]:
        rid = uuid4()
        await trace.on_chat_model_start(
            {}, [], run_id=rid, metadata=metadata, tags=tags
        )
        await trace.on_llm_new_token(
            "PRIVATE_INTERNAL", chunk=chunk("PRIVATE_INTERNAL"), run_id=rid
        )
        await trace.on_llm_end(final("PRIVATE_INTERNAL"), run_id=rid)
        assert not texts(events, rid)
    rid = uuid4()
    await trace.on_chat_model_start({}, [], run_id=rid)
    await trace.on_llm_new_token("PRIVATE_UNTYPED", run_id=rid)
    content = [
        {"type": "reasoning", "summary": "PRIVATE_REASON"},
        {"type": "thinking", "thinking": "PRIVATE_BLOCK"},
        {"type": "text", "thought": True, "text": "PRIVATE_THOUGHT"},
        {"type": "text", "text": "captured without streaming"},
    ]
    await trace.on_llm_end(final(content), run_id=rid)
    assert texts(events, rid) == "captured without streaming"
    assert "PRIVATE_" not in json.dumps(events)
    assert (
        public_text(AIMessage(content="<analysis>not shown</analysis>visible"))
        == "visible"
    )


async def test_parallel_branches_cancel_flush_and_bounded_replay(monkeypatch):
    monkeypatch.setattr(trace_stream, "MODEL_CHARS", 24)
    monkeypatch.setattr(trace_stream, "DELTA_CHARS", 6)
    events = []

    async def emit(e):
        await asyncio.sleep(0)
        events.append(e)

    trace = ActivityTrace("r", emit)
    subs = [uuid4(), uuid4()]
    models = [uuid4(), uuid4()]
    for sub, model in zip(subs, models, strict=True):
        await trace.on_tool_start(
            {"name": "task"}, "", run_id=sub, inputs={"subagent_type": "engineering"}
        )
        await trace.on_chat_model_start({}, [], run_id=model, parent_run_id=sub)
    first, second = models
    await trace.on_llm_new_token("", chunk=chunk("first branch partial"), run_id=first)
    await trace.on_llm_new_token("", chunk=chunk("second branch " * 30), run_id=second)
    await asyncio.gather(
        trace.on_llm_error(asyncio.CancelledError(), run_id=first),
        trace.on_llm_end(final("second branch " * 30), run_id=second),
    )
    assert texts(events, first) == "first branch partial"
    assert texts(events, second) == ("second branch " * 30)[:24]
    assert any(e.get("payload", {}).get("truncated") for e in events)
    for model, sub in zip(models, subs, strict=True):
        rows = [e for e in events if e["id"] == str(model)]
        assert all(r["parent_id"] == str(sub) for r in rows)
        assert rows[-1]["status"] == ("cancelled" if model == first else "completed")
    await trace.close_pending("cancelled")
    assert not trace.streams
    assert all(
        e["payload"]["error"] == "调用已中断"
        for e in events
        if e["kind"] == "subagent" and e["status"] == "cancelled"
    )


async def test_cancellation_hides_incomplete_configured_key():
    events = []

    async def emit(event):
        events.append(event)

    trace = ActivityTrace("r", emit, secrets=("opaque-test-key",))
    rid = uuid4()
    await trace.on_chat_model_start({}, [], run_id=rid)
    await trace.on_llm_new_token("", chunk=chunk("progress opaque-test"), run_id=rid)
    await trace.on_llm_error(asyncio.CancelledError(), run_id=rid)
    assert texts(events) == "progress [已隐藏]"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-splitcredential-1234",
        "GITHUB_TOKEN=ghp_abcdefghijk99",
        'api_key="SECRET WITH SPACES"',
        "密码：secret-value",
    ],
)
async def test_every_chunk_boundary_keeps_credentials_out(secret):
    events = []

    async def emit(e):
        events.append(e)

    trace = ActivityTrace("r", emit)
    rid = uuid4()
    await trace.on_chat_model_start({}, [], run_id=rid)
    full = "准备。 " + secret + " 完成。"
    for c in full:
        await trace.on_llm_new_token("", chunk=chunk(c), run_id=rid)
        await trace.streams[rid].drain()
    await trace.on_llm_end(final(full), run_id=rid)
    assert texts(events) == public_text(AIMessage(content=full))
    assert secret not in texts(events)


async def test_nonstream_budget_answer_filters_thoughts_and_known_secret(
    tmp_path, monkeypatch
):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatResult

    from famsun.runtime import BudgetClosed, run_deadline

    class Reply(BaseChatModel):
        @property
        def _llm_type(self):
            return "test"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            message = AIMessage(
                content="<think>PRIVATE_BUDGET</think>公开部分 opaque-config-secret。"
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

    monkeypatch.setattr(harness, "build_model", lambda _: Reply())
    events = []

    async def emit(event):
        events.append(event)

    cfg = SimpleNamespace(
        mcp_enabled=False,
        checkpoint_path=tmp_path / "ck.sqlite",
        llm_api_key="opaque-config-secret",
        run_timeout=300,
    )
    token = run_deadline.set(asyncio.get_running_loop().time() + 15)
    try:
        with pytest.raises(BudgetClosed) as exc:
            await harness.run_agent(
                user_id="u",
                session_id="s",
                run_id="b",
                prompt="check",
                emit=emit,
                sandbox=FilesystemBackend(root_dir=tmp_path),
                settings=cfg,
            )
    finally:
        run_deadline.reset(token)
    assert exc.value.answer == "公开部分 [已隐藏]。"
    assert texts(events) == exc.value.answer
    assert "PRIVATE_BUDGET" not in json.dumps(events)


async def test_shared_run_quota_and_rejected_tool_arguments():
    from langchain_core.messages import ToolMessage

    events = []

    async def emit(event):
        events.append(event)

    trace = ActivityTrace("r", emit)
    trace.remaining_text = 7
    for expected in ["abcdef", "ghijkl"]:
        rid = uuid4()
        await trace.on_chat_model_start({}, [], run_id=rid)
        await trace.on_llm_end(final(expected), run_id=rid)
    assert texts(events) == "abcdefg"
    assert sum(e.get("payload", {}).get("truncated", False) for e in events) == 1
    rejected = uuid4()
    await trace.on_chain_start(
        {},
        [{"id": "bad", "args": {"path": "/hello", "token": "do-not-emit"}}],
        run_id=rejected,
        name="tools",
        metadata={"langgraph_node": "tools"},
    )
    await trace.on_chain_end(
        {
            "messages": [
                ToolMessage(
                    content="Error: unavailable",
                    tool_call_id="bad",
                    name="not_registered",
                    status="error",
                )
            ]
        },
        run_id=rejected,
    )
    payload = events[-1]["payload"]
    assert payload["arguments"] == {"path": "/hello", "token": "[已隐藏]"}
    assert payload["result"] == "Error: unavailable"
