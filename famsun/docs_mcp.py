"""Optional read-only access to the official LangChain documentation server."""

import asyncio

import httpx
from langchain_core.tools import ToolException, tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.shared.exceptions import McpError

DOCS_MCP = "https://docs.langchain.com/mcp"
SEARCH = "search_docs_by_lang_chain"


def docs_client():
    return MultiServerMCPClient(
        {
            "langchain": {
                "transport": "streamable_http",
                "url": DOCS_MCP,
                "timeout": 15.0,
                "sse_read_timeout": 45.0,
            }
        }
    )


async def probe_tools():
    async with asyncio.timeout(20):
        tools = await docs_client().get_tools()
    if not any(t.name == SEARCH for t in tools):
        raise RuntimeError("官方文档服务缺少检索工具")
    return [SEARCH]


@tool
async def search_docs_by_lang_chain(query: str) -> str:
    """Look up LangChain, LangGraph or Deep Agents APIs in their official documentation.

    Use only for questions about these libraries. Send a focused documentation query.
    """
    try:
        async with asyncio.timeout(45), docs_client().session("langchain") as session:
            result = await session.call_tool(SEARCH, {"query": query})
    except (TimeoutError, OSError, httpx.HTTPError, ExceptionGroup, McpError) as exc:
        raise ToolException("LangChain 官方文档服务暂时无法连接。") from exc
    if result.isError:
        raise ToolException("LangChain 官方文档服务未能完成查询。")
    return "\n\n".join(block.text for block in result.content if block.type == "text")


# A failed optional lookup is a failed tool result; the agent can still finish the turn.
search_docs_by_lang_chain.handle_tool_error = True
