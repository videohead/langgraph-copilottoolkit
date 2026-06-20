import os
import asyncio
import logging
from typing import Annotated

from langchain_core.messages import AnyMessage, HumanMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from typing_extensions import TypedDict

try:
    from langchain_mcp_adapters.client import MultiServerMCPClient
except Exception:  # noqa: BLE001
    MultiServerMCPClient = None


class GraphState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


_LOG = logging.getLogger(__name__)

_model = ChatOllama(
    base_url=os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"),
    model=os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"),
)


def _run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


async def _load_mcp_tools():
    if MultiServerMCPClient is None:
        _LOG.warning("langchain-mcp-adapters is unavailable; MCP tools disabled")
        return []

    if os.environ.get("MCP_FILESYSTEM_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return []

    mcp_url = os.environ.get("MCP_FILESYSTEM_URL", "http://mcp-filesystem:8765/mcp")
    client = MultiServerMCPClient(
        {
            "filesystem": {
                "transport": "http",
                "url": mcp_url,
            }
        }
    )
    try:
        return await client.get_tools()
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Unable to load MCP filesystem tools from %s: %s", mcp_url, exc)
        return []


def chat_node(state: GraphState) -> GraphState:
    response = _model.invoke(state["messages"])
    return {"messages": [response]}


_mcp_tools = _run_async(_load_mcp_tools())

if _mcp_tools:
    graph = create_react_agent(_model, _mcp_tools)
else:
    builder = StateGraph(GraphState)
    builder.add_node("chat", chat_node)
    builder.set_entry_point("chat")
    builder.add_edge("chat", END)
    graph = builder.compile()
