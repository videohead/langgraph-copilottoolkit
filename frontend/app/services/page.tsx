"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

type ServiceStartup = {
  status: "up" | "down";
  probeUrl: string | null;
  statusCode: number | null;
  error: string | null;
};

type ServiceStatus = {
  id: string;
  name: string;
  group: "core" | "additional";
  location: string;
  detail?: string | null;
  startup: ServiceStartup;
};

type GraphChart = {
  graphId: string;
  pngUrl: string | null;
  available: boolean;
};

type ServicesPayload = {
  generatedAt: string;
  services: ServiceStatus[];
  graphCharts: GraphChart[];
};

export default function ServicesDashboardPage() {
  const [payload, setPayload] = useState<ServicesPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const response = await fetch("/api/services", { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`Request failed with HTTP ${response.status}`);
        }
        const nextPayload = (await response.json()) as ServicesPayload;
        if (cancelled) {
          return;
        }
        setPayload(nextPayload);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Unable to load services dashboard");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    load();
    const intervalId = setInterval(load, 12000);

    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, []);

  const coreServices = useMemo(
    () => payload?.services.filter((service) => service.group === "core") ?? [],
    [payload],
  );
  const additionalServices = useMemo(
    () => payload?.services.filter((service) => service.group === "additional") ?? [],
    [payload],
  );

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100 px-6 py-6 md:px-10">
      <div className="max-w-5xl mx-auto flex flex-col gap-6">
        <header className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h1 className="text-2xl font-semibold">Services Dashboard</h1>
            <p className="text-sm text-gray-400">Startup status and graph chart access</p>
          </div>
          <Link
            href="/"
            className="inline-flex items-center rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200 hover:bg-gray-800"
          >
            Back to chat
          </Link>
        </header>

        {loading && !payload ? (
          <section className="rounded-xl border border-gray-800 bg-gray-900/80 p-4 text-sm text-gray-300">
            Loading service status...
          </section>
        ) : null}

        {error ? (
          <section className="rounded-xl border border-red-800/60 bg-red-950/40 p-4 text-sm text-red-200">
            {error}
          </section>
        ) : null}

        <ServiceGroup title="Core services" services={coreServices} />
        <ServiceGroup title="Additional services" services={additionalServices} />

        <section className="rounded-xl border border-gray-800 bg-gray-900/80 p-4">
          <h2 className="text-lg font-medium">Graph chart PNGs</h2>
          <p className="mt-1 text-sm text-gray-400">
            Quick links to generated Mermaid PNGs per graph.
          </p>

          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="text-left text-gray-400 border-b border-gray-800">
                  <th className="py-2 pr-3 font-medium">Graph</th>
                  <th className="py-2 pr-3 font-medium">PNG</th>
                  <th className="py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {(payload?.graphCharts ?? []).map((chart) => (
                  <tr key={chart.graphId} className="border-b border-gray-900">
                    <td className="py-2 pr-3 text-gray-200">{chart.graphId}</td>
                    <td className="py-2 pr-3">
                      {chart.available && chart.pngUrl ? (
                        <a
                          href={chart.pngUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-blue-300 hover:text-blue-200 underline"
                        >
                          Open PNG
                        </a>
                      ) : (
                        <span className="text-gray-500">Not generated</span>
                      )}
                    </td>
                    <td className="py-2">
                      {chart.available ? (
                        <span className="text-emerald-300">available</span>
                      ) : (
                        <span className="text-yellow-300">missing</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <footer className="text-xs text-gray-500">
          Last update: {payload?.generatedAt ?? "not available"}
        </footer>
      </div>
    </main>
  );
}

function ServiceGroup({ title, services }: { title: string; services: ServiceStatus[] }) {
  return (
    <section className="rounded-xl border border-gray-800 bg-gray-900/80 p-4">
      <h2 className="text-lg font-medium">{title}</h2>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="text-left text-gray-400 border-b border-gray-800">
              <th className="py-2 pr-3 font-medium">Service</th>
              <th className="py-2 pr-3 font-medium">Detail</th>
              <th className="py-2 pr-3 font-medium">Location</th>
              <th className="py-2 pr-3 font-medium">Startup</th>
              <th className="py-2 font-medium">Probe</th>
            </tr>
          </thead>
          <tbody>
            {services.map((service) => {
              const isUp = service.startup.status === "up";
              return (
                <tr key={service.id} className="border-b border-gray-900">
                  <td className="py-2 pr-3 text-gray-200">{service.name}</td>
                  <td className="py-2 pr-3 text-gray-300">
                    {service.detail?.trim() ? service.detail : "-"}
                  </td>
                  <td className="py-2 pr-3">
                    <a
                      href={service.location}
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-300 hover:text-blue-200 underline"
                    >
                      {service.location}
                    </a>
                  </td>
                  <td className="py-2 pr-3">
                    <span
                      className={isUp ? "text-emerald-300" : "text-red-300"}
                      aria-label={`${service.name} startup ${isUp ? "up" : "down"}`}
                    >
                      {isUp ? "up" : "down"}
                    </span>
                  </td>
                  <td className="py-2 text-gray-400">
                    {service.startup.probeUrl ?? "n/a"}
                    {service.startup.statusCode ? ` (${service.startup.statusCode})` : ""}
                    {service.startup.error ? ` - ${service.startup.error}` : ""}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
