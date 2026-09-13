"use client";

import type { CSSProperties } from "react";
import {
  FIPS_TO_STATE,
  RaceForecast,
  STATE_NAME,
  demFill,
  marginFill,
  pct,
  primaryRace,
  ratingFill,
  signedMargin,
} from "@/lib/utils";
import { geoAlbersUsa, geoPath, type GeoPermissibleObjects } from "d3-geo";
import { useEffect, useMemo, useState } from "react";
import { feature } from "topojson-client";

type MapMode = "probability" | "ratings" | "margin";

type Props = {
  races: RaceForecast[];
  pDemControl: number;
  expectedDem: number;
  expectedRep: number;
  asOf: string;
};

type HoverState = {
  abbr: string;
  races: RaceForecast[];
  x: number;
  y: number;
};

function caucusFavored(race: RaceForecast): "D" | "R" {
  if (race.favored_caucus === "D" || race.favored_caucus === "R") {
    return race.favored_caucus;
  }
  if (race.favored_party === "R") return "R";
  return "D"; // D or I
}

export function SenateMap({
  races,
  pDemControl,
  expectedDem,
  expectedRep,
  asOf,
}: Props) {
  const [mode, setMode] = useState<MapMode>("probability");
  const [open, setOpen] = useState(false);
  const [paths, setPaths] = useState<{ id: string; d: string; abbr: string }[]>(
    [],
  );
  const [hover, setHover] = useState<HoverState | null>(null);

  const byState = useMemo(() => {
    const m = new Map<string, RaceForecast[]>();
    for (const r of races) {
      const list = m.get(r.state) ?? [];
      list.push(r);
      m.set(r.state, list);
    }
    return m;
  }, [races]);

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
    void fetch(`${base}/data/states-10m.json`)
      .then((r) => r.json())
      .then((topo: { objects: { states: unknown }; type: string; arcs: unknown }) => {
        const states = feature(
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          topo as any,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (topo.objects as any).states,
        );
        const projection = geoAlbersUsa().fitSize([960, 520], states);
        const path = geoPath(projection);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const feats = (states as any).features as {
          id: string | number;
          properties?: { name?: string };
        }[];
        setPaths(
          feats
            .map((f) => {
              const fips = String(f.id).padStart(2, "0");
              const abbr = FIPS_TO_STATE[fips];
              if (!abbr || abbr === "DC") return null;
              const d = path(f as unknown as GeoPermissibleObjects);
              if (!d) return null;
              return { id: fips, d, abbr };
            })
            .filter(Boolean) as { id: string; d: string; abbr: string }[],
        );
      })
      .catch(() => setPaths([]));
  }, []);

  const demLead = Math.round(expectedDem);
  const repLead = Math.round(expectedRep);
  const controlLabel = pDemControl >= 0.5 ? "Democrats" : "Republicans";
  const controlPct = pct(Math.max(pDemControl, 1 - pDemControl), 0);
  const controlTone =
    pDemControl >= 0.5 ? "text-[var(--dem)]" : "text-[var(--rep)]";

  return (
    <section className="space-y-5">
      <div className="text-center">
        <h2 className="font-display text-3xl leading-tight text-[var(--ink)] sm:text-4xl">
          <span className={controlTone}>{controlLabel}</span> have a{" "}
          <span className={controlTone}>{controlPct}</span> chance of
          controlling the Senate.
        </h2>
        <p className="mt-2 text-xs uppercase tracking-[0.16em] text-[var(--muted)]">
          As of {asOf} · 50–50 counts as Republican control (VP) · Ind caucus
          with Dem
        </p>
      </div>

      <div className="mx-auto flex max-w-xl items-center gap-3">
        <div
          className="h-3 flex-1 overflow-hidden rounded-full"
          style={{
            background: `linear-gradient(90deg, #1f5f8b 0%, #3d7ea8 45%, #c46a5c 55%, #a33b2d 100%)`,
          }}
        />
      </div>
      <div className="mx-auto flex max-w-xl justify-between text-sm font-medium">
        <span className="text-[var(--dem)]">{demLead} Dem caucus</span>
        <span className="text-[var(--rep)]">{repLead} Republicans</span>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-4 text-xs text-[var(--muted)]">
          <Legend swatch="bg-[var(--dem)]" label="Dem/Ind hold" />
          <Legend
            swatch="bg-[var(--dem)] opacity-90"
            label="Dem/Ind flip"
            hatch
          />
          <Legend swatch="bg-[var(--rep)]" label="Rep hold" />
          <Legend
            swatch="bg-[var(--rep)] opacity-90"
            label="Rep flip"
            hatch
          />
          <Legend swatch="bg-[#c5d0d7]" label="Not up" />
        </div>

        <div className="relative">
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-md border border-[var(--line)] bg-[var(--panel)] px-3 py-1.5 text-sm text-[var(--ink)]"
            onClick={() => setOpen((v) => !v)}
          >
            {mode === "probability"
              ? "Probability"
              : mode === "ratings"
                ? "Ratings"
                : "Margin"}
            <span aria-hidden>▾</span>
          </button>
          {open ? (
            <div className="absolute right-0 z-20 mt-1 min-w-[9rem] overflow-hidden rounded-md border border-[var(--line)] bg-white shadow-md">
              {(
                [
                  ["probability", "Probability"],
                  ["ratings", "Ratings"],
                  ["margin", "Margin"],
                ] as const
              ).map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  className={`block w-full px-3 py-2 text-left text-sm hover:bg-[var(--panel)] ${
                    mode === k ? "bg-[var(--panel)] font-medium" : ""
                  }`}
                  onClick={() => {
                    setMode(k);
                    setOpen(false);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      <div className="relative overflow-hidden rounded-lg border border-[var(--line)] bg-[#f7f9fa] p-2 sm:p-4">
        <svg viewBox="0 0 960 520" className="h-auto w-full" role="img">
          <defs>
            <pattern
              id="hatch-dem"
              width="8"
              height="8"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <rect width="8" height="8" fill="#1f5f8b" />
              <line
                x1="0"
                y1="0"
                x2="0"
                y2="8"
                stroke="#f7f9fa"
                strokeWidth="3"
              />
            </pattern>
            <pattern
              id="hatch-rep"
              width="8"
              height="8"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <rect width="8" height="8" fill="#a33b2d" />
              <line
                x1="0"
                y1="0"
                x2="0"
                y2="8"
                stroke="#f7f9fa"
                strokeWidth="3"
              />
            </pattern>
          </defs>
          {paths.map((p) => {
            const stateRaces = byState.get(p.abbr) ?? [];
            const race = primaryRace(stateRaces);
            const fill = raceFill(race, mode);
            return (
              <path
                key={p.id}
                d={p.d}
                fill={fill}
                stroke="#f7f9fa"
                strokeWidth={1.1}
                className="cursor-pointer transition-opacity hover:opacity-90"
                onMouseEnter={(e) => {
                  const rect = (
                    e.currentTarget.ownerSVGElement as SVGSVGElement
                  ).getBoundingClientRect();
                  setHover({
                    abbr: p.abbr,
                    races: stateRaces,
                    x: e.clientX - rect.left,
                    y: e.clientY - rect.top,
                  });
                }}
                onMouseMove={(e) => {
                  const rect = (
                    e.currentTarget.ownerSVGElement as SVGSVGElement
                  ).getBoundingClientRect();
                  setHover((h) =>
                    h
                      ? {
                          ...h,
                          x: e.clientX - rect.left,
                          y: e.clientY - rect.top,
                        }
                      : h,
                  );
                }}
                onMouseLeave={() => setHover(null)}
              />
            );
          })}
        </svg>

        {hover ? (
          <MapTooltip
            abbr={hover.abbr}
            races={hover.races}
            style={{
              left: Math.min(hover.x + 12, 640),
              top: Math.max(hover.y - 12, 8),
            }}
          />
        ) : null}
      </div>
    </section>
  );
}

function raceFill(race: RaceForecast | null, mode: MapMode): string {
  if (!race) return "#d5dde2";
  const fav = caucusFavored(race);
  if (mode === "probability") {
    if (race.is_flip) {
      return fav === "D" ? "url(#hatch-dem)" : "url(#hatch-rep)";
    }
    return demFill(race.p_dem);
  }
  if (mode === "margin") {
    if (race.is_flip) {
      return race.mean_margin >= 0 ? "url(#hatch-dem)" : "url(#hatch-rep)";
    }
    return marginFill(race.mean_margin);
  }
  if (race.is_flip) {
    return fav === "D" ? "url(#hatch-dem)" : "url(#hatch-rep)";
  }
  return ratingFill(race.rating ?? "Tossup");
}

function Legend({
  swatch,
  label,
  hatch,
}: {
  swatch: string;
  label: string;
  hatch?: boolean;
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className={`inline-block h-3 w-4 rounded-sm border border-[var(--line)] ${swatch}`}
        style={
          hatch
            ? {
                backgroundImage:
                  "repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(255,255,255,0.55) 2px, rgba(255,255,255,0.55) 4px)",
              }
            : undefined
        }
      />
      {label}
    </span>
  );
}

function MapTooltip({
  abbr,
  races,
  style,
}: {
  abbr: string;
  races: RaceForecast[];
  style: CSSProperties;
}) {
  const name = STATE_NAME[abbr] ?? abbr;
  if (!races.length) {
    return (
      <div
        className="pointer-events-none absolute z-30 w-64 rounded-lg border border-[var(--line)] bg-white p-3 shadow-lg"
        style={style}
      >
        <p className="font-display text-base text-[var(--ink)]">
          {name}&apos;s Senate seat
        </p>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Not contested in this cycle.
        </p>
      </div>
    );
  }

  return (
    <div
      className="pointer-events-none absolute z-30 w-[18.5rem] rounded-lg border border-[var(--line)] bg-white p-3 shadow-lg"
      style={style}
    >
      <p className="font-display text-base leading-snug text-[var(--ink)]">
        {name}
        {races.length > 1 ? ` · ${races.length} contests` : " · Senate"}
      </p>
      <div className="mt-2 space-y-3">
        {races.map((race) => (
          <RaceTooltipBlock key={race.race_id} race={race} />
        ))}
      </div>
    </div>
  );
}

function RaceTooltipBlock({ race }: { race: RaceForecast }) {
  const demParty = race.dem_party === "I" ? "I" : "D";
  const favoredIndOrDem = race.p_dem >= 0.5;
  const favoredName = favoredIndOrDem
    ? (race.dem_candidate ?? (demParty === "I" ? "Independent" : "Democrat"))
    : (race.rep_candidate ?? "Republican");
  const favoredP = favoredIndOrDem ? race.p_dem : race.p_rep;
  const favoredTone = favoredIndOrDem
    ? demParty === "I"
      ? "text-[#5a6a3a]"
      : "text-[var(--dem)]"
    : "text-[var(--rep)]";
  const special =
    race.seat_class === "special" ? "Special" : race.seat_class === "II" ? "Class II" : null;

  return (
    <div className="border-t border-[var(--line)] pt-2 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap gap-1.5">
        <span
          className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
            race.rating?.includes("D")
              ? "bg-[#d7e6f2] text-[var(--dem)]"
              : race.rating?.includes("R")
                ? "bg-[#f0d8d3] text-[var(--rep)]"
                : "bg-[#efe8c8] text-[#6a5f20]"
          }`}
        >
          {race.rating ?? "Tossup"}
        </span>
        {special ? (
          <span className="rounded-full border border-[var(--line)] px-2 py-0.5 text-[11px] text-[var(--muted)]">
            {special}
          </span>
        ) : null}
        {race.is_flip ? (
          <span className="rounded-full border border-[var(--line)] px-2 py-0.5 text-[11px] text-[var(--muted)]">
            Flip
          </span>
        ) : null}
      </div>
      <p className={`mt-2 text-sm font-medium ${favoredTone}`}>
        {favoredName} has a {pct(favoredP, 1)} chance.
      </p>
      <div className="mt-2">
        <CandidateRow
          name={race.dem_candidate ?? (demParty === "I" ? "Independent" : "Democrat")}
          party={demParty}
          share={race.dem_share ?? 50 + race.mean_margin / 2}
        />
        <CandidateRow
          name={race.rep_candidate ?? "Republican"}
          party="R"
          share={race.rep_share ?? 50 - race.mean_margin / 2}
        />
        <div className="mt-1 flex justify-between text-sm">
          <span className="text-[var(--muted)]">Margin</span>
          <span
            className={`font-medium ${
              race.mean_margin >= 0 ? "text-[var(--dem)]" : "text-[var(--rep)]"
            }`}
          >
            {signedMargin(race.mean_margin)}
          </span>
        </div>
      </div>
    </div>
  );
}

function CandidateRow({
  name,
  party,
  share,
}: {
  name: string;
  party: "D" | "R" | "I";
  share: number;
}) {
  const tone =
    party === "R"
      ? "text-[var(--rep)]"
      : party === "I"
        ? "text-[#5a6a3a]"
        : "text-[var(--dem)]";
  const badge =
    party === "R"
      ? "bg-[var(--rep)]"
      : party === "I"
        ? "bg-[#7a8a4a]"
        : "bg-[var(--dem)]";
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-2 py-1 text-sm">
      <span className="inline-flex items-center gap-2 truncate text-[var(--ink)]">
        <span className="truncate">{name}</span>
        <span
          className={`inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold text-white ${badge}`}
        >
          {party}
        </span>
      </span>
      <span className={`font-medium tabular-nums ${tone}`}>
        {share.toFixed(1)}%
      </span>
    </div>
  );
}
