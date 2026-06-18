"use client";

import { useState } from "react";
import { CopilotChatConfigurationProvider } from "@copilotkit/react-core/v2/headless";
import { CopilotSidebar } from "@copilotkit/react-ui";

const GRAPHS = [
  {
    id: "basic",
    label: "Basic Chat",
    description: "Single-turn chat agent powered by Ollama.",
  },
  {
    id: "swarm_v1",
    label: "Swarm (Planner → Coder → Reviewer)",
    description: "Multi-agent pipeline: planner, coder, reviewer, and writer nodes.",
  },
];

export default function Home() {
  const [selectedGraph, setSelectedGraph] = useState(GRAPHS[0].id);

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">LangGraph AI</h1>
          <p className="text-sm text-gray-400">CopilotKit · Django · Ollama</p>
        </div>

        {/* Graph selector */}
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-400">Agent:</span>
          <select
            value={selectedGraph}
            onChange={(e) => setSelectedGraph(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {GRAPHS.map((g) => (
              <option key={g.id} value={g.id}>
                {g.label}
              </option>
            ))}
          </select>
        </div>
      </header>

      {/* Description strip */}
      <div className="px-6 py-3 bg-gray-900 border-b border-gray-800 text-sm text-gray-400">
        {GRAPHS.find((g) => g.id === selectedGraph)?.description}
      </div>

      {/* Content */}
      <div className="flex flex-col items-center justify-center px-6 py-20 text-center">
        <p className="text-gray-500 max-w-sm">
          Open the chat sidebar (bottom-right) to start a conversation with the{" "}
          <strong className="text-gray-300">
            {GRAPHS.find((g) => g.id === selectedGraph)?.label}
          </strong>{" "}
          agent.
        </p>
      </div>

      {/*
        CopilotSidebar renders a floating sidebar with the chat interface.
        agentId selects which of the registered CopilotRuntime agents to use.
      */}
      <CopilotChatConfigurationProvider agentId={selectedGraph}>
        <CopilotSidebar
          defaultOpen={true}
          labels={{
            title: GRAPHS.find((g) => g.id === selectedGraph)?.label ?? "AI Chat",
            initial: `Hi! I'm the **${GRAPHS.find((g) => g.id === selectedGraph)?.label}** agent. How can I help?`,
          }}
        />
      </CopilotChatConfigurationProvider>
    </main>
  );
}
