"use client";

import { pct } from "@/lib/utils";

export type PeerComparison = {
  as_of_peers?: string;
  disclaimer?: string;
  control_p_dem?: Record<string, number | null | undefined>;
  races?: {
    state: string;
    ours?: number | null;
    rating?: string | null;
    ddhq?: number | null;
    votehub?: number | null;
    kalshi?: number | null;
  }[];
  sources?: Record<string, { label?: string; url?: string; note?: string }>;
};

const COLS = [
  { key: "ours", label: "Ours" },
  { key: "kalshi", label: "Kalshi" },
  { key: "ddhq", label: "DDHQ" },
  { key: "votehub", label: "VoteHub" },
] as const;

function fmt(p: number | null | undefined): string {
  if (p == null || Number.isNaN(p)) return "—";
  return pct(p, 0);
}

export function PeerComparePanel({ peer }: { peer: PeerComparison }) {
  const control = peer.control_p_dem ?? {};
  const races = peer.races ?? [];

  return (
    <section className="space-y-4">
      <div>
        <h2 className="font-display text-xl text-[var(--ink)]">
          Peer model comparison
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-[var(--muted)]">
          {peer.disclaimer ??
            "Research context only — peers are not averaged into this forecast."}
          {peer.as_of_peers ? ` Peer snapshot as of ${peer.as_of_peers}.` : null}
        </p>
      </div>

      <div className="overflow-x-auto rounded-lg border border-[var(--line)]">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-[var(--panel)] text-xs uppercase tracking-wide text-[var(--muted)]">
            <tr>
              <th className="px-3 py-2 font-medium">Target</th>
              {COLS.map((c) => (
                <th key={c.key} className="px-3 py-2 font-medium">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-[var(--line)] bg-[var(--panel)]/40">
              <td className="px-3 py-2.5 font-medium text-[var(--ink)]">
                Dem control
              </td>
              {COLS.map((c) => (
                <td key={c.key} className="px-3 py-2.5 tabular-nums">
                  {fmt(control[c.key])}
                </td>
              ))}
            </tr>
            {races.map((r) => (
              <tr
                key={r.state}
                className="border-t border-[var(--line)] hover:bg-[var(--panel)]/60"
              >
                <td className="px-3 py-2.5 font-medium text-[var(--ink)]">
                  {r.state}
                  {r.rating ? (
                    <span className="ml-2 text-xs font-normal text-[var(--muted)]">
                      {r.rating}
                    </span>
                  ) : null}
                </td>
                {COLS.map((c) => (
                  <td key={c.key} className="px-3 py-2.5 tabular-nums">
                    {fmt(
                      c.key === "ours"
                        ? r.ours
                        : c.key === "kalshi"
                          ? r.kalshi
                          : c.key === "ddhq"
                            ? r.ddhq
                            : r.votehub,
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
