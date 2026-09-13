"use client";

import { pct } from "@/lib/utils";

export type ScenarioBlock = {
  note?: string;
  baseline?: { p_dem_majority?: number; expected_dem_seats?: number };
  national_dem_miss_m3?: {
    p_dem_majority?: number;
    expected_dem_seats?: number;
    delta_p_dem?: number;
  };
  national_rep_miss_m3?: {
    p_dem_majority?: number;
    expected_dem_seats?: number;
    delta_p_dem?: number;
  };
  south_dem_miss_m4?: {
    p_dem_majority?: number;
    expected_dem_seats?: number;
    delta_p_dem?: number;
  };
  reduced_poll_quality?: {
    p_dem_majority?: number;
    expected_dem_seats?: number;
    delta_p_dem?: number;
  };
};

const ROWS: { key: keyof ScenarioBlock; label: string }[] = [
  { key: "baseline", label: "Baseline (production)" },
  { key: "national_dem_miss_m3", label: "National Dem miss (−3pp)" },
  { key: "national_rep_miss_m3", label: "National Rep miss (−3pp to R / +3 D)" },
  { key: "south_dem_miss_m4", label: "South Dem miss (−4pp)" },
  { key: "reduced_poll_quality", label: "Reduced poll quality" },
];

export function ScenarioPanel({ scenarios }: { scenarios: ScenarioBlock }) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="font-display text-xl text-[var(--ink)]">
          Scenario sensitivity
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-[var(--muted)]">
          {scenarios.note ??
            "Labeled stress tests — not alternate production forecasts."}
        </p>
      </div>
      <div className="overflow-x-auto rounded-lg border border-[var(--line)]">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-[var(--panel)] text-xs uppercase tracking-wide text-[var(--muted)]">
            <tr>
              <th className="px-3 py-2 font-medium">Scenario</th>
              <th className="px-3 py-2 font-medium">P(Dem control)</th>
              <th className="px-3 py-2 font-medium">E[Dem seats]</th>
              <th className="px-3 py-2 font-medium">Δ P(Dem)</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map(({ key, label }) => {
              const row = scenarios[key] as
                | {
                    p_dem_majority?: number;
                    expected_dem_seats?: number;
                    delta_p_dem?: number;
                  }
                | undefined;
              if (!row) return null;
              return (
                <tr key={key} className="border-t border-[var(--line)]">
                  <td className="px-3 py-2">{label}</td>
                  <td className="px-3 py-2">
                    {row.p_dem_majority != null
                      ? pct(row.p_dem_majority, 0)
                      : "—"}
                  </td>
                  <td className="px-3 py-2">
                    {row.expected_dem_seats != null
                      ? row.expected_dem_seats.toFixed(1)
                      : "—"}
                  </td>
                  <td className="px-3 py-2">
                    {row.delta_p_dem != null
                      ? `${(row.delta_p_dem * 100).toFixed(1)} pp`
                      : "—"}
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
