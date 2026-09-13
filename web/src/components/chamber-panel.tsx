"use client";

import { ChamberForecast, pct } from "@/lib/utils";

export function ChamberPanel({ chamber }: { chamber: ChamberForecast }) {
  const hist = chamber.seat_histogram ?? [];
  const maxP = Math.max(...hist.map((h) => h.probability), 0.01);
  const lo = hist.length ? Math.min(...hist.map((h) => h.dem_seats)) : 0;
  const hi = hist.length ? Math.max(...hist.map((h) => h.dem_seats)) : 0;
  const expectedDem =
    typeof chamber.expected_dem_seats === "number" &&
    Number.isFinite(chamber.expected_dem_seats)
      ? chamber.expected_dem_seats
      : 0;
  const expectedRep =
    typeof chamber.expected_rep_seats === "number" &&
    Number.isFinite(chamber.expected_rep_seats)
      ? chamber.expected_rep_seats
      : 100 - expectedDem;

  return (
    <section className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2">
        <Stat
          label="Democrats control"
          value={pct(chamber.p_dem_majority, 1)}
          tone="dem"
        />
        <Stat
          label="Republicans control"
          value={pct(chamber.p_rep_majority, 1)}
          tone="rep"
        />
      </div>

      <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)]/70 p-4 sm:p-5">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 className="font-display text-xl text-[var(--ink)]">
              Joint seat-total distribution
            </h2>
            <p className="mt-1 max-w-xl text-sm text-[var(--muted)]">
              Democratic seats from correlated draws (held {chamber.held_dem} /{" "}
              {chamber.held_rep} R + contested). Expected{" "}
              <span className="font-medium text-[var(--ink)]">
                {expectedDem.toFixed(1)} D
              </span>
              {" / "}
              <span className="font-medium text-[var(--ink)]">
                {expectedRep.toFixed(1)} R
              </span>
              . Bars at ≤50 seats are Republican control.
            </p>
          </div>
          <p className="text-xs text-[var(--muted)]">
            Range {lo}–{hi}
          </p>
        </div>
        <div className="flex h-40 items-end gap-px overflow-x-auto">
          {hist.length ? (
            hist.map((bin) => {
            const isDemControl = bin.dem_seats >= chamber.majority_threshold;
            // Floor height so mid-range bins (incl. 50) stay visible
            const barPx = Math.max(
              4,
              Math.round((bin.probability / maxP) * 152),
            );
            return (
              <div
                key={bin.dem_seats}
                className="group relative flex h-full min-w-[12px] flex-1 flex-col items-center justify-end"
                title={`${bin.dem_seats} Dem seats → ${
                  isDemControl ? "Dem" : "Rep"
                } control: ${pct(bin.probability, 1)}`}
              >
                <div
                  className={`w-full rounded-t-sm transition ${
                    isDemControl ? "bg-[var(--dem)]" : "bg-[var(--rep)]"
                  }`}
                  style={{ height: `${barPx}px` }}
                />
              </div>
            );
          })
          ) : null}
        </div>
        <div className="mt-2 flex justify-between text-[10px] uppercase tracking-wide text-[var(--muted)]">
          <span>{lo}</span>
          <span>{chamber.majority_threshold}+ Dem control</span>
          <span>{hi}</span>
        </div>
      </div>
    </section>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "dem" | "rep";
}) {
  const color = tone === "dem" ? "text-[var(--dem)]" : "text-[var(--rep)]";
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)]/70 px-4 py-3">
      <p className="text-xs uppercase tracking-[0.14em] text-[var(--muted)]">
        {label}
      </p>
      <p className={`mt-1 font-display text-3xl tabular-nums ${color}`}>{value}</p>
    </div>
  );
}
