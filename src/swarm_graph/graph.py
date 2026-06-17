import os
from typing import Annotated

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class SwarmState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    task: str
    plan: str
    draft: str
    review: str
    final: str


_model = ChatOllama(
    base_url=os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"),
    model=os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"),
)


def _last_user_text(messages: list[AnyMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content
    return ""


def planner_node(state: SwarmState) -> SwarmState:
    task = _last_user_text(state["messages"]) or state.get("task", "")
    plan_msg = _model.invoke(
        [
            SystemMessage(
                content=(
                    "You are a planning agent. Break the task into 3-6 concrete execution steps. "
                    "Return concise numbered steps only."
                )
            ),
            HumanMessage(content=task),
        ]
    )
    return {
        "task": task,
        "plan": plan_msg.content,
        "messages": [AIMessage(content=f"[planner]\n{plan_msg.content}")],
    }


def coder_node(state: SwarmState) -> SwarmState:
    draft_msg = _model.invoke(
        [
            SystemMessage(
                content=(
                    "You are a coding agent. Produce an implementation draft based on the task and plan. "
                    "Prefer concise, practical output."
                )
            ),
            HumanMessage(content=f"Task:\n{state.get('task', '')}\n\nPlan:\n{state.get('plan', '')}"),
        ]
    )
    return {
        "draft": draft_msg.content,
        "messages": [AIMessage(content=f"[coder]\n{draft_msg.content}")],
    }


def reviewer_node(state: SwarmState) -> SwarmState:
    review_msg = _model.invoke(
        [
            SystemMessage(
                content=(
                    "You are a reviewer agent. Critique the draft and provide specific improvements, "
                    "risks, and missing edge cases."
                )
            ),
            HumanMessage(
                content=(
                    f"Task:\n{state.get('task', '')}\n\nPlan:\n{state.get('plan', '')}\n\n"
                    f"Draft:\n{state.get('draft', '')}"
                )
            ),
        ]
    )
    return {
        "review": review_msg.content,
        "messages": [AIMessage(content=f"[reviewer]\n{review_msg.content}")],
    }


def synthesizer_node(state: SwarmState) -> SwarmState:
    final_msg = _model.invoke(
        [
            SystemMessage(
                content=(
                    "You are a synthesis agent. Produce a final polished answer using the task, plan, "
                    "draft, and review. Integrate reviewer fixes."
                )
            ),
            HumanMessage(
                content=(
                    f"Task:\n{state.get('task', '')}\n\nPlan:\n{state.get('plan', '')}\n\n"
                    f"Draft:\n{state.get('draft', '')}\n\nReview:\n{state.get('review', '')}"
                )
            ),
        ]
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
