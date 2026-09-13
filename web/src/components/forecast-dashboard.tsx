"use client";

import { ChamberPanel } from "@/components/chamber-panel";
import { PeerComparePanel, type PeerComparison } from "@/components/peer-compare";
import { RaceTable } from "@/components/race-table";
import { ScenarioPanel, type ScenarioBlock } from "@/components/scenario-panel";
import { SenateMap } from "@/components/senate-map";
import { Button } from "@/components/ui/button";
import { ForecastArtifact } from "@/lib/utils";
import { useCallback, useEffect, useState } from "react";

async function loadForecast(): Promise<ForecastArtifact> {
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
  const api = process.env.NEXT_PUBLIC_API_URL;
  if (api) {
    try {
      const res = await fetch(`${api}/forecast/latest`, { cache: "no-store" });
      if (res.ok) return (await res.json()) as ForecastArtifact;
    } catch {
      // fall through to static artifact
    }
  }
  const res = await fetch(`${basePath}/data/forecast_latest.json`, {
    cache: "no-store",
  });
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
      <header className="space-y-3 border-b border-[var(--line)] pb-6">
        <p className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">
          Internal research UI · {data.model_version}
        </p>
        <div className="flex flex-wrap gap-3 text-xs text-[var(--muted)]">
          <span
            className={`rounded-md border px-2 py-1 ${
              (data.method ?? "").startsWith("pymc") ||
              (data.method ?? "").startsWith("ensemble")
                ? "border-[var(--line)]"
                : "border-[var(--rep)] text-[var(--rep)]"
            }`}
          >
            method: {data.method ?? "unknown"}
          </span>
          {typeof data.generic_ballot === "number" &&
          Number.isFinite(data.generic_ballot) ? (
            <span className="rounded-md border border-[var(--line)] px-2 py-1">
              GB: {data.generic_ballot >= 0 ? "+" : ""}
              {data.generic_ballot.toFixed(1)}
            </span>
          ) : null}
          {typeof data.diagnostics?.enop_global === "number" &&
          Number.isFinite(data.diagnostics.enop_global as number) ? (
            <span className="rounded-md border border-[var(--line)] px-2 py-1">
              ENOP: {(data.diagnostics.enop_global as number).toFixed(1)}
            </span>
          ) : null}
          {typeof data.overlays?.expert_source === "string" ? (
            <span className="rounded-md border border-[var(--line)] px-2 py-1">
              ratings: {String(data.overlays.expert_source)}
            </span>
          ) : null}
          {typeof data.snapshot?.kalshi_control_p_dem === "number" &&
          Number.isFinite(data.snapshot.kalshi_control_p_dem as number) ? (
            <span className="rounded-md border border-[var(--line)] px-2 py-1">
              Kalshi control D:{" "}
              {((data.snapshot.kalshi_control_p_dem as number) * 100).toFixed(0)}%
            </span>
          ) : null}
          {data.ablation &&
          typeof (
            data.ablation.delta_p_dem_majority ??
            (data.ablation.adjusted?.p_dem_majority != null &&
            data.ablation.unadjusted?.p_dem_majority != null
              ? data.ablation.adjusted.p_dem_majority -
                data.ablation.unadjusted.p_dem_majority
              : null)
          ) === "number" ? (
            <span className="rounded-md border border-[var(--line)] px-2 py-1">
              ablation ΔDem ctl:{" "}
              {(
                (data.ablation.delta_p_dem_majority ??
                  (data.ablation.adjusted!.p_dem_majority as number) -
                    (data.ablation.unadjusted!.p_dem_majority as number)) * 100
              ).toFixed(1)}
              pp
            </span>
          ) : null}
          <span className="rounded-md border border-[var(--line)] px-2 py-1">
            run: {data.run_id}
          </span>
          <Button variant="outline" size="sm" onClick={() => void refresh()}>
            Reload artifact
          </Button>
        </div>
        {data.warnings && data.warnings.length > 0 ? (
          <p className="text-xs text-[var(--rep)]">
            Layer warnings:{" "}
            {data.warnings
              .map((w) => `${w.layer ?? "layer"}: ${w.error ?? "error"}`)
              .join(" · ")}
          </p>
        ) : null}
      </header>

      <SenateMap
        races={data.races}
        pDemControl={data.chamber.p_dem_majority}
        expectedDem={data.chamber.expected_dem_seats}
        expectedRep={
          data.chamber.expected_rep_seats ??
          100 - data.chamber.expected_dem_seats
        }
        asOf={data.forecast_as_of}
      />

      <ChamberPanel chamber={data.chamber} />
      <RaceTable races={data.races} />
      {data.scenarios ? (
        <ScenarioPanel scenarios={data.scenarios as ScenarioBlock} />
      ) : null}
      {data.peer_comparison ? (
        <PeerComparePanel peer={data.peer_comparison as PeerComparison} />
      ) : null}
    </div>
  );
}
