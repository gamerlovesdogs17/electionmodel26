"use client";

import {
  RaceForecast,
  modeledProbability,
  pct,
  signedRaceMargin,
} from "@/lib/utils";
import { presentRace } from "@/lib/race-presentation";
import { useMemo, useState } from "react";

export function RaceTable({ races }: { races: RaceForecast[] }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"toss" | "dem" | "state">("toss");

  const rows = useMemo(() => {
    let list = [...races];
    if (q.trim()) {
      const needle = q.trim().toUpperCase();
      list = list.filter(
        (r) =>
          r.state.includes(needle) ||
          r.race_id.toUpperCase().includes(needle) ||
          (r.modeled_candidate ?? r.dem_candidate ?? "").toUpperCase().includes(needle) ||
          (r.opposing_candidate ?? r.rep_candidate ?? "").toUpperCase().includes(needle),
      );
    }
    list.sort((a, b) => {
      if (sort === "state") return a.state.localeCompare(b.state);
      if (sort === "dem") return modeledProbability(b) - modeledProbability(a);
      return (
        Math.abs(modeledProbability(a) - 0.5) -
        Math.abs(modeledProbability(b) - 0.5)
      );
    });
    return list;
  }, [races, q, sort]);

  if (!races.length) {
    return (
      <div className="rounded-lg border border-dashed border-[var(--line)] p-8 text-center text-sm text-[var(--muted)]">
        No contested races in this snapshot.
      </div>
    );
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="font-display text-xl text-[var(--ink)]">
            Seat-by-seat probabilities
          </h2>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Model-derived ratings use each modeled candidate&apos;s probability.
            Ballot party and chamber caucus accounting remain separate.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter state or candidate…"
            className="h-9 rounded-md border border-[var(--line)] bg-[var(--paper)] px-3 text-sm outline-none ring-[var(--accent)] focus:ring-2"
          />
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as typeof sort)}
            className="h-9 rounded-md border border-[var(--line)] bg-[var(--paper)] px-3 text-sm"
          >
            <option value="toss">Closest first</option>
            <option value="dem">Modeled probability</option>
            <option value="state">State</option>
          </select>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-[var(--line)]">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-[var(--panel)] text-xs uppercase tracking-wide text-[var(--muted)]">
            <tr>
              <th className="px-3 py-2 font-medium">State</th>
              <th className="hidden px-3 py-2 font-medium lg:table-cell">
                Candidates
              </th>
              <th className="px-3 py-2 font-medium">Rating</th>
              <th className="px-3 py-2 font-medium">P(modeled)</th>
              <th className="px-3 py-2 font-medium">Margin</th>
              <th className="hidden px-3 py-2 font-medium sm:table-cell">
                90% interval
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const view = presentRace(r);
              const demParty = (view.favored_party === "I" || r.modeled_ballot_party === "I")
                ? "I"
                : (r.modeled_ballot_party ?? r.dem_party) === "I"
                  ? "I"
                  : "D";
              const pModeled = view.unsupported_probability
                ? null
                : (view.favored_win_probability ?? modeledProbability(r));
              return (
                <tr
                  key={r.race_id}
                  className="border-t border-[var(--line)] hover:bg-[var(--panel)]/60"
                >
                  <td className="px-3 py-2.5 font-medium text-[var(--ink)]">
                    {r.state}
                    {view.rcv_detail ? (
                      <span className="ml-2 rounded border border-[var(--line)] px-1.5 py-0.5 text-[10px] font-normal uppercase tracking-wide text-[var(--muted)]">
                        RCV
                      </span>
                    ) : null}
                    {r.is_open ? (
                      <span className="ml-2 text-xs font-normal text-[var(--muted)]">
                        open
                      </span>
                    ) : r.incumbent_party ? (
                      <span className="ml-2 text-xs font-normal text-[var(--muted)]">
                        {r.incumbent_party} inc.
                      </span>
                    ) : null}
                  </td>
                  <td className="hidden px-3 py-2.5 text-xs text-[var(--muted)] lg:table-cell">
                    {view.candidates.length ? (
                      <div className="space-y-1">
                        {view.candidates
                          .slice(0, view.rcv_detail || view.unsupported_probability ? 6 : 2)
                          .map((candidate) => (
                          <div
                            key={candidate.candidate_id || candidate.candidate_name}
                            className="flex justify-between gap-3"
                          >
                            <span className={candidate.ballot_party === "I" || candidate.ballot_party === "L" ? "text-[var(--ind)]" : ""}>
                              {candidate.candidate_name}
                              <span className="ml-1 opacity-70">({candidate.ballot_party})</span>
                            </span>
                            {view.rcv_detail && !view.unsupported_probability ? (
                              <span className="tabular-nums">{pct(candidate.p_win, 1)}</span>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <>
                        <span className={demParty === "I" ? "text-[var(--ind)]" : ""}>
                          {r.modeled_candidate ?? r.dem_candidate ?? (demParty === "I" ? "Independent" : "Dem")}
                          <span className="ml-1 opacity-70">({demParty})</span>
                        </span>
                        {" / "}
                        {r.opposing_candidate ?? r.rep_candidate ?? "Rep"}
                        <span className="ml-1 opacity-70">(R)</span>
                      </>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-xs font-medium text-[var(--ink)]">
                    {view.rating ?? (view.unsupported_probability ? "Withheld" : "—")}
                  </td>
                  <td className="px-3 py-2.5">
                    {view.unsupported_probability ? (
                      <span className="text-xs text-[var(--muted)]">
                        {view.unsupported_message ?? "Probability withheld"}
                      </span>
                    ) : (
                      <div className="flex items-center gap-2">
                        <div className="h-2 w-20 overflow-hidden rounded bg-[var(--rep)]/25 sm:w-28">
                          <div
                            className="h-full bg-[var(--dem)]"
                            style={{
                              width: `${
                                typeof pModeled === "number" && Number.isFinite(pModeled)
                                  ? Math.min(100, Math.max(0, pModeled * 100))
                                  : 0
                              }%`,
                            }}
                          />
                        </div>
                        <span className="tabular-nums">{pct(pModeled, 0)}</span>
                      </div>
                    )}
                    {view.rcv_detail && !view.unsupported_probability ? (
                      <span className="mt-1 block text-xs text-[var(--muted)]">
                        D-caucus seat: {pct(view.seat_control_p_dem_caucus, 1)}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2.5 tabular-nums text-[var(--ink)]">
                    {view.rcv_detail
                      ? "—"
                      : typeof r.mean_margin === "number"
                        ? signedRaceMargin(r)
                        : "—"}
                    <span className="ml-1 text-xs text-[var(--muted)]">
                      {!view.rcv_detail && typeof r.sd_margin === "number"
                        ? `±${r.sd_margin.toFixed(1)}`
                        : ""}
                    </span>
                  </td>
                  <td className="hidden px-3 py-2.5 tabular-nums text-[var(--muted)] sm:table-cell">
                    {view.rcv_detail
                      ? `Exhaustion: ${pct(view.rcv_detail.exhausted_ballot_share, 1)}`
                      : typeof r.ci05 === "number" && typeof r.ci95 === "number"
                        ? `${signedRaceMargin(r, r.ci05)} – ${signedRaceMargin(r, r.ci95)}`
                        : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!rows.length ? (
        <p className="text-sm text-[var(--muted)]">No races match that filter.</p>
      ) : null}
    </section>
  );
}
