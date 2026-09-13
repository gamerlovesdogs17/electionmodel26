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
  seat_class?: string | null;
  p_dem: number;
  p_rep: number;
  mean_margin: number | null;
  sd_margin: number | null;
  ci05: number | null;
  ci95: number | null;
  prior_lean: number | null;
  incumbent_party: string | null;
  is_open: boolean | null;
  held_by?: string | null;
  dem_candidate?: string;
  rep_candidate?: string;
  dem_party?: "D" | "I" | string;
  caucus?: string;
  dem_share?: number;
  rep_share?: number;
  rating?: string;
  expert_rating?: string;
  is_flip?: boolean;
  favored_party?: string;
  favored_caucus?: string;
  market_p_dem?: number;
};

export type ChamberForecast = {
  held_dem: number;
  held_rep: number;
  held_ind?: number;
  majority_threshold: number;
  p_dem_majority: number;
  p_rep_majority: number;
  p_tie: number;
  p_fifty_fifty?: number;
  vp_tiebreak_party?: string;
  expected_dem_seats: number;
  expected_rep_seats?: number;
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
  warnings?: { layer?: string; error?: string }[];
  chamber: ChamberForecast;
  races: RaceForecast[];
  diagnostics?: Record<string, unknown>;
  overlays?: Record<string, unknown>;
  ablation?: {
    unadjusted: {
      p_dem_majority: number;
      p_rep_majority: number;
      expected_dem_seats: number;
    };
    adjusted: {
      p_dem_majority: number;
      p_rep_majority: number;
      expected_dem_seats: number;
    };
    delta_p_dem_majority?: number;
  };
  snapshot?: Record<string, unknown>;
  scenarios?: Record<string, unknown>;
  auxiliary?: Record<string, unknown>;
  peer_comparison?: {
    as_of_peers?: string;
    disclaimer?: string;
    control_p_dem?: Record<string, number | null | undefined>;
    races?: Record<string, unknown>[];
    sources?: Record<string, { label?: string; url?: string; note?: string }>;
  };
};

/** Census FIPS → postal abbreviation for us-atlas states-10m */
export const FIPS_TO_STATE: Record<string, string> = {
  "01": "AL",
  "02": "AK",
  "04": "AZ",
  "05": "AR",
  "06": "CA",
  "08": "CO",
  "09": "CT",
  "10": "DE",
  "11": "DC",
  "12": "FL",
  "13": "GA",
  "15": "HI",
  "16": "ID",
  "17": "IL",
  "18": "IN",
  "19": "IA",
  "20": "KS",
  "21": "KY",
  "22": "LA",
  "23": "ME",
  "24": "MD",
  "25": "MA",
  "26": "MI",
  "27": "MN",
  "28": "MS",
  "29": "MO",
  "30": "MT",
  "31": "NE",
  "32": "NV",
  "33": "NH",
  "34": "NJ",
  "35": "NM",
  "36": "NY",
  "37": "NC",
  "38": "ND",
  "39": "OH",
  "40": "OK",
  "41": "OR",
  "42": "PA",
  "44": "RI",
  "45": "SC",
  "46": "SD",
  "47": "TN",
  "48": "TX",
  "49": "UT",
  "50": "VT",
  "51": "VA",
  "53": "WA",
  "54": "WV",
  "55": "WI",
  "56": "WY",
};

export const STATE_NAME: Record<string, string> = {
  AL: "Alabama",
  AK: "Alaska",
  AZ: "Arizona",
  AR: "Arkansas",
  CA: "California",
  CO: "Colorado",
  CT: "Connecticut",
  DE: "Delaware",
  FL: "Florida",
  GA: "Georgia",
  HI: "Hawaii",
  ID: "Idaho",
  IL: "Illinois",
  IN: "Indiana",
  IA: "Iowa",
  KS: "Kansas",
  KY: "Kentucky",
  LA: "Louisiana",
  ME: "Maine",
  MD: "Maryland",
  MA: "Massachusetts",
  MI: "Michigan",
  MN: "Minnesota",
  MS: "Mississippi",
  MO: "Missouri",
  MT: "Montana",
  NE: "Nebraska",
  NV: "Nevada",
  NH: "New Hampshire",
  NJ: "New Jersey",
  NM: "New Mexico",
  NY: "New York",
  NC: "North Carolina",
  ND: "North Dakota",
  OH: "Ohio",
  OK: "Oklahoma",
  OR: "Oregon",
  PA: "Pennsylvania",
  RI: "Rhode Island",
  SC: "South Carolina",
  SD: "South Dakota",
  TN: "Tennessee",
  TX: "Texas",
  UT: "Utah",
  VT: "Vermont",
  VA: "Virginia",
  WA: "Washington",
  WV: "West Virginia",
  WI: "Wisconsin",
  WY: "Wyoming",
};

/** Color bands aligned with rating_from_probability thresholds (incl. Tilt). */
export function demFill(p: number): string {
  if (p >= 0.92) return "#143f6b"; // Solid D
  if (p >= 0.78) return "#1f5f8b"; // Likely D
  if (p >= 0.62) return "#3d7ea8"; // Lean D
  if (p >= 0.55) return "#6a9bb8"; // Tilt D
  if (p >= 0.45) return "#9a8f4a"; // Tossup
  if (p >= 0.38) return "#d48a7a"; // Tilt R
  if (p >= 0.22) return "#c46a5c"; // Lean R
  if (p >= 0.08) return "#a33b2d"; // Likely R
  return "#7a2418"; // Solid R
}

export function marginFill(m: number): string {
  if (m >= 10) return "#143f6b";
  if (m >= 4) return "#1f5f8b";
  if (m >= 2) return "#3d7ea8";
  if (m >= 0.5) return "#6a9bb8";
  if (m > -0.5) return "#8a9096";
  if (m > -2) return "#d48a7a";
  if (m > -4) return "#c46a5c";
  if (m > -10) return "#a33b2d";
  return "#7a2418";
}

export function ratingFromProbability(p: number): string {
  if (p >= 0.92) return "Solid D";
  if (p >= 0.78) return "Likely D";
  if (p >= 0.62) return "Lean D";
  if (p >= 0.55) return "Tilt D";
  if (p >= 0.45) return "Tossup";
  if (p >= 0.38) return "Tilt R";
  if (p >= 0.22) return "Lean R";
  if (p >= 0.08) return "Likely R";
  return "Solid R";
}

export function primaryRace(races: RaceForecast[]): RaceForecast | null {
  if (!races.length) return null;
  return [...races].sort(
    (a, b) => Math.abs(a.p_dem - 0.5) - Math.abs(b.p_dem - 0.5),
  )[0];
}

export function ratingFill(rating: string): string {
  switch (rating) {
    case "Solid D":
      return "#143f6b";
    case "Likely D":
      return "#1f5f8b";
    case "Lean D":
      return "#3d7ea8";
    case "Tilt D":
      return "#6a9bb8";
    case "Tossup":
      return "#9a8f4a";
    case "Tilt R":
      return "#d48a7a";
    case "Lean R":
      return "#c46a5c";
    case "Likely R":
      return "#a33b2d";
    case "Solid R":
      return "#7a2418";
    default:
      return "#b0b8be";
  }
}
