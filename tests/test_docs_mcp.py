from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from famsun import docs_mcp
from famsun.config import Settings


@pytest.mark.parametrize("failure", [None, "server", "connection"])
async def test_explicit_search_uses_one_session_and_reports_errors(
    monkeypatch, failure
):
    monkeypatch.delenv("FAMSUN_MCP_ENABLED", raising=False)
    assert not Settings().mcp_enabled
    call = AsyncMock(
        return_value=SimpleNamespace(
            isError=failure == "server",
            content=[SimpleNamespace(type="text", text="A documented API")],
        )
    )

    @asynccontextmanager
    async def session(name):
        assert name == "langchain"
        if failure == "connection":
            raise OSError("private diagnostic")
        yield SimpleNamespace(call_tool=call)

    monkeypatch.setattr(
        docs_mcp, "docs_client", lambda: SimpleNamespace(session=session)
    )
    result = await docs_mcp.search_docs_by_lang_chain.ainvoke(
        {
            "type": "tool_call",
            "id": "doc-1",
            "name": docs_mcp.SEARCH,
            "args": {"query": "Deep Agents streaming"},
        }
    )
    if failure:
        assert result.status == "error"
        assert "private diagnostic" not in result.content
    else:
        assert result.status == "success" and result.content == "A documented API"
    if failure != "connection":
        call.assert_awaited_once_with(
            docs_mcp.SEARCH, {"query": "Deep Agents streaming"}
        )
