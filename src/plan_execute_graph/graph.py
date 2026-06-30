import os
import re
from typing import Annotated

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from src.checkpointing import get_checkpointer
from typing_extensions import TypedDict


class PlanExecuteState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    plan_steps: list[str]
    step_index: int
    step_outputs: list[str]


_MAX_PLAN_STEPS = int(os.environ.get("PLAN_EXECUTE_MAX_STEPS", "3"))

_model = ChatOllama(
    base_url=os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"),
    model=os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"),
)


def _last_user_message(messages: list[AnyMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


def _parse_plan_steps(plan_text: str) -> list[str]:
    steps: list[str] = []
    for raw_line in plan_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cleaned = re.sub(r"^[-*]\s+", "", line)
        cleaned = re.sub(r"^\d+[.)]\s+", "", cleaned)
        cleaned = cleaned.strip()
        if cleaned:
            steps.append(cleaned)
    return steps[:_MAX_PLAN_STEPS]


def planner(state: PlanExecuteState) -> PlanExecuteState:
    request_text = _last_user_message(state["messages"])
    prompt = [
        SystemMessage(
            content=(
                "Create a short execution plan with at most 3 steps. "
                "Return only a markdown bullet list."
            )
        ),
        HumanMessage(content=request_text),
    ]
    plan_msg = _model.invoke(prompt)
    plan_text = plan_msg.content if isinstance(plan_msg.content, str) else str(plan_msg.content)
    steps = _parse_plan_steps(plan_text)
    if not steps:
        steps = [request_text]
    return {
        "messages": [plan_msg],
        "plan_steps": steps,
        "step_index": 0,
        "step_outputs": [],
    }


def execute_step(state: PlanExecuteState) -> PlanExecuteState:
    steps = state.get("plan_steps", [])
    index = int(state.get("step_index", 0))
    outputs = list(state.get("step_outputs", []))

    if index >= len(steps):
        return {"step_index": index}

    step = steps[index]
    context = "\n".join(f"Step {i + 1} result: {text}" for i, text in enumerate(outputs))
    prompt = [
        SystemMessage(
            content=(
                "You are executing one plan step. Produce the result for this step only, "
                "clearly and concisely."
            )
        ),
        HumanMessage(
            content=(
                f"Current step ({index + 1}/{len(steps)}): {step}\n\n"
                f"Prior step outputs:\n{context or 'None'}"
            )
        ),
    ]
    result_msg = _model.invoke(prompt)
    result_text = result_msg.content if isinstance(result_msg.content, str) else str(result_msg.content)
    outputs.append(result_text)

    return {
        "messages": [result_msg],
        "step_index": index + 1,
        "step_outputs": outputs,
    }


def route_after_execute(state: PlanExecuteState) -> str:
    steps = state.get("plan_steps", [])
    if int(state.get("step_index", 0)) < len(steps):
        return "execute_step"
    return "synthesize"


def synthesize(state: PlanExecuteState) -> PlanExecuteState:
    request_text = _last_user_message(state["messages"])
    steps = state.get("plan_steps", [])
    outputs = state.get("step_outputs", [])

    steps_text = "\n".join(f"- {step}" for step in steps)
    outputs_text = "\n".join(f"- {item}" for item in outputs)

    prompt = [
        SystemMessage(
            content=(
                "Synthesize the execution results into a final answer for the user. "
                "Be direct and include the most important conclusions."
            )
        ),
        HumanMessage(
            content=(
                f"Original request:\n{request_text}\n\n"
                f"Plan:\n{steps_text or '- None'}\n\n"
                f"Step outputs:\n{outputs_text or '- None'}"
            )
        ),
    ]
    final_msg = _model.invoke(prompt)
    return {"messages": [final_msg]}


builder = StateGraph(PlanExecuteState)
builder.add_node("planner", planner)
builder.add_node("execute_step", execute_step)
builder.add_node("synthesize", synthesize)

builder.set_entry_point("planner")
builder.add_edge("planner", "execute_step")
builder.add_conditional_edges("execute_step", route_after_execute)
builder.add_edge("synthesize", END)

graph = builder.compile(checkpointer=get_checkpointer())
