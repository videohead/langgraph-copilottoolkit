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


class SwarmState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    task: str
    plan: str
    draft: str
    review: str
    final: str


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
_FS_TOOL_NAMES = {
    "get_root",
    "list_directory",
    "read_text_file",
    "write_text_file",
    "append_text_file",
    "make_directory",
    "move_path",
    "delete_path",
    "get_shell_policy",
    "run_shell",
}


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
        _LOG.warning("langchain-mcp-adapters is unavailable; swarm MCP tools disabled")
        return []

    if os.environ.get("MCP_FILESYSTEM_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return []

    filesystem_url = os.environ.get("MCP_FILESYSTEM_URL", "http://mcp-filesystem:8765/mcp")
    servers = {
        "filesystem": {
            "transport": "http",
            "url": filesystem_url,
        }
    }
    if os.environ.get("MCP_SHELL_ENABLED", "true").lower() in {"1", "true", "yes"}:
        shell_url = os.environ.get("MCP_SHELL_URL", "http://mcp-shell:8770/mcp")
        servers["shell"] = {
            "transport": "http",
            "url": shell_url,
        }

    client = MultiServerMCPClient(servers)
    try:
        return await client.get_tools()
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Unable to load swarm MCP tools from %s: %s", servers, exc)
        return []


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
        _LOG.info("Loaded %d MCP tools for swarm graph", len(tools))
        return _TOOL_AGENT

    return None


def _last_user_text(messages: list[AnyMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content
    return ""


def _chat_context(messages: list[AnyMessage], limit: int = 8) -> str:
    rows = []
    for msg in messages[-limit:]:
        if isinstance(msg, HumanMessage):
            role = "user"
        elif isinstance(msg, AIMessage):
            role = "assistant"
        elif isinstance(msg, SystemMessage):
            role = "system"
        else:
            role = "message"
        rows.append(f"{role}: {msg.content}")
    return "\n".join(rows)


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


def _normalize_tool_calls(payload) -> list[dict]:
    calls: list[dict] = []
    if isinstance(payload, dict):
        name = payload.get("name")
        args = payload.get("arguments") or payload.get("args") or {}
        if isinstance(name, str) and name in _FS_TOOL_NAMES and isinstance(args, dict):
            calls.append({"name": name, "arguments": args})
    elif isinstance(payload, list):
        for item in payload:
            calls.extend(_normalize_tool_calls(item))
    return calls


def _extract_tool_calls_from_text(text: str) -> list[dict]:
    if not text:
        return []

    calls: list[dict] = []
    seen: set[str] = set()

    def add_calls(items: list[dict]):
        for item in items:
            key = json.dumps(item, sort_keys=True, ensure_ascii=True)
            if key not in seen:
                seen.add(key)
                calls.append(item)

    # First pass: direct JSON body.
    try:
        add_calls(_normalize_tool_calls(json.loads(text.strip())))
    except Exception:  # noqa: BLE001
        pass

    # Second pass: fenced JSON snippets.
    for block in re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE):
        try:
            add_calls(_normalize_tool_calls(json.loads(block.strip())))
        except Exception:  # noqa: BLE001
            continue

    # Third pass: scan inline JSON objects/arrays embedded in prose.
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch not in "[{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
            add_calls(_normalize_tool_calls(obj))
        except Exception:  # noqa: BLE001
            continue

    return calls


def _execute_tool_calls(calls: list[dict], guarded_tools: list) -> AIMessage:
    lines: list[str] = []
    for call in calls:
        tool_name = call["name"]
        tool_args = call["arguments"]
        try:
            result = _invoke_tool(tool_name, tool_args, guarded_tools)
            lines.append(f"Executed {tool_name}: {_tool_result_to_text(result)}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{tool_name} failed: {exc}")
            return AIMessage(content="\n".join(lines))
    return AIMessage(content="\n".join(lines))


def _execution_outcome_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Executed ") or line.endswith(" failed") or " failed:" in line:
            lines.append(line)
    return lines


def _sanitize_non_executor_output(text: str, draft_text: str = "") -> str:
    cleaned = text

    # Remove fenced JSON blocks that tend to contain raw tool-call plans.
    cleaned = re.sub(r"```json[\s\S]*?```", "", cleaned, flags=re.IGNORECASE)

    kept: list[str] = []
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue

        # Drop lines that are explicit tool-call payloads.
        if '"name"' in line and ('"arguments"' in line or '"args"' in line):
            continue
        if line.startswith("[") and line.endswith("]") and ('"name"' in line or '{"name"' in line):
            continue
        if line.startswith("{") and line.endswith("}") and '"name"' in line:
            continue

        kept.append(raw)

    sanitized = "\n".join(kept).strip()

    outcomes = _execution_outcome_lines(draft_text)
    if outcomes:
        outcome_block = "\n".join(f"- {line}" for line in outcomes)
        summary = f"Execution outcomes:\n{outcome_block}"
        if sanitized:
            return f"{sanitized}\n\n{summary}"
        return summary

    if sanitized:
        return sanitized
    return "No additional review details. See coder execution outcomes above."


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


def _find_tool(tools: list, name: str):
    for t in tools:
        if getattr(t, "name", None) == name:
            return t
    return None


def _invoke_tool(tool_name: str, tool_args: dict, tools: list):
    tool_obj = _find_tool(tools, tool_name)
    if tool_obj is None:
        raise ValueError(f"Tool '{tool_name}' is not available")
    if hasattr(tool_obj, "ainvoke"):
        return _run_async(tool_obj.ainvoke(tool_args))
    return tool_obj.invoke(tool_args)


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

    def enforce_shell_allowed():
        if read_only:
            raise PermissionError("Profile tool mode is read_only; shell operations are blocked")

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

    @tool("get_shell_policy")
    def guarded_get_shell_policy():
        """Return shell policy metadata for this run."""
        enforce_shell_allowed()
        return _invoke_tool("get_shell_policy", {}, base_tools)

    @tool("run_shell")
    def guarded_run_shell(command: str, cwd: str = ".", timeout_seconds: int | None = None):
        """Run an allowlisted shell command in the selected filesystem root."""
        enforce_shell_allowed()
        safe_cwd = enforce_path(cwd)
        payload = {"command": command, "cwd": safe_cwd}
        if isinstance(timeout_seconds, int) and timeout_seconds > 0:
            payload["timeout_seconds"] = timeout_seconds
        return _invoke_tool("run_shell", payload, base_tools)

    guarded = [
        guarded_get_root,
        guarded_list_directory,
        guarded_read_text_file,
        guarded_write_text_file,
        guarded_append_text_file,
        guarded_make_directory,
        guarded_move_path,
        guarded_delete_path,
    ]

    if _find_tool(base_tools, "run_shell") is not None:
        guarded.append(guarded_get_shell_policy)
        guarded.append(guarded_run_shell)

    return guarded


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


def _invoke_swarm_agent(
    system_prompt: str,
    user_prompt: str,
    messages: list[AnyMessage],
    execute_tools: bool = False,
):
    policy = _extract_profile_context(messages)
    tool_agent, guarded_tools = _get_tool_agent_for_policy(policy)
    if tool_agent is not None:
        result = tool_agent.invoke(
            {
                "messages": [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ]
            }
        )
        msgs = result.get("messages", []) if isinstance(result, dict) else []
        for msg in reversed(msgs):
            if isinstance(msg, AIMessage) and msg.content:
                if execute_tools and isinstance(msg.content, str):
                    calls = _extract_tool_calls_from_text(msg.content)
                    if calls:
                        return _execute_tool_calls(calls, guarded_tools)
                return msg
        return AIMessage(content="")

    return _model.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )


def planner_node(state: SwarmState) -> SwarmState:
    task = _last_user_text(state["messages"]) or state.get("task", "")
    plan_msg = _invoke_swarm_agent(
        "You are a planning agent. Break the task into 3-6 concrete execution steps. Return concise numbered steps only.",
        f"Task:\n{task}\n\nRecent conversation context:\n{_chat_context(state['messages'])}",
        state["messages"],
        execute_tools=False,
    )
    return {
        "task": task,
        "plan": plan_msg.content,
        "messages": [AIMessage(content=f"[planner]\n{plan_msg.content}")],
    }


def coder_node(state: SwarmState) -> SwarmState:
    draft_msg = _invoke_swarm_agent(
        "You are a coding agent. Produce an implementation draft based on the task and plan. Prefer concise, practical output. Use tools when helpful.",
        (
            f"Task:\n{state.get('task', '')}\n\nPlan:\n{state.get('plan', '')}\n\n"
            f"Recent conversation context:\n{_chat_context(state['messages'])}"
        ),
        state["messages"],
        execute_tools=True,
    )
    return {
        "draft": draft_msg.content,
        "messages": [AIMessage(content=f"[coder]\n{draft_msg.content}")],
    }


def reviewer_node(state: SwarmState) -> SwarmState:
    review_msg = _invoke_swarm_agent(
        (
            "You are a reviewer agent. Critique executed results and provide concise improvements, risks, and edge cases. "
            "Do not output raw JSON tool-call payloads or tool-call plans."
        ),
        (
            f"Task:\n{state.get('task', '')}\n\nPlan:\n{state.get('plan', '')}\n\n"
            f"Draft:\n{state.get('draft', '')}\n\nRecent conversation context:\n{_chat_context(state['messages'])}"
        ),
        state["messages"],
        execute_tools=False,
    )
    sanitized_review = _sanitize_non_executor_output(
        str(review_msg.content),
        str(state.get("draft", "")),
    )
    return {
        "review": sanitized_review,
        "messages": [AIMessage(content=f"[reviewer]\n{sanitized_review}")],
    }


def synthesizer_node(state: SwarmState) -> SwarmState:
    final_msg = _invoke_swarm_agent(
        (
            "You are a synthesis agent. Produce a concise final status using task, draft execution, and review. "
            "Do not output raw JSON tool-call payloads, code fences, or tool-call plans."
        ),
        (
            f"Task:\n{state.get('task', '')}\n\nPlan:\n{state.get('plan', '')}\n\n"
            f"Draft:\n{state.get('draft', '')}\n\nReview:\n{state.get('review', '')}\n\n"
            f"Recent conversation context:\n{_chat_context(state['messages'])}"
        ),
        state["messages"],
        execute_tools=False,
    )
    sanitized_final = _sanitize_non_executor_output(
        str(final_msg.content),
        str(state.get("draft", "")),
    )
    return {
        "final": sanitized_final,
        "messages": [AIMessage(content=f"[final]\n{sanitized_final}")],
    }


builder = StateGraph(SwarmState)
builder.add_node("planner", planner_node)
builder.add_node("coder", coder_node)
builder.add_node("reviewer", reviewer_node)
builder.add_node("synthesizer", synthesizer_node)

builder.set_entry_point("planner")
builder.add_edge("planner", "coder")
builder.add_edge("coder", "reviewer")
builder.add_edge("reviewer", "synthesizer")
builder.add_edge("synthesizer", END)

graph = builder.compile(checkpointer=get_checkpointer())
