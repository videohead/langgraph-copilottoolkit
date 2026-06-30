import os
from typing import Annotated

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from src.checkpointing import get_checkpointer
from typing_extensions import TypedDict


class ReflectionState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    revision_count: int


_MAX_REVISIONS = int(os.environ.get("REFLECTION_MAX_REVISIONS", "2"))

_model = ChatOllama(
    base_url=os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"),
    model=os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"),
)


def _last_user_message(messages: list[AnyMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


def _last_ai_message(messages: list[AnyMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


def draft(state: ReflectionState) -> ReflectionState:
    response = _model.invoke(state["messages"])
    return {"messages": [response]}


def critique(state: ReflectionState) -> ReflectionState:
    user_text = _last_user_message(state["messages"])
    draft_text = _last_ai_message(state["messages"])
    prompt = [
        SystemMessage(
            content=(
                "You are a strict reviewer. Critique the assistant draft and list concrete "
                "improvements for accuracy, completeness, and clarity in 3-5 bullets."
            )
        ),
        HumanMessage(content=f"User request:\n{user_text}\n\nAssistant draft:\n{draft_text}"),
    ]
    review = _model.invoke(prompt)
    return {"messages": [review]}


def revise(state: ReflectionState) -> ReflectionState:
    user_text = _last_user_message(state["messages"])
    critique_text = _last_ai_message(state["messages"])
    prompt = [
        SystemMessage(
            content=(
                "Revise the answer based on critique. Return only the improved final answer, "
                "without mentioning internal critique process."
            )
        ),
        HumanMessage(
            content=(
                f"User request:\n{user_text}\n\n"
                f"Critique notes:\n{critique_text}\n\n"
                "Write the improved answer now."
            )
        ),
    ]
    revised = _model.invoke(prompt)
    current_revisions = int(state.get("revision_count", 0))
    return {
        "messages": [revised],
        "revision_count": current_revisions + 1,
    }


def should_continue(state: ReflectionState) -> str:
    if int(state.get("revision_count", 0)) < _MAX_REVISIONS:
        return "critique"
    return END


builder = StateGraph(ReflectionState)
builder.add_node("draft", draft)
builder.add_node("critique", critique)
builder.add_node("revise", revise)

builder.set_entry_point("draft")
builder.add_edge("draft", "critique")
builder.add_edge("critique", "revise")
builder.add_conditional_edges("revise", should_continue)

graph = builder.compile(checkpointer=get_checkpointer())
