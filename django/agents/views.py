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
import importlib.util
import os
import logging
from pathlib import Path

from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

# In containers, Django app root is /app and src is mounted at /app/src.
_app_root = Path(__file__).resolve().parent.parent
if str(_app_root) not in sys.path:
    sys.path.insert(0, str(_app_root))

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage  # noqa: E402

_LANGGRAPH_CANDIDATE_FILES = [
    os.environ.get("LANGGRAPH_REGISTRY_FILE", "").strip(),
    "/workspace/langgraph.json",
    "/config/langgraph.json",
    "/user/www/langgraph-copilottoolkit/langgraph.json",
    "/app/langgraph.json",
]
_graph_descriptions_file = Path(__file__).resolve().parent.parent / "graph_descriptions.json"

_project_profiles_file = Path(__file__).resolve().parent.parent / "project_profiles.json"

_PROJECT_PROFILES_FALLBACK = [
    {
        "id": "workspace",
        "name": "Workspace Sandbox",
        "description": "General purpose profile for files under MCP sandbox root.",
        "filesystem_roots": ["/workspace-data"],
        "mcp_root": "/workspace-data",
        "default_graph": "basic",
        "allowed_graphs": ["basic", "swarm_v1"],
        "tool_mode": "read_write",
    }
]

_GRAPH_DESCRIPTION_FALLBACKS = {
    "basic": "ReAct chat agent powered by Ollama with MCP filesystem tools.",
    "swarm_v1": "Multi-agent swarm: planner → coder → reviewer → writer.",
}

_GRAPH_CACHE = {
    "mtime": None,
    "file": None,
    "graphs": {},
}

_LOG = logging.getLogger(__name__)

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


def _load_graph_descriptions() -> dict[str, str]:
    try:
        if _graph_descriptions_file.exists():
            data = json.loads(_graph_descriptions_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    str(k): str(v)
                    for k, v in data.items()
                    if isinstance(k, str) and isinstance(v, str)
                }
    except Exception:  # noqa: BLE001
        pass
    return _GRAPH_DESCRIPTION_FALLBACKS


def _load_graph_object(graph_id: str, graph_ref: str):
    try:
        path_part, symbol = graph_ref.split(":", 1)
    except ValueError:
        return None

    module_path = (_app_root / path_part).resolve()
    if not module_path.exists():
        return None

    module_name = f"dynamic_graph_{graph_id}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, symbol, None)


def _resolve_langgraph_file() -> Path | None:
    for candidate in _LANGGRAPH_CANDIDATE_FILES:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _load_graph_registry() -> dict[str, object]:
    langgraph_file = _resolve_langgraph_file()
    if langgraph_file is None:
        return _GRAPH_CACHE["graphs"]

    try:
        mtime = langgraph_file.stat().st_mtime
    except FileNotFoundError:
        return _GRAPH_CACHE["graphs"]

    if _GRAPH_CACHE["graphs"] and _GRAPH_CACHE["mtime"] == mtime and _GRAPH_CACHE["file"] == str(langgraph_file):
        return _GRAPH_CACHE["graphs"]

    loaded_graphs: dict[str, object] = {}
    try:
        payload = json.loads(langgraph_file.read_text(encoding="utf-8"))
        graph_map = payload.get("graphs", {})
        if isinstance(graph_map, dict):
            for graph_id, graph_ref in graph_map.items():
                if not isinstance(graph_id, str) or not isinstance(graph_ref, str):
                    continue
                try:
                    graph_obj = _load_graph_object(graph_id, graph_ref)
                    if graph_obj is not None:
                        loaded_graphs[graph_id] = graph_obj
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        loaded_graphs = _GRAPH_CACHE["graphs"]

    _GRAPH_CACHE["mtime"] = mtime
    _GRAPH_CACHE["file"] = str(langgraph_file)
    _GRAPH_CACHE["graphs"] = loaded_graphs
    return loaded_graphs


