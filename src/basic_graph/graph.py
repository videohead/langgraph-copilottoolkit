import os
import asyncio
import logging
import time
import json
import re
from pathlib import PurePosixPath
from typing import Annotated

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from src.checkpointing import get_checkpointer
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
_TOOL_AGENTS_BY_POLICY = {}
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


def _humanize_tool_payload_text(text: str) -> str | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    if "path" in payload and "bytes_written" in payload:
        return f"Wrote file to {payload['path']} ({payload['bytes_written']} bytes)."
    if "path" in payload and "bytes_appended" in payload:
        return f"Appended to {payload['path']} ({payload['bytes_appended']} bytes)."
    if "deleted" in payload:
        return f"Deleted {payload['deleted']}."
    if "from" in payload and "to" in payload:
        return f"Moved {payload['from']} to {payload['to']}."
    if "path" in payload:
        return f"Updated {payload['path']}."

    return None


def _find_tool(name: str):
    for tool in _MCP_TOOLS:
        if getattr(tool, "name", None) == name:
            return tool
    return None


def _invoke_tool(tool_name: str, tool_args: dict, tools: list | None = None):
    search_tools = tools if tools is not None else _MCP_TOOLS
    tool_obj = None
    for candidate in search_tools:
        if getattr(candidate, "name", None) == tool_name:
            tool_obj = candidate
            break

    tool = tool_obj
    if tool is None:
        raise ValueError(f"Tool '{tool_name}' is not available")
    if hasattr(tool, "ainvoke"):
        return _run_async(tool.ainvoke(tool_args))
    return tool.invoke(tool_args)


def _verify_text_file_exists(path: str, tools: list | None = None) -> bool:
    try:
        _invoke_tool("read_text_file", {"path": path}, tools)
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


