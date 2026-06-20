import os
import asyncio
import logging
import time
import json
import re
from typing import Annotated

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
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


def _last_user_text(messages: list[AnyMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = getattr(msg, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def _is_write_intent(text: str) -> bool:
    t = text.lower()
    write_keywords = (
        "write",
        "create",
        "save",
        "make",
        "generate",
    )
    return ("file" in t or "." in t or "directory" in t or "path" in t) and any(
        k in t for k in write_keywords
    )


def _extract_target_path(text: str) -> str | None:
    # Prefer quoted paths first.
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", text)
    for candidate in quoted:
        c = candidate.strip()
        if "/" in c or c.endswith((".html", ".htm", ".css", ".js", ".ts", ".tsx", ".py", ".md", ".txt", ".json")):
            return c

    # Then unquoted absolute/relative file paths.
    patterns = [
        r"(/workspace-data/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)",
        r"([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)",
    ]
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            return match.group(1)
    return None


def _extract_code_block(text: str) -> str | None:
    match = re.search(r"```[a-zA-Z0-9_-]*\n([\s\S]*?)```", text)
    if match:
        code = match.group(1).strip("\n")
        return code if code else None
    return None


def _normalize_generated_code(code: str) -> str:
    stripped = code.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return code

    if isinstance(payload, dict):
        name = payload.get("name")
        arguments = payload.get("arguments") or payload.get("args")
        if name == "write_text_file" and isinstance(arguments, dict):
            content = arguments.get("content")
            if isinstance(content, str):
                return content
    return code


def _find_tool(name: str):
    for tool in _MCP_TOOLS:
        if getattr(tool, "name", None) == name:
            return tool
    return None


def _invoke_tool(tool_name: str, tool_args: dict):
    tool = _find_tool(tool_name)
    if tool is None:
        raise ValueError(f"Tool '{tool_name}' is not available")
    if hasattr(tool, "ainvoke"):
        return _run_async(tool.ainvoke(tool_args))
    return tool.invoke(tool_args)


def _verify_text_file_exists(path: str) -> bool:
    try:
        _invoke_tool("read_text_file", {"path": path})
        return True
    except Exception:  # noqa: BLE001
        return False


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
    last_user_text = _last_user_text(state["messages"])
    is_write_intent = _is_write_intent(last_user_text)
    target_path = _extract_target_path(last_user_text) if is_write_intent else None
    tool_agent = _get_tool_agent() if _should_use_tools(state["messages"]) else None
    if is_write_intent and target_path and tool_agent is None:
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"Write requested for {target_path}, but filesystem tools are unavailable. "
                        "Please retry after MCP tools are attached."
                    )
                )
            ]
        }

    if tool_agent is not None:
        forced_messages = [
            SystemMessage(
                content=(
                    "When a user asks to create, write, or save files, you must use filesystem tools "
                    "to perform the write operation. Do not only return code in chat."
                )
            ),
            *state["messages"],
        ]
        result = tool_agent.invoke({"messages": forced_messages})
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

                try:
                    tool_result = _invoke_tool(tool_name, tool_args)
                    response = AIMessage(content=_tool_result_to_text(tool_result))
                except Exception as exc:  # noqa: BLE001
                    response = AIMessage(content=f"Tool '{tool_name}' failed: {exc}")

        # Fallback for weaker models: if user asked to write a file and the model only returned code,
        # persist that code to the requested path.
        response_text = getattr(response, "content", "")
        if isinstance(response_text, str) and is_write_intent:
            code = _extract_code_block(response_text)
            if target_path and code:
                code = _normalize_generated_code(code)
                try:
                    write_result = _invoke_tool(
                        "write_text_file",
                        {
                            "path": target_path,
                            "content": code,
                            "create_parents": True,
                        },
                    )
                    response = AIMessage(
                        content=(
                            f"Wrote file to {target_path}.\n"
                            f"Result: {_tool_result_to_text(write_result)}"
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    response = AIMessage(content=f"Failed to write {target_path}: {exc}")

        if is_write_intent and target_path and not _verify_text_file_exists(target_path):
            response = AIMessage(
                content=(
                    f"Write requested for {target_path}, but no file was created. "
                    "Please include explicit file content or a code block so it can be written."
                )
            )
    else:
        response = _model.invoke(state["messages"])
    return {"messages": [response]}


builder = StateGraph(GraphState)
builder.add_node("chat", chat_node)
builder.set_entry_point("chat")
builder.add_edge("chat", END)
graph = builder.compile()
