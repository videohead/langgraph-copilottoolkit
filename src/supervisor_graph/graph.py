import asyncio
import json
import os
import re
import time
from functools import partial
from typing import Annotated, Literal

from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from src.checkpointing import get_checkpointer
from pydantic import BaseModel
from typing_extensions import TypedDict

try:
    from langchain_mcp_adapters.client import MultiServerMCPClient
except Exception:  # noqa: BLE001
    MultiServerMCPClient = None


class SupervisorState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    next: str
    rounds: int


MEMBERS = ["Researcher", "Coder"]
OPTIONS = ["FINISH", *MEMBERS]
MAX_ROUNDS = int(os.environ.get("SUPERVISOR_MAX_ROUNDS", "8"))

_model = ChatOllama(
    base_url=os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"),
    model=os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"),
    temperature=0,
)

_MCP_TOOLS = []
_LAST_MCP_ATTEMPT = 0.0
_MCP_RETRY_SECONDS = float(os.environ.get("MCP_TOOL_RETRY_SECONDS", "10"))


class RouteResponse(BaseModel):
    next: Literal["FINISH", "Researcher", "Coder"]


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
    except Exception:  # noqa: BLE001
        return []


def _get_mcp_tools() -> list:
    global _MCP_TOOLS, _LAST_MCP_ATTEMPT

    if _MCP_TOOLS:
        return _MCP_TOOLS

    now = time.monotonic()
    if now - _LAST_MCP_ATTEMPT < _MCP_RETRY_SECONDS:
        return []

    _LAST_MCP_ATTEMPT = now
    _MCP_TOOLS = _run_async(_load_mcp_tools())
    return _MCP_TOOLS


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif isinstance(item, str) and item:
                parts.append(item)
        return "".join(parts)
    if isinstance(content, dict):
        text = content.get("text")
        return text if isinstance(text, str) else ""
    return str(content)


def _route_from_text(text: str) -> str:
    if not text:
        return "Researcher"

    cleaned = text.strip()
    try:
        payload = json.loads(cleaned)
        candidate = payload.get("next") if isinstance(payload, dict) else None
        if isinstance(candidate, str) and candidate in OPTIONS:
            return candidate
    except json.JSONDecodeError:
        pass

    upper = cleaned.upper()
    if "FINISH" in upper:
        return "FINISH"
    if re.search(r"\bRESEARCHER\b", upper):
        return "Researcher"
    if re.search(r"\bCODER\b", upper):
        return "Coder"
    return "Researcher"


def agent_node(state: SupervisorState, agent, name: str) -> SupervisorState:
    result = agent.invoke({"messages": state["messages"]})
    messages = result.get("messages", []) if isinstance(result, dict) else []
    if not messages:
        return {"messages": [HumanMessage(content="No result returned.", name=name)]}
    last_message = messages[-1]
    return {"messages": [HumanMessage(content=_message_text(last_message.content), name=name)]}


_supervisor_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "You are a supervisor managing a conversation between worker agents: {members}. "
                "Choose exactly one next worker or FINISH. "
                "Prefer Researcher for information gathering/planning and Coder for implementation tasks."
            ),
        ),
        MessagesPlaceholder(variable_name="messages"),
        (
            "system",
            "Return JSON only in the form {{\"next\":\"Researcher|Coder|FINISH\"}}.",
        ),
    ]
).partial(members=", ".join(MEMBERS))


def supervisor_agent(state: SupervisorState) -> SupervisorState:
    rounds = int(state.get("rounds", 0))
    if rounds >= MAX_ROUNDS:
        return {"next": "FINISH", "rounds": rounds}

    structured_chain = _supervisor_prompt | _model.with_structured_output(RouteResponse)
    try:
        decision = structured_chain.invoke(state)
        next_node = decision.next
    except Exception:  # noqa: BLE001
        fallback_chain = _supervisor_prompt | _model
        fallback = fallback_chain.invoke(state)
        next_node = _route_from_text(_message_text(fallback.content))

    if next_node not in OPTIONS:
        next_node = "Researcher"

    return {"next": next_node, "rounds": rounds + 1}


def get_next(state: SupervisorState) -> str:
    return state.get("next", "FINISH")


_researcher_agent = create_react_agent(
    _model,
    tools=[],
    prompt=(
        "You are Researcher. Gather context, clarify assumptions, and produce concise findings "
        "for the team. Do not claim file edits were performed."
    ),
)

_coder_agent = create_react_agent(
    _model,
    tools=_get_mcp_tools(),
    prompt=(
        "You are Coder. Implement requested code/file changes carefully. "
        "If filesystem tools are available, use them for edits; otherwise provide exact patch-ready instructions."
    ),
)

research_node = partial(agent_node, agent=_researcher_agent, name="Researcher")
coder_node = partial(agent_node, agent=_coder_agent, name="Coder")

workflow = StateGraph(SupervisorState)
workflow.add_node("Supervisor", supervisor_agent)
workflow.add_node("Researcher", research_node)
workflow.add_node("Coder", coder_node)

workflow.add_edge(START, "Supervisor")
workflow.add_edge("Researcher", "Supervisor")
workflow.add_edge("Coder", "Supervisor")
workflow.add_conditional_edges(
    "Supervisor",
    get_next,
    {
        "Researcher": "Researcher",
        "Coder": "Coder",
        "FINISH": END,
    },
)

graph = workflow.compile(checkpointer=get_checkpointer())
