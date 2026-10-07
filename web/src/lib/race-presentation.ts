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
  presentation_schema: "race-presentation-v1";
};

function finite(value: unknown): number | null {
  if (typeof value !== "number" || Number.isNaN(value) || !Number.isFinite(value)) {
    return null;
  }
  return value;
}

export function presentRace(race: RaceForecast): RacePresentation {
  const structure = race.contest_structure ?? null;
  const modelingPath = String(
    (race as RaceForecast & { modeling_path?: string }).modeling_path ?? "ordinary_stack",
  );
  const candidateProbs = [...(race.candidate_probabilities ?? [])];
  const isRcv = structure === "ranked_choice_multiway" && candidateProbs.length > 0;
  const unsupported = Boolean(
    (race as RaceForecast & { probability_model_support_status?: string; win_probability_status?: string })
      .probability_model_support_status === "unsupported" ||
      (race as RaceForecast & { win_probability_status?: string }).win_probability_status ===
        "fail_closed",
  );

  let candidates: CandidatePresentation[] = [];
  if (candidateProbs.length) {
    candidates = [...candidateProbs]
      .sort((a, b) => b.p_win - a.p_win)
      .map((row) => ({
        candidate_id: row.candidate_id,
        candidate_name: row.candidate_name,
        ballot_party: row.ballot_party,
        caucus: row.caucus ?? null,
        p_win: finite(row.p_win),
        share_estimate: finite(
          row.final_support_estimate ?? row.first_choice_estimate ?? null,
        ),
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
        p_win: finite(race.p_modeled_candidate ?? race.p_dem ?? null),
        share_estimate: finite(race.modeled_candidate_share ?? race.dem_share ?? null),
      });
    }
    if (opposingName) {
      candidates.push({
        candidate_id: race.opposing_candidate_id ?? "",
        candidate_name: opposingName,
        ballot_party: String(race.opposing_ballot_party ?? "R"),
        caucus: "R",
        p_win: finite(race.p_opposing_candidate ?? race.p_rep ?? null),
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
    rating: race.rating ?? null,
    favored_candidate: favored?.candidate_name ?? race.favored_candidate ?? null,
    favored_candidate_id: favored?.candidate_id ?? null,
    favored_party: favored?.ballot_party || race.favored_party || null,
    favored_caucus: favored?.caucus ?? race.favored_caucus ?? null,
    favored_win_probability: unsupported ? null : (favored?.p_win ?? null),
    principal_opponent: opponent?.candidate_name ?? null,
    principal_opponent_id: opponent?.candidate_id ?? null,
    principal_opponent_party: opponent?.ballot_party ?? null,
    principal_opponent_probability: unsupported ? null : (opponent?.p_win ?? null),
    seat_control_p_dem_caucus: finite(race.p_dem_caucus ?? null),
    seat_control_p_rep_caucus: finite(race.p_rep_caucus ?? null),
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
    presentation_schema: "race-presentation-v1",
  };
}
