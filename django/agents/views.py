"""
AG-UI compatible streaming endpoint for LangGraph agents.

The AG-UI protocol (https://ag-ui.com) is what CopilotKit uses to stream
events from an agent to the frontend. This view:
  1. Accepts a POST with a RunAgentInput JSON body (messages + threadId)
  2. Runs the appropriate LangGraph graph
  3. Streams AG-UI Server-Sent Events back to the caller
"""
import json
import sys
import uuid
from pathlib import Path

from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

# Make the repo-root src/ importable regardless of working directory
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage  # noqa: E402
from src.basic_graph.graph import graph as _basic_graph  # noqa: E402
from src.swarm_graph.graph import graph as _swarm_graph  # noqa: E402

GRAPHS = {
    "basic": _basic_graph,
    "swarm_v1": _swarm_graph,
}

GRAPH_DESCRIPTIONS = {
    "basic": "Single-turn chat agent powered by Ollama.",
    "swarm_v1": "Multi-agent swarm: planner → coder → reviewer → writer.",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(event: dict) -> str:
    """Format a dict as a Server-Sent Event data line."""
    return f"data: {json.dumps(event)}\n\n"


def _parse_messages(raw_messages: list[dict]) -> list:
    """Convert AG-UI message objects to LangChain message objects."""
    result = []
    for msg in raw_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if not isinstance(content, str):
            # ContentPart array — join text parts
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        if role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
        elif role == "system":
            result.append(SystemMessage(content=content))
    return result


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@require_GET
def health(request):
    return JsonResponse({"status": "ok", "graphs": list(GRAPHS.keys())})


@require_GET
def list_graphs(request):
    return JsonResponse(
        {
            "graphs": [
                {"id": gid, "description": GRAPH_DESCRIPTIONS.get(gid, "")}
                for gid in GRAPHS
            ]
        }
    )


@csrf_exempt
@require_POST
def run_agent(request, graph_name: str):
    if graph_name not in GRAPHS:
        return JsonResponse(
            {"error": f"Unknown graph '{graph_name}'. Available: {list(GRAPHS.keys())}"},
            status=404,
        )

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    thread_id = body.get("threadId") or str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    raw_messages = body.get("messages", [])
    lc_messages = _parse_messages(raw_messages)

    if not lc_messages:
        return JsonResponse({"error": "No messages provided"}, status=400)

    graph = GRAPHS[graph_name]

    def stream_events():
        yield _sse({"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id})

        current_msg_id: str | None = None
        current_node: str | None = None

        try:
            # stream_mode="messages" yields (AIMessageChunk, metadata) tuples,
            # giving token-level streaming for each node in the graph.
            for chunk, metadata in graph.stream(
                {"messages": lc_messages},
                stream_mode="messages",
            ):
                if not isinstance(chunk, AIMessageChunk):
                    continue
                if not chunk.content:
                    continue

                node = metadata.get("langgraph_node", "agent")

                # Start a new message whenever the node changes
                if node != current_node:
                    if current_msg_id is not None:
                        yield _sse({"type": "TEXT_MESSAGE_END", "messageId": current_msg_id})
                    current_node = node
                    current_msg_id = str(uuid.uuid4())
                    # Use node name as a visible label in the UI
                    label = f"[{node}] " if node not in ("chat", "agent") else ""
                    yield _sse(
                        {
                            "type": "TEXT_MESSAGE_START",
                            "messageId": current_msg_id,
                            "role": "assistant",
                            "metadata": {"node": node, "label": label},
                        }
                    )

                content = chunk.content
                if isinstance(content, list):
                    content = "".join(
                        c.get("text", "") if isinstance(c, dict) else str(c)
                        for c in content
                    )

                yield _sse(
                    {
                        "type": "TEXT_MESSAGE_CONTENT",
                        "messageId": current_msg_id,
                        "delta": content,
                    }
                )

            if current_msg_id is not None:
                yield _sse({"type": "TEXT_MESSAGE_END", "messageId": current_msg_id})

        except Exception as exc:  # noqa: BLE001
            yield _sse(
                {
                    "type": "RUN_ERROR",
                    "threadId": thread_id,
                    "runId": run_id,
                    "message": str(exc),
                }
            )
            return

        yield _sse({"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id})

    response = StreamingHttpResponse(
        stream_events(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    response["Access-Control-Allow-Origin"] = "*"
    return response
