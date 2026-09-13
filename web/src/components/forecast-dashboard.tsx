"use client";

import { ChamberPanel } from "@/components/chamber-panel";
import { RaceTable } from "@/components/race-table";
import { Button } from "@/components/ui/button";
import { ForecastArtifact } from "@/lib/utils";
import { useCallback, useEffect, useState } from "react";

async function loadForecast(): Promise<ForecastArtifact> {
  const api = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8787";
  try {
    const res = await fetch(`${api}/forecast/latest`, { cache: "no-store" });
    if (res.ok) return (await res.json()) as ForecastArtifact;
  } catch {
    // fall through to static artifact
  }
  const res = await fetch("/data/forecast_latest.json", { cache: "no-store" });
  if (!res.ok) throw new Error("Forecast artifact unavailable");
  return (await res.json()) as ForecastArtifact;
}

export function ForecastDashboard() {
  const [data, setData] = useState<ForecastArtifact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const art = await loadForecast();
      setData(art);
    } catch (e) {
      setData(null);
      setError(e instanceof Error ? e.message : "Failed to load forecast");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (loading) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="h-28 rounded-lg bg-[var(--panel)]" />
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="h-24 rounded-lg bg-[var(--panel)]" />
          <div className="h-24 rounded-lg bg-[var(--panel)]" />
          <div className="h-24 rounded-lg bg-[var(--panel)]" />
        </div>
        <div className="h-48 rounded-lg bg-[var(--panel)]" />
        <p className="text-sm text-[var(--muted)]">Loading forecast artifact…</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="rounded-lg border border-dashed border-[var(--line)] bg-[var(--panel)]/50 p-8 text-center">
        <h2 className="font-display text-xl text-[var(--ink)]">
          No forecast loaded
        </h2>
        <p className="mx-auto mt-2 max-w-md text-sm text-[var(--muted)]">
          {error ??
            "Generate an artifact with `python -m midterms.cli forecast`, then refresh."}
        </p>
        <Button className="mt-4" onClick={() => void refresh()}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-10">
      <header className="space-y-3 border-b border-[var(--line)] pb-8">
        <p className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">
          Internal research UI · {data.model_version}
        </p>
        <h1 className="font-display text-4xl leading-tight text-[var(--ink)] sm:text-5xl">
          Senate Probability Lab
        </h1>
        <p className="max-w-2xl text-base text-[var(--muted)] sm:text-lg">
          Seat-by-seat and chamber-wide probabilities for{" "}
          <span className="text-[var(--ink)]">{data.election_id}</span>, as of{" "}
          <span className="text-[var(--ink)]">{data.forecast_as_of}</span>.
          Chamber totals come from joint correlated draws — not independent
          race calls.
        </p>
        <div className="flex flex-wrap gap-3 text-xs text-[var(--muted)]">
          <span className="rounded-md border border-[var(--line)] px-2 py-1">
            method: {data.method}
          </span>
          <span className="rounded-md border border-[var(--line)] px-2 py-1">
            run: {data.run_id}
          </span>
          <Button variant="outline" size="sm" onClick={() => void refresh()}>
            Reload artifact
          </Button>
        </div>
      </header>

      <ChamberPanel chamber={data.chamber} />
      <RaceTable races={data.races} />
    </div>
  );
}
