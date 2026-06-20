"use client";

import { useEffect, useMemo, useState } from "react";
import { CopilotChatConfigurationProvider } from "@copilotkit/react-core/v2/headless";
import { CopilotSidebar } from "@copilotkit/react-ui";

type GraphOption = {
  id: string;
  label: string;
  description: string;
};

type ProjectProfile = {
  id: string;
  name: string;
  description?: string;
  mcp_root?: string;
  default_graph?: string;
  allowed_graphs?: string[];
  tool_mode?: string;
};

const FALLBACK_GRAPHS: GraphOption[] = [
  {
    id: "basic",
    label: "Basic",
    description: "ReAct chat agent powered by Ollama with MCP filesystem tools.",
  },
  {
    id: "swarm_v1",
    label: "Swarm V1",
    description: "Multi-agent pipeline: planner, coder, reviewer, and synthesizer.",
  },
];

const FALLBACK_PROFILES: ProjectProfile[] = [
  {
    id: "workspace",
    name: "Workspace Sandbox",
    description: "General-purpose profile for the MCP filesystem sandbox.",
    mcp_root: "/workspace-data",
    default_graph: "basic",
    allowed_graphs: ["basic", "swarm_v1"],
    tool_mode: "read_write",
  },
];

export default function Home() {
  const [graphs, setGraphs] = useState<GraphOption[]>(FALLBACK_GRAPHS);
  const [profiles, setProfiles] = useState<ProjectProfile[]>(FALLBACK_PROFILES);
  const [selectedProfile, setSelectedProfile] = useState(FALLBACK_PROFILES[0].id);
  const [selectedGraph, setSelectedGraph] = useState(FALLBACK_GRAPHS[0].id);

  useEffect(() => {
    let cancelled = false;

    async function loadRuntimeConfig() {
      try {
        const [graphsRes, projectsRes] = await Promise.all([
          fetch("/api/graphs", { cache: "no-store" }),
          fetch("/api/projects", { cache: "no-store" }),
        ]);

        if (!graphsRes.ok || !projectsRes.ok) {
          return;
        }

        const [graphsPayload, projectsPayload] = await Promise.all([
          graphsRes.json(),
          projectsRes.json(),
        ]);

        const runtimeInfoRes = await fetch("/api/copilotkit/info", {
          cache: "no-store",
        });
        const runtimeInfo = runtimeInfoRes.ok ? await runtimeInfoRes.json() : null;
        const runtimeAgents = new Set(
          Object.keys(runtimeInfo?.agents ?? {}).filter((id) => id !== "default"),
        );

        if (cancelled) {
          return;
        }

        const loadedGraphsRaw: GraphOption[] = Array.isArray(graphsPayload?.graphs)
          ? graphsPayload.graphs
              .filter((g: unknown) => typeof g === "object" && g !== null)
              .map((g: Record<string, unknown>) => {
                const id = String(g.id ?? "").trim();
                const description = String(g.description ?? "").trim();
                return {
                  id,
                  label: id.replace(/_/g, " "),
                  description: description || `Graph agent ${id}`,
                };
              })
              .filter((g: GraphOption) => g.id.length > 0)
          : [];

        const loadedGraphs =
          runtimeAgents.size > 0
            ? loadedGraphsRaw.filter((g) => runtimeAgents.has(g.id))
            : loadedGraphsRaw;

        const loadedProfiles: ProjectProfile[] = Array.isArray(projectsPayload?.projects)
          ? projectsPayload.projects.filter(
              (p: unknown): p is ProjectProfile =>
                typeof p === "object" && p !== null && typeof (p as ProjectProfile).id === "string",
            )
          : [];

        if (loadedGraphs.length > 0) {
          setGraphs(loadedGraphs);
          setSelectedGraph((current) => {
            if (loadedGraphs.some((g) => g.id === current)) {
              return current;
            }
            return loadedGraphs[0].id;
          });
        }

        if (loadedProfiles.length > 0) {
          setProfiles(loadedProfiles);
          setSelectedProfile((current) => {
            if (loadedProfiles.some((p) => p.id === current)) {
              return current;
            }
            return loadedProfiles[0].id;
          });
        }
      } catch {
        // Keep fallback config when runtime config fetch fails.
      }
    }

    loadRuntimeConfig();
    return () => {
      cancelled = true;
    };
  }, []);

  const activeProfile = useMemo(
    () => profiles.find((p) => p.id === selectedProfile) ?? profiles[0],
    [profiles, selectedProfile],
  );

  const visibleGraphs = useMemo(() => {
    if (!activeProfile?.allowed_graphs || activeProfile.allowed_graphs.length === 0) {
      return graphs;
    }
    const allow = new Set(activeProfile.allowed_graphs);
    const filtered = graphs.filter((g) => allow.has(g.id));
    return filtered.length > 0 ? filtered : graphs;
  }, [graphs, activeProfile]);

  useEffect(() => {
    if (!visibleGraphs.some((g) => g.id === selectedGraph)) {
      setSelectedGraph(visibleGraphs[0]?.id ?? "basic");
    }
  }, [visibleGraphs, selectedGraph]);

  useEffect(() => {
    if (!activeProfile) {
      return;
    }
    const cookieValue = encodeURIComponent(JSON.stringify(activeProfile));
    document.cookie = `copilot_project_profile=${cookieValue}; path=/; max-age=2592000; samesite=lax`;
    if (activeProfile.default_graph && visibleGraphs.some((g) => g.id === activeProfile.default_graph)) {
      setSelectedGraph(activeProfile.default_graph);
    }
  }, [activeProfile?.id]);

  const activeGraph = visibleGraphs.find((g) => g.id === selectedGraph) ?? visibleGraphs[0];

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-4 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">LangGraph AI</h1>
          <p className="text-sm text-gray-400">CopilotKit · Django · Ollama</p>
        </div>

        {/* Profile and graph selectors */}
        <div className="flex items-center gap-3 flex-wrap justify-end">
          <span className="text-sm text-gray-400">Profile:</span>
          <select
            value={selectedProfile}
            onChange={(e) => setSelectedProfile(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>

          <span className="text-sm text-gray-400">Agent:</span>
          <select
            value={selectedGraph}
            onChange={(e) => setSelectedGraph(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {visibleGraphs.map((g) => (
              <option key={g.id} value={g.id}>
                {g.label}
              </option>
            ))}
          </select>
        </div>
      </header>

      {/* Description strip */}
      <div className="px-6 py-3 bg-gray-900 border-b border-gray-800 text-sm text-gray-400 flex flex-col gap-1">
        <span>{activeGraph?.description}</span>
        <span>
          Profile root: <strong className="text-gray-300">{activeProfile?.mcp_root ?? "/workspace-data"}</strong> ·
          Tool mode: <strong className="text-gray-300">{activeProfile?.tool_mode ?? "read_only"}</strong>
        </span>
      </div>

      {/* Content */}
      <div className="flex flex-col items-center justify-center px-6 py-20 text-center">
        <p className="text-gray-500 max-w-sm">
          Open the chat sidebar (bottom-right) to start a conversation with the{" "}
          <strong className="text-gray-300">
            {activeGraph?.label}
          </strong>{" "}
          agent using the <strong className="text-gray-300">{activeProfile?.name}</strong> profile.
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
            title: activeGraph?.label ?? "AI Chat",
            initial: `Hi! I'm the **${activeGraph?.label ?? "AI"}** agent using the **${activeProfile?.name ?? "default"}** profile. How can I help?`,
          }}
        />
      </CopilotChatConfigurationProvider>
    </main>
  );
}
