"use client";

import { ChamberForecast, pct } from "@/lib/utils";

export function ChamberPanel({ chamber }: { chamber: ChamberForecast }) {
  const hist = chamber.seat_histogram;
  const maxP = Math.max(...hist.map((h) => h.probability), 0.01);
  const lo = Math.min(...hist.map((h) => h.dem_seats));
  const hi = Math.max(...hist.map((h) => h.dem_seats));
  const pFifty = chamber.p_fifty_fifty ?? chamber.p_tie ?? 0;
  const expectedRep = chamber.expected_rep_seats ?? 100 - chamber.expected_dem_seats;

  return (
    <section className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat
          label="Dem control (≥51)"
          value={pct(chamber.p_dem_majority, 1)}
          tone="dem"
        />
        <Stat
          label="50–50 (R via VP)"
          value={pct(pFifty, 1)}
          tone="neutral"
        />
        <Stat
          label="Rep control (≤50)"
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
              Democratic seats from correlated draws (held {chamber.held_dem} D /{" "}
              {chamber.held_rep} R + contested outcomes). Expected{" "}
              <span className="font-medium text-[var(--ink)]">
                {chamber.expected_dem_seats.toFixed(1)} D
              </span>
              {" / "}
              <span className="font-medium text-[var(--ink)]">
                {expectedRep.toFixed(1)} R
              </span>
              . A 50–50 chamber is Republican control under the VP tiebreak.
            </p>
          </div>
          <p className="text-xs text-[var(--muted)]">
            Range {lo}–{hi}
          </p>
        </div>
        <div className="flex h-40 items-end gap-px overflow-x-auto">
          {hist.map((bin) => {
            const isDemControl = bin.dem_seats >= chamber.majority_threshold;
            const isFifty = bin.dem_seats === 50;
            const barPx = Math.max(2, Math.round((bin.probability / maxP) * 152));
            return (
              <div
                key={bin.dem_seats}
                className="group relative flex h-full min-w-[10px] flex-1 flex-col items-center justify-end"
                title={`${bin.dem_seats} Dem seats: ${pct(bin.probability, 1)}`}
              >
                <div
                  className={`w-full rounded-t-sm transition ${
                    isFifty
                      ? "bg-[var(--rep)]/80"
                      : isDemControl
                        ? "bg-[var(--dem)]"
                        : "bg-[var(--rep)]"
                  }`}
                  style={{ height: `${barPx}px` }}
                />
              </div>
            );
          })}
        </div>
        <div className="mt-2 flex justify-between text-[10px] uppercase tracking-wide text-[var(--muted)]">
          <span>{lo} D</span>
          <span>50</span>
          <span>{hi} D</span>
        </div>
        {chamber.note ? (
          <p className="mt-4 text-xs leading-relaxed text-[var(--muted)]">
            {chamber.note}
          </p>
        ) : null}
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
  tone: "dem" | "rep" | "neutral";
}) {
  const color =
    tone === "dem"
      ? "text-[var(--dem)]"
      : tone === "rep"
        ? "text-[var(--rep)]"
        : "text-[var(--accent)]";
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)]/70 px-4 py-3">
      <p className="text-xs uppercase tracking-[0.14em] text-[var(--muted)]">
        {label}
      </p>
      <p className={`mt-1 font-display text-3xl tabular-nums ${color}`}>{value}</p>
    </div>
  );
}
