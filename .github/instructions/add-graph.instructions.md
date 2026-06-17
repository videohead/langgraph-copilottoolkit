---
description: "Use when adding a new LangGraph graph, registering an agent, creating a new graph module, or wiring a new graph into the API and frontend. Covers file structure, state typing, Django registration, and CopilotKit runtime registration."
---

# Adding a New LangGraph Graph

## 1. Create the graph module

Add a directory under `src/`:

```
src/
  my_graph/
    __init__.py   (empty)
    graph.py
```

`graph.py` must export a compiled graph named `graph`:

```python
import os
from typing import Annotated
from langchain_core.messages import AnyMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

class MyState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

_model = ChatOllama(
    base_url=os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"),
    model=os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"),
)

def my_node(state: MyState) -> MyState:
    response = _model.invoke(state["messages"])
    return {"messages": [response]}

builder = StateGraph(MyState)
builder.add_node("my_node", my_node)
builder.set_entry_point("my_node")
builder.add_edge("my_node", END)

graph = builder.compile()
```

Always read `OLLAMA_BASE_URL` and `OLLAMA_MODEL` from environment — never hard-code.

## 2. Register in `langgraph.json`

```json
{
  "dependencies": ["."],
  "graphs": {
    "basic":    "./src/basic_graph/graph.py:graph",
    "swarm_v1": "./src/swarm_graph/graph.py:graph",
    "my_graph": "./src/my_graph/graph.py:graph"
  },
  "env": ".env"
}
```

The key is the graph ID used in all downstream registrations.

## 3. Register in Django (`django/agents/views.py`)

Add two entries — the import and the dict entry:

```python
from src.my_graph.graph import graph as _my_graph   # add import

GRAPHS = {
    "basic":    _basic_graph,
    "swarm_v1": _swarm_graph,
    "my_graph": _my_graph,                          # add entry
}

GRAPH_DESCRIPTIONS = {
    "basic":    "Single-turn chat agent powered by Ollama.",
    "swarm_v1": "Multi-agent swarm: planner → coder → reviewer → writer.",
    "my_graph": "Description shown in the UI graph selector.",  # add entry
}
```

No other Django files need changes — the URL pattern `api/agents/<str:graph_name>/` is already dynamic.

## 4. Register in the CopilotKit Runtime (`frontend/app/api/copilotkit/[...path]/route.ts`)

```typescript
import { HttpAgent } from "@ag-ui/client";

const runtime = new CopilotRuntime({
  agents: {
    basic:    new HttpAgent({ url: `${djangoUrl}/api/agents/basic/` }),
    swarm_v1: new HttpAgent({ url: `${djangoUrl}/api/agents/swarm_v1/` }),
    my_graph: new HttpAgent({ url: `${djangoUrl}/api/agents/my_graph/` }),  // add
  },
});
```

## 5. Add to the frontend selector (`frontend/app/page.tsx`)

```typescript
const GRAPHS = [
  { id: "basic",    label: "Basic Chat",            description: "..." },
  { id: "swarm_v1", label: "Swarm ...",              description: "..." },
  { id: "my_graph", label: "My Graph Display Name", description: "..." },  // add
];
```

## Checklist

- [ ] `src/my_graph/__init__.py` exists (empty)
- [ ] `src/my_graph/graph.py` exports `graph = builder.compile()`
- [ ] `OLLAMA_BASE_URL` / `OLLAMA_MODEL` read from env
- [ ] `langgraph.json` updated
- [ ] `GRAPHS` + `GRAPH_DESCRIPTIONS` dicts updated in `django/agents/views.py`
- [ ] `CopilotRuntime` agents updated in `frontend/app/api/copilotkit/[...path]/route.ts`
- [ ] `GRAPHS` array updated in `frontend/app/page.tsx`

## Streaming behaviour notes

The Django AG-UI view uses `stream_mode="messages"` and yields `AIMessageChunk` tokens per-node. If your graph has multiple named nodes, each node transition starts a new `TEXT_MESSAGE_START` / `TEXT_MESSAGE_END` pair — the node name appears as a label in the chat UI. Nodes named `"chat"` or `"agent"` are unlabelled (treated as default).
