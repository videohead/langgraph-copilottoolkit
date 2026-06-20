import os
import asyncio
import logging
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
        _LOG.warning("Unable to load swarm MCP filesystem tools from %s: %s", mcp_url, exc)
        return []


_mcp_tools = _run_async(_load_mcp_tools())
_tool_agent = create_react_agent(_model, _mcp_tools) if _mcp_tools else None


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


def _invoke_swarm_agent(system_prompt: str, user_prompt: str):
    if _tool_agent is not None:
        result = _tool_agent.invoke(
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
    )
    return {
        "draft": draft_msg.content,
        "messages": [AIMessage(content=f"[coder]\n{draft_msg.content}")],
    }


def reviewer_node(state: SwarmState) -> SwarmState:
    review_msg = _invoke_swarm_agent(
        "You are a reviewer agent. Critique the draft and provide specific improvements, risks, and missing edge cases. Use tools to inspect files when needed.",
        (
            f"Task:\n{state.get('task', '')}\n\nPlan:\n{state.get('plan', '')}\n\n"
            f"Draft:\n{state.get('draft', '')}\n\nRecent conversation context:\n{_chat_context(state['messages'])}"
        ),
    )
    return {
        "review": review_msg.content,
        "messages": [AIMessage(content=f"[reviewer]\n{review_msg.content}")],
    }


def synthesizer_node(state: SwarmState) -> SwarmState:
    final_msg = _invoke_swarm_agent(
        "You are a synthesis agent. Produce a final polished answer using the task, plan, draft, and review. Integrate reviewer fixes.",
        (
            f"Task:\n{state.get('task', '')}\n\nPlan:\n{state.get('plan', '')}\n\n"
            f"Draft:\n{state.get('draft', '')}\n\nReview:\n{state.get('review', '')}\n\n"
            f"Recent conversation context:\n{_chat_context(state['messages'])}"
        ),
    )
    return {
        "final": final_msg.content,
        "messages": [AIMessage(content=f"[final]\n{final_msg.content}")],
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

graph = builder.compile()
