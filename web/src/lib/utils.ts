import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function pct(p: number, digits = 0): string {
  return `${(p * 100).toFixed(digits)}%`;
}

export function signedMargin(m: number): string {
  const abs = Math.abs(m).toFixed(1);
  if (m > 0.05) return `D+${abs}`;
  if (m < -0.05) return `R+${abs}`;
  return "EVEN";
}

export type RaceForecast = {
  race_id: string;
  state: string;
  p_dem: number;
  p_rep: number;
  mean_margin: number;
  sd_margin: number;
  ci05: number;
  ci95: number;
  prior_lean: number | null;
  incumbent_party: string | null;
  is_open: boolean | null;
};

export type ChamberForecast = {
  held_dem: number;
  held_rep: number;
  majority_threshold: number;
  p_dem_majority: number;
  p_rep_majority: number;
  p_tie: number;
  expected_dem_seats: number;
  seat_histogram: { dem_seats: number; count: number; probability: number }[];
  independent_bernoulli_foil_expected?: number;
  note?: string;
};

export type ForecastArtifact = {
  run_id: string;
  generated_at: string;
  forecast_as_of: string;
  election_id: string;
  model_version: string;
  method: string;
  chamber: ChamberForecast;
  races: RaceForecast[];
  diagnostics?: Record<string, unknown>;
};
