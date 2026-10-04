"""Sealed Alaska RCV evidence and candidate/poll identity contracts.

The ordinary Senate model is binary.  This module owns the separate Alaska
multi-candidate evidence boundary and never turns a pairwise poll into a
first-choice observation.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, ROOT

RAW_DIR = ROOT / "data" / "raw" / "external" / "alaska_rcv"
MANIFEST_PATH = MANIFESTS_DIR / "alaska_rcv_sources.json"
NORMALIZED_PATH = NORMALIZED_DIR / "alaska_rcv_2026.json"
PARSER_VERSION = "alaska-rcv-official-v1"
METHOD = "limited_validation_alaska_rcv_model-v1"
RACE_ID = "senate-2026-AK"

SOURCE_SPECS = {
    "2026_general_candidates.html": (
        "https://www.elections.alaska.gov/candidates/?election=26genr",
        "2823ad45bf7dfa66fe4516173204263e8421361464947a07bdaf7b515f683947",
    ),
    "2026_primary_summary.pdf": (
        "https://www.elections.alaska.gov/enr26/results/ElectionSummaryReportRPT.pdf",
        "5d97a98f682a9dfa66eabdf64965e4728feb744c7863682acfeacbeb7241dee9",
    ),
    "2026_primary_precinct.csv": (
        "https://www.elections.alaska.gov/enr26/results/GA_ENR_Precinct_State_of_Alaska.csv",
        "7dde3df0d57b1618143214224bf550ea55e295a6e9960b938e6e92047c1be765",
    ),
    "2022_general_us_sen_rcv.txt": (
        "https://elections.alaska.gov/results/22GENR/US%20SEN.txt",
        "01ea2bef334a3204fe42d7f0830598c1344dab04fb6f96e416f06202505f9962",
    ),
    "2022_general_us_house_rcv.txt": (
        "https://elections.alaska.gov/results/22GENR/US%20REP.txt",
        "7982b3e82741a304f222852936619e60eb0b1f87ed026cd05738eb3236437f11",
    ),
    "2022_special_us_house_rcv.txt": (
        "https://www.elections.alaska.gov/results/22SSPG/RcvDetailedReport.txt",
        "db5a886678aae4b91fcd93aea5f67e1c22585cdd7409757f366013cc13d56648",
    ),
    "2024_general_us_house_rcv.pdf": (
        "https://www.elections.alaska.gov/results/24GENR/RCV-USRep.pdf",
        "2880b88fb540f4b6327f686d19c9bd9636ff9c8a4c5301d31361405178ad7f5e",
    ),
}
SEALED_RETRIEVED_AT = {
    "2026_general_candidates.html": "2026-10-04T15:17:52.750093Z",
    "2026_primary_summary.pdf": "2026-10-04T15:17:53.043518Z",
    "2026_primary_precinct.csv": "2026-10-04T15:17:53.619650Z",
    "2022_general_us_sen_rcv.txt": "2026-10-04T15:17:53.911955Z",
    "2022_general_us_house_rcv.txt": "2026-10-04T15:17:54.074089Z",
    "2022_special_us_house_rcv.txt": "2026-10-04T15:17:54.243699Z",
    "2024_general_us_house_rcv.pdf": "2026-10-04T15:17:54.488954Z",
}

CANDIDATES = [
    {"candidate_id": f"{RACE_ID}:gerald-l-heikes", "name": "Gerald L. Heikes", "ballot_party": "R", "caucus": "R", "caucus_basis": "major_party_ballot_affiliation", "write_in": False},
    {"candidate_id": f"{RACE_ID}:mary-peltola", "name": "Mary Peltola", "ballot_party": "D", "caucus": "D", "caucus_basis": "major_party_ballot_affiliation", "write_in": False},
    {"candidate_id": f"{RACE_ID}:dan-s-sullivan", "name": "Dan S. Sullivan", "ballot_party": "R", "caucus": "R", "caucus_basis": "major_party_ballot_affiliation", "write_in": False, "incumbent": True, "poll_aliases": ["Dan Sullivan"]},
    {"candidate_id": f"{RACE_ID}:daniel-j-sullivan-jr", "name": "Daniel J. Sullivan Jr.", "ballot_party": "R", "caucus": "R", "caucus_basis": "major_party_ballot_affiliation", "write_in": False},
]
WRITE_INS = [
    {"candidate_id": f"{RACE_ID}:sidney-sid-hill", "name": "Sidney “Sid” Hill", "ballot_party": "I", "caucus": None, "write_in": True},
    {"candidate_id": f"{RACE_ID}:heather-mcelwain", "name": "Heather McElwain", "ballot_party": "L", "caucus": None, "write_in": True},
]

ANALOG_PARTIES = {
    "2022_general_senate": {"Chesbro, Patricia R.": "D", "Kelley, Buzz A.": "R", "Murkowski, Lisa": "R", "Tshibaka, Kelly C.": "R"},
    "2022_general_house": {"Begich, Nick": "R", "Bye, Chris": "I", "Palin, Sarah": "R", "Peltola, Mary S.": "D"},
    "2022_special_house": {"Begich, Nick": "R", "Palin, Sarah": "R", "Peltola, Mary S.": "D"},
    "2024_general_house": {"Begich, Nick": "R", "Hafner, Eric": "D", "Howe, John Wayne": "I", "Peltola, Mary S.": "D"},
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _int(value: Any) -> int:
    return int(str(value or "0").replace(",", "").replace('"', "").strip() or 0)


def _parse_txt_analog(path: Path, analog_id: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-16", newline="") as handle:
        raw_rows = list(csv.reader(handle, delimiter="\t"))
    header_index = next(index for index, row in enumerate(raw_rows) if "choiceName2" in row)
    header = raw_rows[header_index]
    positions = {name: header.index(name) for name in (
        "Textbox86", "choiceName2", "votes", "sourceChoiceName",
        "choiceName", "ballotsTransferred3",
    )}
    def value(row: list[str], name: str) -> str:
        position = positions[name]
        return row[position] if position < len(row) else ""
    rows = raw_rows[header_index + 1:]
    parties = ANALOG_PARTIES[analog_id]
    rounds: dict[int, dict[str, int]] = {}
    transfers: list[dict[str, Any]] = []
    for row in rows:
        round_label = str(value(row, "Textbox86") or "")
        choice = str(value(row, "choiceName2") or "").strip()
        if round_label.startswith("Round ") and choice in parties:
            rounds.setdefault(int(round_label.split()[-1]), {})[choice] = _int(value(row, "votes"))
        source = str(value(row, "sourceChoiceName") or "").strip()
        destination = str(value(row, "choiceName") or "").strip()
        ballots = _int(value(row, "ballotsTransferred3"))
        if source in parties and destination and destination != source and ballots > 0:
            transfers.append({"source": source, "source_party": parties[source], "destination": destination, "destination_party": parties.get(destination), "ballots": ballots, "exhausted": destination in {"Exhausted", "Overvotes"}})
    ordered = [{"round": number, "votes": rounds[number]} for number in sorted(rounds)]
    if len(ordered) < 2 or not transfers:
        raise ValueError(f"official RCV report did not parse: {path.name}")
    final = ordered[-1]["votes"]
    winner = max(final, key=final.get)
    return {"analog_id": analog_id, "office": "US_SENATE" if "senate" in analog_id else "US_HOUSE", "rounds": ordered, "transfers": transfers, "winner": winner, "candidate_parties": parties}


def _parse_2024_pdf(path: Path) -> dict[str, Any]:
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    required = ("Begich, Nick", "Hafner, Eric", "Howe, John Wayne", "Peltola, Mary S.", "159,777", "164,861", "156,985")
    if not all(token in text for token in required):
        raise ValueError("2024 official RCV PDF content changed")
    rounds = [
        {"round": 1, "votes": {"Begich, Nick": 159777, "Hafner, Eric": 3558, "Howe, John Wayne": 13210, "Peltola, Mary S.": 152948}},
        {"round": 2, "votes": {"Begich, Nick": 160044, "Hafner, Eric": 0, "Howe, John Wayne": 13871, "Peltola, Mary S.": 154261}},
        {"round": 3, "votes": {"Begich, Nick": 164861, "Hafner, Eric": 0, "Howe, John Wayne": 0, "Peltola, Mary S.": 156985}},
    ]
    transfers = [
        {"source": "Hafner, Eric", "source_party": "D", "destination": "Begich, Nick", "destination_party": "R", "ballots": 267, "exhausted": False},
        {"source": "Hafner, Eric", "source_party": "D", "destination": "Howe, John Wayne", "destination_party": "I", "ballots": 661, "exhausted": False},
        {"source": "Hafner, Eric", "source_party": "D", "destination": "Peltola, Mary S.", "destination_party": "D", "ballots": 1313, "exhausted": False},
        {"source": "Hafner, Eric", "source_party": "D", "destination": "Exhausted", "destination_party": None, "ballots": 1317, "exhausted": True},
        {"source": "Howe, John Wayne", "source_party": "I", "destination": "Begich, Nick", "destination_party": "R", "ballots": 4817, "exhausted": False},
        {"source": "Howe, John Wayne", "source_party": "I", "destination": "Peltola, Mary S.", "destination_party": "D", "ballots": 2724, "exhausted": False},
        {"source": "Howe, John Wayne", "source_party": "I", "destination": "Exhausted", "destination_party": None, "ballots": 6330, "exhausted": True},
    ]
    return {"analog_id": "2024_general_house", "office": "US_HOUSE", "rounds": rounds, "transfers": transfers, "winner": "Begich, Nick", "candidate_parties": ANALOG_PARTIES["2024_general_house"]}


def prepare_alaska_rcv_evidence(*, retrieved_at: str | None = None) -> dict[str, Any]:
    receipts: dict[str, Any] = {}
    for name, (url, expected) in SOURCE_SPECS.items():
        path = RAW_DIR / name
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = _sha(path)
        if actual != expected:
            raise ValueError(f"sealed Alaska source hash changed: {name}")
        timestamp = retrieved_at or SEALED_RETRIEVED_AT[name]
        receipts[name] = {"path": path.relative_to(ROOT).as_posix(), "source_url": url, "sha256": actual, "retrieved_at": timestamp}

    html = (RAW_DIR / "2026_general_candidates.html").read_text(encoding="utf-8")
    for token in ("Heikes, Gerald L.", "Peltola, Mary", "Sullivan, Dan S.", "Sullivan, Daniel J. Jr.", "Hill, Sidney", "McElwain, Heather"):
        if token not in html:
            raise ValueError(f"official 2026 candidate page lacks {token}")

    primary = pd.read_csv(RAW_DIR / "2026_primary_precinct.csv", low_memory=False)
    senate = primary[primary["Contest_title"].astype(str).eq("U.S. Senator")]
    totals = senate.groupby("candidate_name", dropna=False)["total_votes"].sum().astype(int)
    expected_total = 166004
    if int(totals.sum()) != expected_total:
        raise ValueError("official primary candidate total changed")
    primary_alias = {"Gerald L. Heikes": "Heikes, Gerald L.", "Mary Peltola": "Peltola, Mary", "Dan S. Sullivan": "Sullivan, Dan S.", "Daniel J. Sullivan Jr.": "Sullivan, Daniel J. Jr."}
    primary_rows = []
    selected = 0
    for candidate in CANDIDATES:
        votes = int(totals[primary_alias[candidate["name"]]])
        selected += votes
        primary_rows.append({"candidate_id": candidate["candidate_id"], "candidate_name": candidate["name"], "votes": votes, "share": votes / expected_total})
    write_in_anchor_votes = int(totals["Hill, Sidney \"Sid\""] + totals["McElwain, Heather"])
    primary_rows.append({"candidate_id": f"{RACE_ID}:write-in-aggregate", "candidate_name": "Certified write-in aggregate anchor", "votes": write_in_anchor_votes, "share": write_in_anchor_votes / expected_total})
    primary_rows.append({"candidate_id": f"{RACE_ID}:other-primary-field", "candidate_name": "Other defeated primary candidates", "votes": expected_total - selected - write_in_anchor_votes, "share": (expected_total - selected - write_in_anchor_votes) / expected_total})

    analogs = [
        _parse_txt_analog(RAW_DIR / "2022_general_us_sen_rcv.txt", "2022_general_senate"),
        _parse_txt_analog(RAW_DIR / "2022_general_us_house_rcv.txt", "2022_general_house"),
        _parse_txt_analog(RAW_DIR / "2022_special_us_house_rcv.txt", "2022_special_house"),
        _parse_2024_pdf(RAW_DIR / "2024_general_us_house_rcv.pdf"),
    ]
    payload = {
        "schema_version": "alaska-rcv-evidence-v1", "parser_version": PARSER_VERSION,
        "race_id": RACE_ID, "election_day": "2026-11-03", "primary_date": "2026-08-18",
        "primary_certified_report_generated_at": "2026-08-31T12:43:34-08:00",
        "candidate_field": CANDIDATES, "certified_write_ins": WRITE_INS,
        "write_in_treatment": "aggregate_residual_with_unknown_caucus_fail_closed_if_winner",
        "primary_total_votes": expected_total, "primary_results": primary_rows,
        "historical_analogs": analogs,
        "poll_measurement_contract": (
            "normalized_relative_preference_peltola_vs_incumbent_sullivan_only; "
            "binary_and_multiway_question_projections; never first_choice"
        ),
    }
    payload["semantic_sha256"] = _canonical_sha(payload)
    NORMALIZED_PATH.parent.mkdir(parents=True, exist_ok=True)
    # write_bytes + explicit LF so Windows CI/dev do not seal CRLF receipts that
    # break Linux Actions checkouts under Git text normalization.
    NORMALIZED_PATH.write_bytes((json.dumps(payload, indent=2) + "\n").encode("utf-8"))
    manifest = {
        "schema_version": "alaska-rcv-source-manifest-v1", "parser_version": PARSER_VERSION,
        "source_provider": "Alaska Division of Elections", "sources": receipts,
        "normalized_path": NORMALIZED_PATH.relative_to(ROOT).as_posix(),
        "normalized_sha256": _sha(NORMALIZED_PATH), "normalized_semantic_sha256": payload["semantic_sha256"],
        "production_eligible": True, "validation_class": "limited_validation_alaska_rcv_model",
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    return payload


def load_alaska_rcv_evidence() -> dict[str, Any]:
    if not NORMALIZED_PATH.is_file() or not MANIFEST_PATH.is_file():
        raise FileNotFoundError("sealed Alaska RCV evidence is missing")
    payload = json.loads(NORMALIZED_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    semantic = dict(payload)
    stored = semantic.pop("semantic_sha256", None)
    if _canonical_sha(semantic) != stored or stored != manifest.get("normalized_semantic_sha256"):
        raise ValueError("Alaska normalized evidence semantic hash changed")
    # Byte hashes are not authoritative across Git text/EOL normalization. The
    # semantic digest already seals every JSON field.
    return payload


def classify_pairwise_polls(polls: pd.DataFrame) -> pd.DataFrame:
    """Retain only exact Peltola/incumbent-Sullivan rows and label estimand."""
    if polls.empty:
        return polls.copy()
    out = polls[polls.get("race_id", pd.Series("", index=polls.index)).astype(str).eq(RACE_ID)].copy()
    modeled = out.get("modeled_candidate_name", pd.Series("", index=out.index)).fillna("").astype(str).str.casefold().str.strip()
    opposing = out.get("opposing_candidate_name", pd.Series("", index=out.index)).fillna("").astype(str).str.casefold().str.strip()
    out = out[modeled.eq("mary peltola") & opposing.eq("dan sullivan")].copy()
    # A source question that included additional candidates remains a
    # multi-candidate measurement even when the warehouse stores its Peltola /
    # incumbent-Sullivan projection on a normalized two-candidate scale.  Both
    # forms constrain only their relative preference; neither is first-choice
    # evidence for the RCV field.
    source_multiway = out.get("multiway", pd.Series(False, index=out.index)).map(
        lambda value: False if pd.isna(value) else bool(value)
    )
    out["source_question_multiway"] = source_multiway
    out["measurement_type"] = np.where(
        source_multiway,
        "multiway_question_normalized_pairwise_projection",
        "binary_pairwise_normalized",
    )
    out["pairwise_candidate_a_id"] = f"{RACE_ID}:mary-peltola"
    out["pairwise_candidate_b_id"] = f"{RACE_ID}:dan-s-sullivan"
    out["is_first_choice_measurement"] = False
    return out
