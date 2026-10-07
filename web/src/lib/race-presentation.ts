import type { RaceForecast } from "@/lib/utils";

export type CandidatePresentation = {
  candidate_id: string;
  candidate_name: string;
  ballot_party: string;
  caucus: string | null;
  p_win: number | null;
  share_estimate: number | null;
};

export type RacePresentation = {
  race_id: string;
  state: string;
  modeling_path: string;
  contest_structure: string | null;
  rating: string | null;
  favored_candidate: string | null;
  favored_candidate_id: string | null;
  favored_party: string | null;
  favored_caucus: string | null;
  favored_win_probability: number | null;
  principal_opponent: string | null;
  principal_opponent_id: string | null;
  principal_opponent_party: string | null;
  principal_opponent_probability: number | null;
  seat_control_p_dem_caucus: number | null;
  seat_control_p_rep_caucus: number | null;
  candidates: CandidatePresentation[];
  rcv_detail: {
    exhausted_ballot_share: number | null;
    candidate_probabilities: CandidatePresentation[];
    authoritative_binary_aliases: boolean;
    note: string;
  } | null;
  unsupported_probability: boolean;
  unsupported_message: string | null;
  presentation_schema: "race-presentation-v1";
};

type ExtendedRace = RaceForecast & {
  modeling_path?: string;
  probability_model_support_status?: string;
  win_probability_status?: string;
  ballot_candidates?: Array<{
    candidate_id?: string;
    candidate_name?: string;
    ballot_party?: string;
    caucus?: string | null;
  }>;
};

function finite(value: unknown): number | null {
  if (typeof value !== "number" || Number.isNaN(value) || !Number.isFinite(value)) {
    return null;
  }
  return value;
}

export function presentRace(race: RaceForecast): RacePresentation {
  const ext = race as ExtendedRace;
  const structure = race.contest_structure ?? null;
  const modelingPath = String(ext.modeling_path ?? "ordinary_stack");
  const candidateProbs = [...(race.candidate_probabilities ?? [])];
  const isRcv = structure === "ranked_choice_multiway" && candidateProbs.length > 0;
  const isMultiway = structure === "multiway_plurality";
  const unsupported = Boolean(
    ext.probability_model_support_status === "unsupported" ||
      ext.win_probability_status === "fail_closed" ||
      (isMultiway &&
        race.p_modeled_candidate == null &&
        candidateProbs.every((row) => row.p_win == null)),
  );
  const unsupportedMessage = unsupported
    ? isMultiway
      ? "Probability withheld — multiway model not sufficiently validated"
      : "Probability withheld — limited-validation / unsupported path"
    : null;

  let candidates: CandidatePresentation[] = [];
  if (candidateProbs.length) {
    candidates = [...candidateProbs]
      .sort((a, b) => (b.p_win ?? -1) - (a.p_win ?? -1) || a.candidate_id.localeCompare(b.candidate_id))
      .map((row) => ({
        candidate_id: row.candidate_id,
        candidate_name: row.candidate_name,
        ballot_party: row.ballot_party,
        caucus: row.caucus ?? null,
        p_win: unsupported ? null : finite(row.p_win),
        share_estimate: finite(
          row.final_support_estimate ?? row.first_choice_estimate ?? null,
        ),
      }));
  } else if ((ext.ballot_candidates?.length ?? 0) > 0 && (isMultiway || unsupported)) {
    candidates = (ext.ballot_candidates ?? []).map((row) => ({
      candidate_id: row.candidate_id ?? "",
      candidate_name: row.candidate_name ?? "",
      ballot_party: String(row.ballot_party ?? ""),
      caucus: row.caucus ?? null,
      p_win: null,
      share_estimate: null,
    }));
  } else {
    const modeledName = race.modeled_candidate ?? race.dem_candidate;
    const opposingName = race.opposing_candidate ?? race.rep_candidate;
    if (modeledName) {
      candidates.push({
        candidate_id: race.modeled_candidate_id ?? "",
        candidate_name: modeledName,
        ballot_party: String(race.modeled_ballot_party ?? race.dem_party ?? "D"),
        caucus: null,
        p_win: unsupported
          ? null
          : finite(race.p_modeled_candidate ?? race.p_dem ?? null),
        share_estimate: finite(race.modeled_candidate_share ?? race.dem_share ?? null),
      });
    }
    if (opposingName) {
      candidates.push({
        candidate_id: race.opposing_candidate_id ?? "",
        candidate_name: opposingName,
        ballot_party: String(race.opposing_ballot_party ?? "R"),
        caucus: "R",
        p_win: unsupported
          ? null
          : finite(race.p_opposing_candidate ?? race.p_rep ?? null),
        share_estimate: finite(race.opposing_candidate_share ?? race.rep_share ?? null),
      });
    }
  }

  const ranked = [...candidates].sort(
    (a, b) => (b.p_win ?? -1) - (a.p_win ?? -1) || a.candidate_id.localeCompare(b.candidate_id),
  );
  const favored = ranked[0] ?? null;
  const opponent = ranked[1] ?? null;

  return {
    race_id: race.race_id,
    state: race.state,
    modeling_path: modelingPath,
    contest_structure: structure,
    rating: unsupported && isMultiway ? null : (race.rating ?? null),
    favored_candidate: favored?.candidate_name ?? race.favored_candidate ?? null,
    favored_candidate_id: favored?.candidate_id ?? null,
    favored_party: favored?.ballot_party || race.favored_party || null,
    favored_caucus: favored?.caucus ?? race.favored_caucus ?? null,
    favored_win_probability: unsupported ? null : (favored?.p_win ?? null),
    principal_opponent: opponent?.candidate_name ?? null,
    principal_opponent_id: opponent?.candidate_id ?? null,
    principal_opponent_party: opponent?.ballot_party ?? null,
    principal_opponent_probability: unsupported ? null : (opponent?.p_win ?? null),
    seat_control_p_dem_caucus: unsupported && isMultiway ? null : finite(race.p_dem_caucus ?? null),
    seat_control_p_rep_caucus: unsupported && isMultiway ? null : finite(race.p_rep_caucus ?? null),
    candidates,
    rcv_detail: isRcv
      ? {
          exhausted_ballot_share: finite(race.exhausted_ballot_share ?? null),
          candidate_probabilities: candidates,
          authoritative_binary_aliases: Boolean(race.authoritative_binary_aliases),
          note: "Authoritative Alaska result remains candidate-level RCV.",
        }
      : null,
    unsupported_probability: unsupported,
    unsupported_message: unsupportedMessage,
    presentation_schema: "race-presentation-v1",
  };
}