def _normalize_root_path(path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return "/workspace-data"
    if not raw.startswith("/"):
        raw = f"/workspace-data/{raw.lstrip('/')}"
    normalized = str(PurePosixPath(raw))
    if not normalized.startswith("/workspace-data"):
        return "/workspace-data"
    return normalized.rstrip("/") or "/workspace-data"


def _to_absolute_tool_path(path: str, mcp_root: str = "/workspace-data") -> str:
    raw = (path or "").strip()
    if raw in {"", "."}:
        return mcp_root
    if raw.startswith("/"):
        return str(PurePosixPath(raw))
    return str(PurePosixPath(f"{mcp_root.rstrip('/')}/{raw}"))


def _is_within_root(path: str, root: str) -> bool:
    p = str(PurePosixPath(path))
    r = str(PurePosixPath(root))
    return p == r or p.startswith(f"{r}/")


def _extract_profile_context(messages: list[AnyMessage]) -> dict:
    context = {
        "selected_root": "/workspace-data",
        "allowed_roots": ["/workspace-data"],
        "tool_mode": "read_write",
    }
    marker_prefix = "[project-profile:"
    for msg in messages:
        if not isinstance(msg, SystemMessage):
            continue
        content = getattr(msg, "content", "")
        if not isinstance(content, str) or marker_prefix not in content:
            continue

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        for line in lines:
            if line.lower().startswith("filesystem root:"):
                context["selected_root"] = _normalize_root_path(line.split(":", 1)[1].strip())
            elif line.lower().startswith("allowed roots:"):
                raw = line.split(":", 1)[1].strip()
                roots = [_normalize_root_path(r.strip()) for r in raw.split(",") if r.strip()]
                if roots:
                    context["allowed_roots"] = roots
            elif line.lower().startswith("tool mode:"):
                mode = line.split(":", 1)[1].strip().lower()
                context["tool_mode"] = mode or "read_write"

        if context["selected_root"] not in context["allowed_roots"]:
            context["allowed_roots"].append(context["selected_root"])
        return context

    return context


def _build_guarded_tools(policy: dict, base_tools: list):
    selected_root = _normalize_root_path(policy.get("selected_root", "/workspace-data"))
    tool_mode = str(policy.get("tool_mode", "read_write")).lower()
    read_only = tool_mode == "read_only"

    def enforce_path(path: str):
        abs_path = _to_absolute_tool_path(path)
        if not _is_within_root(abs_path, selected_root):
            raise PermissionError(
                f"Path '{abs_path}' is outside selected filesystem root '{selected_root}'"
            )
        return abs_path

    def enforce_write_allowed():
        if read_only:
            raise PermissionError("Profile tool mode is read_only; write operations are blocked")

    @tool("get_root")
    def guarded_get_root() -> str:
        """Return the currently enforced filesystem root for this run."""
        return selected_root

    @tool("list_directory")
    def guarded_list_directory(path: str = "."):
        """List files and folders under a directory in the selected filesystem root."""
        safe_path = enforce_path(path)
        return _invoke_tool("list_directory", {"path": safe_path}, base_tools)

    @tool("read_text_file")
    def guarded_read_text_file(path: str):
        """Read a UTF-8 text file from the selected filesystem root."""
        safe_path = enforce_path(path)
        return _invoke_tool("read_text_file", {"path": safe_path}, base_tools)

    @tool("write_text_file")
    def guarded_write_text_file(path: str, content: str, create_parents: bool = True):
        """Create or overwrite a UTF-8 text file inside the selected filesystem root."""
        enforce_write_allowed()
        safe_path = enforce_path(path)
        return _invoke_tool(
            "write_text_file",
            {"path": safe_path, "content": content, "create_parents": create_parents},
            base_tools,
        )

    @tool("append_text_file")
    def guarded_append_text_file(path: str, content: str, create_parents: bool = True):
        """Append UTF-8 text to a file in the selected filesystem root."""
        enforce_write_allowed()
        safe_path = enforce_path(path)
        return _invoke_tool(
            "append_text_file",
            {"path": safe_path, "content": content, "create_parents": create_parents},
            base_tools,
        )

    @tool("make_directory")
    def guarded_make_directory(path: str, exist_ok: bool = True):
        """Create a directory inside the selected filesystem root."""
        enforce_write_allowed()
        safe_path = enforce_path(path)
        return _invoke_tool("make_directory", {"path": safe_path, "exist_ok": exist_ok}, base_tools)

    @tool("move_path")
    def guarded_move_path(src: str, dst: str, overwrite: bool = False):
        """Move or rename a file/folder inside the selected filesystem root."""
        enforce_write_allowed()
        safe_src = enforce_path(src)
        safe_dst = enforce_path(dst)
        return _invoke_tool(
            "move_path",
            {"src": safe_src, "dst": safe_dst, "overwrite": overwrite},
            base_tools,
        )

    @tool("delete_path")
    def guarded_delete_path(path: str, recursive: bool = True):
        """Delete a file or directory in the selected filesystem root."""
        enforce_write_allowed()
        safe_path = enforce_path(path)
        return _invoke_tool("delete_path", {"path": safe_path, "recursive": recursive}, base_tools)

    return [
        guarded_get_root,
        guarded_list_directory,
        guarded_read_text_file,
        guarded_write_text_file,
        guarded_append_text_file,
        guarded_make_directory,
        guarded_move_path,
        guarded_delete_path,
    ]


def _get_policy_key(policy: dict) -> tuple:
    selected_root = _normalize_root_path(policy.get("selected_root", "/workspace-data"))
    tool_mode = str(policy.get("tool_mode", "read_write")).lower()
    return selected_root, tool_mode


def _get_tool_agent_for_policy(policy: dict):
    base_agent = _get_tool_agent()
    if base_agent is None or not _MCP_TOOLS:
        return None, []

    key = _get_policy_key(policy)
    cached = _TOOL_AGENTS_BY_POLICY.get(key)
    if cached is not None:
        return cached["agent"], cached["tools"]

    guarded_tools = _build_guarded_tools(policy, _MCP_TOOLS)
    guarded_agent = create_react_agent(_model, guarded_tools)
    _TOOL_AGENTS_BY_POLICY[key] = {"agent": guarded_agent, "tools": guarded_tools}
    return guarded_agent, guarded_tools


def chat_node(state: GraphState) -> GraphState:
    last_user_text = _last_user_text(state["messages"])
    profile_policy = _extract_profile_context(state["messages"])
    selected_root = profile_policy.get("selected_root", "/workspace-data")
    is_write_intent = _is_write_intent(last_user_text)
    target_path = _extract_target_path(last_user_text) if is_write_intent else None
    tool_agent = None
    effective_tools = []
    if _should_use_tools(state["messages"]):
        tool_agent, effective_tools = _get_tool_agent_for_policy(profile_policy)

    if is_write_intent and target_path:
        abs_target = _to_absolute_tool_path(target_path)
        if not _is_within_root(abs_target, selected_root):
            return {
                "messages": [
                    AIMessage(
                        content=(
                            f"Refused to write outside selected filesystem root. "
                            f"Selected root: {selected_root}. Requested path: {abs_target}."
                        )
                    )
                ]
            }

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
                    tool_result = _invoke_tool(tool_name, tool_args, effective_tools)
                    response = AIMessage(content=_tool_result_to_text(tool_result))
                except Exception as exc:  # noqa: BLE001
                    response = AIMessage(content=f"Tool '{tool_name}' failed: {exc}")

        # Fallback for weaker models: if user asked to write a file and the model only returned code,
        # persist that code to the requested path.
        response_text = getattr(response, "content", "")
        if isinstance(response_text, str):
            friendly = _humanize_tool_payload_text(response_text)
            if friendly:
                response = AIMessage(content=friendly)
                response_text = friendly

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
                        effective_tools,
                    )
                    response = AIMessage(
                        content=(
                            f"Wrote file to {target_path}.\n"
                            f"Result: {_tool_result_to_text(write_result)}"
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    response = AIMessage(content=f"Failed to write {target_path}: {exc}")

        if is_write_intent and target_path and not _verify_text_file_exists(target_path, effective_tools):
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
graph = builder.compile(checkpointer=get_checkpointer())
