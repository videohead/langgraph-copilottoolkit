import os
import asyncio
import logging
import time
import json
from typing import Annotated

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
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

_TOOL_AGENT = None
_MCP_TOOLS = []
_LAST_MCP_ATTEMPT = 0.0
_MCP_RETRY_SECONDS = float(os.environ.get("MCP_TOOL_RETRY_SECONDS", "10"))


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


def _message_has_text(msg: AnyMessage) -> bool:
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                return True
            if isinstance(item, str) and item.strip():
                return True
    return False


def _extract_last_ai_message(messages):
    for msg in reversed(messages):
        if getattr(msg, "type", "") == "ai" and _message_has_text(msg):
            return msg
    return None


def _tool_result_to_text(result) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts: list[str] = []
        for item in result:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
                elif item:
                    parts.append(json.dumps(item, ensure_ascii=True))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=True)
    return str(result)


def _should_use_tools(messages: list[AnyMessage]) -> bool:
    last_user_text = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = getattr(msg, "content", "")
            last_user_text = content if isinstance(content, str) else str(content)
            break

    text = last_user_text.lower()
    if not text:
        return False

    # Keep basic chat conversational unless there is clear filesystem intent.
    file_keywords = (
        "file",
        "folder",
        "directory",
        "path",
        "read",
        "write",
        "append",
        "create",
        "delete",
        "rename",
        "move",
        "list",
        "workspace",
        ".txt",
        ".md",
        "/workspace-data",
    )
    return any(k in text for k in file_keywords)


def _get_tool_agent():
    global _TOOL_AGENT, _MCP_TOOLS, _LAST_MCP_ATTEMPT

    if _TOOL_AGENT is not None:
        return _TOOL_AGENT

    now = time.monotonic()
    if now - _LAST_MCP_ATTEMPT < _MCP_RETRY_SECONDS:
        return None

    _LAST_MCP_ATTEMPT = now
    tools = _run_async(_load_mcp_tools())
    if tools:
        _MCP_TOOLS = tools
        _TOOL_AGENT = create_react_agent(_model, tools)
        _LOG.info("Loaded %d MCP tools for basic graph", len(tools))
        return _TOOL_AGENT

    return None


def chat_node(state: GraphState) -> GraphState:
    tool_agent = _get_tool_agent() if _should_use_tools(state["messages"]) else None
    if tool_agent is not None:
        result = tool_agent.invoke({"messages": state["messages"]})
        msgs = result.get("messages", []) if isinstance(result, dict) else []
        response = _extract_last_ai_message(msgs) or _model.invoke(state["messages"])

        # Some local models emit a JSON tool-call string instead of producing a final answer.
        # Execute the requested tool directly and return its output as assistant content.
        if isinstance(getattr(response, "content", None), str):
            try:
                payload = json.loads(response.content)
            except json.JSONDecodeError:
                payload = None

            if isinstance(payload, dict) and isinstance(payload.get("name"), str):
                tool_name = payload["name"]
                tool_args = payload.get("arguments") or payload.get("args") or {}
                if not isinstance(tool_args, dict):
                    tool_args = {}

                for tool in _MCP_TOOLS:
                    if getattr(tool, "name", None) == tool_name:
                        try:
                            if hasattr(tool, "ainvoke"):
                                tool_result = _run_async(tool.ainvoke(tool_args))
                            else:
                                tool_result = tool.invoke(tool_args)
                            response = AIMessage(content=_tool_result_to_text(tool_result))
                        except Exception as exc:  # noqa: BLE001
                            response = AIMessage(content=f"Tool '{tool_name}' failed: {exc}")
                        break
    else:
        response = _model.invoke(state["messages"])
    return {"messages": [response]}


builder = StateGraph(GraphState)
builder.add_node("chat", chat_node)
builder.set_entry_point("chat")
builder.add_edge("chat", END)
graph = builder.compile()