def _normalize_profile(profile: dict, available_graphs: set[str]) -> dict:
    roots = profile.get("filesystem_roots")
    mcp_root = profile.get("mcp_root")
    if not isinstance(roots, list):
        roots = [mcp_root] if isinstance(mcp_root, str) and mcp_root.strip() else []
    roots = [str(r).strip() for r in roots if isinstance(r, str) and str(r).strip()]
    if not roots:
        roots = ["/workspace-data"]

    allowed_graphs = profile.get("allowed_graphs")
    if not isinstance(allowed_graphs, list):
        allowed_graphs = list(available_graphs)
    allowed_graphs = [
        str(g).strip()
        for g in allowed_graphs
        if isinstance(g, str) and str(g).strip() in available_graphs
    ]
    if not allowed_graphs:
        allowed_graphs = list(available_graphs)

    default_graph = profile.get("default_graph")
    if not isinstance(default_graph, str) or default_graph not in allowed_graphs:
        default_graph = allowed_graphs[0] if allowed_graphs else ""

    normalized = {
        "id": str(profile.get("id", "")).strip(),
        "name": str(profile.get("name", "")).strip() or "Unnamed Profile",
        "description": str(profile.get("description", "")).strip(),
        "filesystem_roots": roots,
        "mcp_root": roots[0],
        "default_graph": default_graph,
        "allowed_graphs": allowed_graphs,
        "tool_mode": str(profile.get("tool_mode", "read_only")).strip() or "read_only",
    }
    return normalized


def _load_project_profiles() -> list[dict]:
    graph_ids = set(_load_graph_registry().keys())

    try:
        if _project_profiles_file.exists():
            data = json.loads(_project_profiles_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                normalized = [
                    _normalize_profile(p, graph_ids)
                    for p in data
                    if isinstance(p, dict)
                ]
                return [p for p in normalized if p.get("id")]
    except Exception:  # noqa: BLE001
        pass
    normalized = [_normalize_profile(p, graph_ids) for p in _PROJECT_PROFILES_FALLBACK]
    return [p for p in normalized if p.get("id")]


def _graph_has_mcp_tools(graph_obj: object) -> bool | None:
    module_name = getattr(graph_obj, "__module__", None)
    if not module_name:
        return None

    module = sys.modules.get(module_name)
    if module is None:
        return None

    for attr in ("_TOOL_AGENT", "_tool_agent"):
        if hasattr(module, attr):
            return getattr(module, attr) is not None

    if hasattr(module, "_mcp_tools"):
        tools = getattr(module, "_mcp_tools")
        if isinstance(tools, list):
            return len(tools) > 0

    return None


def _mcp_attachment_snapshot(graphs: dict[str, object]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for graph_id, graph_obj in graphs.items():
        status = _graph_has_mcp_tools(graph_obj)
        if status is True:
            snapshot[graph_id] = "attached"
        elif status is False:
            snapshot[graph_id] = "detached"
        else:
            snapshot[graph_id] = "unknown"
    return snapshot


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                # Only forward visible text parts; suppress tool call metadata payloads.
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif isinstance(item, str):
                if item:
                    parts.append(item)
        return "".join(parts)
    if isinstance(content, dict):
        # Guard against leaking tool-call objects as assistant text.
        if "name" in content and ("arguments" in content or "args" in content):
            return ""
        text = content.get("text")
        return text if isinstance(text, str) else ""
    return str(content)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@require_GET
def health(request):
    graphs = _load_graph_registry()
    return JsonResponse({"status": "ok", "graphs": list(graphs.keys())})


@require_GET
def list_graphs(request):
    graphs = _load_graph_registry()
    descriptions = _load_graph_descriptions()
    return JsonResponse(
        {
            "graphs": [
                {"id": gid, "description": descriptions.get(gid, f"LangGraph agent {gid}.")}
                for gid in graphs
            ]
        }
    )


@require_GET
def list_projects(request):
    return JsonResponse({"projects": _load_project_profiles()})


@csrf_exempt
@require_POST
def run_agent(request, graph_name: str):
    graphs = _load_graph_registry()

    if graph_name not in graphs:
        return JsonResponse(
            {"error": f"Unknown graph '{graph_name}'. Available: {list(graphs.keys())}"},
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

    graph = graphs[graph_name]
    _LOG.info(
        "run_agent start graph=%s mcp=%s",
        graph_name,
        json.dumps(_mcp_attachment_snapshot(graphs), sort_keys=True),
    )

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
                if not isinstance(chunk, (AIMessageChunk, AIMessage)):
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

                content = _content_to_text(chunk.content)
                if not content:
                    continue

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
