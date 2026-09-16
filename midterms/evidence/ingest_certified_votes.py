"""Ingest certified U.S. Senate popular-vote totals from Wikipedia.

Replaces margin-scaled 1e6 synthetic ledger rows (audit A-02) with exact
integers scraped from state election-result tables.

Run: python -m midterms.evidence.ingest_certified_votes
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from midterms.config import RAW_DIR
from midterms.evidence.build_official_ledger import CLASS_UP, MARGINS, SPECIAL_META
from midterms.evidence.official_ballot import CLASS_I, CLASS_II, CLASS_III
from midterms.evidence.wiki_ratings import STATE_NAME_TO_ABBR

PARSER_VERSION = "certified-votes-v1"
USER_AGENT = "midterms-senate-model/0.9.20 (research; certified Senate vote ingest)"
OUT_PATH = RAW_DIR / "external" / "certified_vote_counts.json"

STATE_ABBR_TO_NAME = {v: k for k, v in STATE_NAME_TO_ABBR.items()}
# Wikipedia article titles use "Washington" / "Georgia", not postal codes.
WIKI_STATE_SLUG = {
    abbr: name.replace(" ", "_") for abbr, name in STATE_ABBR_TO_NAME.items()
}

CLASS_STATES = {"I": CLASS_I, "II": CLASS_II, "III": CLASS_III}

# Known multiway / nonstandard formats (do not invent fake D–R splits).
FORMAT_HINTS: dict[str, str] = {
    "senate-2016-CA": "top_two",
    "senate-2018-CA": "top_two",
    "senate-2022-CA": "top_two",
    "senate-2024-CA": "top_two",
    "senate-2024-CA-unexpired": "top_two",
    "senate-2022-AK": "rcv",
    "senate-2024-NE": "independent_challenger",
    "senate-2014-LA": "jungle_primary",
    "senate-2016-LA": "jungle_primary",
    "senate-2020-LA": "jungle_primary",
    "senate-2022-LA": "jungle_primary",
}

# Explicit URL overrides when the default title does not exist or hosts two races.
URL_OVERRIDES: dict[str, str] = {
    "senate-2024-CA": "https://en.wikipedia.org/wiki/2024_United_States_Senate_elections_in_California",
    "senate-2024-CA-unexpired": "https://en.wikipedia.org/wiki/2024_United_States_Senate_elections_in_California",
    "senate-2024-NE-unexpired": "https://en.wikipedia.org/wiki/2024_United_States_Senate_special_election_in_Nebraska",
}

# When one page hosts two generals, prefer the table whose top vote-getter matches.
CANDIDATE_HINTS: dict[str, tuple[str, ...]] = {
    "senate-2024-CA": ("Adam Schiff", "Steve Garvey"),
    "senate-2024-CA-unexpired": ("Adam Schiff", "Steve Garvey"),
}


def _class_states(label: str) -> list[str]:
    return list(CLASS_STATES[label])


def expected_race_specs() -> list[dict[str, Any]]:
    """Mirror build_official_ledger race_id universe (regular + specials/unexpired)."""
    specs: list[dict[str, Any]] = []
    for year in sorted(MARGINS):
        up = CLASS_UP[year]
        for st in _class_states(up):
            specs.append(
                {
                    "race_id": f"senate-{year}-{st}",
                    "year": year,
                    "state": st,
                    "kind": "regular",
                    "term_type": "full",
                    "suffix": None,
                }
            )
        for sp in SPECIAL_META.get(year) or []:
            key = sp["key"]
            specs.append(
                {
                    "race_id": f"senate-{year}-{key}",
                    "year": year,
                    "state": sp["state"],
                    "kind": sp.get("kind") or "special",
                    "term_type": sp.get("term_type")
                    or ("unexpired" if "unexpired" in key else "full"),
                    "suffix": key,
                }
            )
    return specs


def wiki_url_for(spec: dict[str, Any]) -> str:
    rid = spec["race_id"]
    if rid in URL_OVERRIDES:
        return URL_OVERRIDES[rid]
    year = int(spec["year"])
    state = spec["state"]
    slug = WIKI_STATE_SLUG[state]
    special = (
        spec["kind"] == "special"
        or (spec.get("suffix") or "").endswith("-special")
        or "special" in (spec.get("suffix") or "")
    )
    # Unexpired NE/CA use overrides; remaining specials use "special_election".
    if special and not str(spec.get("suffix") or "").endswith("-unexpired"):
        title = f"{year}_United_States_Senate_special_election_in_{slug}"
    else:
        title = f"{year}_United_States_Senate_election_in_{slug}"
    return f"https://en.wikipedia.org/wiki/{title}"


def _fetch(url: str, *, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=90) as resp:
                return resp.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    assert last is not None
    raise last


def _flatten_cols(df: pd.DataFrame) -> list[str]:
    cols: list[str] = []
    for c in df.columns.tolist():
        if isinstance(c, tuple):
            parts = [str(x) for x in c if str(x) not in {"", "nan", "None"}]
            cols.append(" ".join(parts))
        else:
            cols.append(str(c))
    return cols


def _norm_header(s: str) -> str:
    s = re.sub(r"\s+", " ", str(s)).strip().lower()
    s = s.replace("±%", "%").replace("�%", "%")
    return s


def _parse_votes(val: Any) -> int | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val)
    s = s.replace(",", "").replace("\xa0", "").strip()
    if not s or s.lower() in {"nan", "none", "—", "-", "n/a"}:
        return None
    # Drop footnote residue / percent rows misaligned into votes.
    if "%" in s and not re.search(r"\d", s.replace("%", "")):
        return None
    # Keep the longest digit run (avoids footnote crumbs like "2;").
    nums = re.findall(r"\d+", s)
    if not nums:
        return None
    try:
        return int(max(nums, key=len))
    except ValueError:
        return None


def _safe_read_html(html: bytes) -> list[pd.DataFrame]:
    """Parse tables one-by-one so a single malformed wikitable cannot abort the page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    out: list[pd.DataFrame] = []
    for table in soup.find_all("table"):
        try:
            dfs = pd.read_html(io.StringIO(str(table)))
        except (ValueError, TypeError, IndexError):
            continue
        out.extend(dfs)
    if out:
        return out
    # Fallback: whole-document parse
    try:
        return pd.read_html(io.BytesIO(html))
    except ValueError:
        return []


def _clean_name(raw: Any) -> str:
    s = str(raw or "")
    s = re.sub(r"\(incumbent\)", "", s, flags=re.I)
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _party_bucket(party: str) -> str:
    p = party.lower().strip()
    if not p or p in {"nan", "none"}:
        return "other"
    if "democratic" in p or p in {"d", "dfl", "dem"}:
        return "D"
    if "republican" in p or p in {"r", "gop", "rep"}:
        return "R"
    if "total" in p or "hold" in p or "gain" in p or "majority" in p:
        return "skip"
    return "other"


def _is_result_table(df: pd.DataFrame) -> bool:
    cols = [_norm_header(c) for c in _flatten_cols(df)]
    joined = " ".join(cols)
    if "candidate" not in joined:
        return False
    if "vote" not in joined and "%" not in joined:
        return False
    # Campaign-finance tables
    if "receipt" in joined or "cash on hand" in joined or "raised" in joined:
        return False
    if "county" in joined and "margin" in joined:
        return False
    return True


def _locate_columns(df: pd.DataFrame) -> tuple[int | None, int | None, int | None]:
    cols = [_norm_header(c) for c in _flatten_cols(df)]
    party_i = cand_i = votes_i = None
    for i, c in enumerate(cols):
        if party_i is None and c.startswith("party") and "party.1" not in c:
            # Prefer Party.1 (text) when both Party / Party.1 exist
            pass
        if "party.1" in c or c == "party":
            # later party.1 wins if present
            if "party.1" in c:
                party_i = i
            elif party_i is None:
                party_i = i
        if cand_i is None and "candidate" in c:
            cand_i = i
        if votes_i is None and (c == "votes" or c.startswith("votes ") or c == "#"):
            votes_i = i
    # Wikipedia often: Party | Party.1 | Candidate | Votes
    flat = _flatten_cols(df)
    for i, c in enumerate(flat):
        if str(c).lower() in {"party.1"}:
            party_i = i
    return party_i, cand_i, votes_i


def extract_candidate_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    party_i, cand_i, votes_i = _locate_columns(df)
    if cand_i is None or votes_i is None:
        return []
    rows: list[dict[str, Any]] = []
    for _, ser in df.iterrows():
        vals = ser.tolist()
        cand = _clean_name(vals[cand_i] if cand_i < len(vals) else "")
        if not cand or cand.lower() in {"total votes", "total", "nan"}:
            continue
        if "hold" in cand.lower() or "gain" in cand.lower():
            continue
        party_raw = ""
        if party_i is not None and party_i < len(vals):
            party_raw = str(vals[party_i] if not pd.isna(vals[party_i]) else "")
        # Sometimes party color column is empty and Party.1 holds the label —
        # already preferred. If still empty, try adjacent.
        if not party_raw or party_raw.lower() == "nan":
            for j in range(len(vals)):
                if j == cand_i or j == votes_i:
                    continue
                cell = str(vals[j] if not pd.isna(vals[j]) else "")
                if _party_bucket(cell) in {"D", "R", "other"} and "total" not in cell.lower():
                    if any(
                        k in cell.lower()
                        for k in (
                            "democrat",
                            "republican",
                            "independent",
                            "libertarian",
                            "green",
                            "write",
                            "alliance",
                            "constitution",
                            "legal marijuana",
                            "progressive",
                            "no party",
                        )
                    ):
                        party_raw = cell
                        break
        votes = _parse_votes(vals[votes_i] if votes_i < len(vals) else None)
        if votes is None or votes < 0:
            continue
        bucket = _party_bucket(party_raw)
        if bucket == "skip":
            continue
        if cand.lower() == "write-in" or "write-in" in cand.lower():
            bucket = "other"
            party_raw = party_raw or "Write-in"
        rows.append(
            {
                "name": cand,
                "party": party_raw.strip() or "Unknown",
                "party_bucket": bucket,
                "votes": int(votes),
            }
        )
    return rows


def score_general_table(rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Return (total_votes, n_candidates, n_parties) for ranking."""
    if len(rows) < 2:
        return (0, 0, 0)
    total = sum(r["votes"] for r in rows)
    parties = {r["party_bucket"] for r in rows}
    return (total, len(rows), len(parties))


def _extract_rcv_rows(df: pd.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Parse Wikipedia RCV transfer tables → (first_choice_rows, final_round_rows)."""
    cols = _flatten_cols(df)
    norms = [_norm_header(c) for c in cols]
    if not any("first choice" in c and "vote" in c for c in norms):
        return None
    party_i = cand_i = first_i = None
    round_vote_cols: list[tuple[int, int]] = []  # (round_num, col_index)
    for i, c in enumerate(norms):
        if party_i is None and ("party.1" in c or c.endswith("party")):
            if "party.1" in c or c == "party party.1":
                party_i = i
            elif party_i is None and "party" in c:
                party_i = i
        if cand_i is None and "candidate" in c:
            cand_i = i
        if first_i is None and "first choice" in c and "vote" in c:
            first_i = i
        m = re.search(r"round\s+(\d+)\s+votes?", c)
        if m:
            round_vote_cols.append((int(m.group(1)), i))
    if cand_i is None or first_i is None:
        return None
    # Prefer Party.1-like column
    flat = _flatten_cols(df)
    for i, c in enumerate(flat):
        if str(c).lower() in {"party.1", "party party.1"} or str(c).endswith("Party.1"):
            party_i = i

    first_rows: list[dict[str, Any]] = []
    for _, ser in df.iterrows():
        vals = ser.tolist()
        cand = _clean_name(vals[cand_i])
        if not cand or cand.lower() in {"total votes", "total", "blank or inactive ballots"}:
            continue
        if "hold" in cand.lower() or "gain" in cand.lower():
            continue
        party_raw = ""
        if party_i is not None:
            party_raw = str(vals[party_i] if not pd.isna(vals[party_i]) else "")
        votes = _parse_votes(vals[first_i])
        if votes is None:
            continue
        bucket = _party_bucket(party_raw)
        if bucket == "skip":
            continue
        first_rows.append(
            {
                "name": cand,
                "party": party_raw.strip() or "Unknown",
                "party_bucket": bucket,
                "votes": int(votes),
            }
        )
    if len(first_rows) < 2:
        return None

    final_rows: list[dict[str, Any]] = []
    if round_vote_cols:
        last_round = max(r for r, _ in round_vote_cols)
        last_i = next(i for r, i in round_vote_cols if r == last_round)
        for _, ser in df.iterrows():
            vals = ser.tolist()
            cand = _clean_name(vals[cand_i])
            if not cand or cand.lower().startswith("total") or "blank" in cand.lower():
                continue
            if "hold" in cand.lower():
                continue
            raw = vals[last_i]
            if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                continue
            if "eliminated" in str(raw).lower():
                continue
            votes = _parse_votes(raw)
            if votes is None:
                continue
            party_raw = ""
            if party_i is not None:
                party_raw = str(vals[party_i] if not pd.isna(vals[party_i]) else "")
            bucket = _party_bucket(party_raw)
            if bucket == "skip":
                continue
            final_rows.append(
                {
                    "name": cand,
                    "party": party_raw.strip() or "Unknown",
                    "party_bucket": bucket,
                    "votes": int(votes),
                }
            )
    return first_rows, final_rows


def pick_general_tables(
    tables: list[pd.DataFrame],
) -> list[tuple[int, list[dict[str, Any]], int]]:
    """All plausible general-election result tables, highest total first."""
    scored: list[tuple[int, list[dict[str, Any]], int]] = []
    for i, df in enumerate(tables):
        rcv = _extract_rcv_rows(df)
        if rcv is not None:
            first_rows, _final = rcv
            total, n_cand, n_party = score_general_table(first_rows)
            if total >= 1_000 and n_cand >= 2:
                scored.append((total, first_rows, i))
            continue
        if not _is_result_table(df):
            continue
        rows = extract_candidate_rows(df)
        total, n_cand, n_party = score_general_table(rows)
        if total < 1_000 or n_cand < 2:
            continue
        scored.append((total, rows, i))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _pick_rcv_from_tables(
    tables: list[pd.DataFrame],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None, str] | None:
    best: tuple[int, list[dict[str, Any]], list[dict[str, Any]], str] | None = None
    for df in tables:
        rcv = _extract_rcv_rows(df)
        if rcv is None:
            continue
        first_rows, final_rows = rcv
        total = sum(r["votes"] for r in first_rows)
        if best is None or total > best[0]:
            note = "Parsed RCV transfer table (first-choice aggregates; final round attached)."
            best = (total, first_rows, final_rows, note)
    if best is None:
        return None
    return best[1], best[2] or None, best[3]


def contest_format_for(
    race_id: str, rows: list[dict[str, Any]]
) -> str:
    if race_id in FORMAT_HINTS:
        return FORMAT_HINTS[race_id]
    parties = {r["party_bucket"] for r in rows}
    if parties == {"D"} or parties == {"R"}:
        return "same_party_general"
    if "D" not in parties or "R" not in parties:
        return "non_two_party"
    if len(rows) > 3:
        return "plurality_multi"
    return "plurality"


def aggregate_contest(
    *,
    race_id: str,
    state: str,
    rows: list[dict[str, Any]],
    source_url: str,
    notes: str = "",
    rcv_final: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    dem_rows = [r for r in rows if r["party_bucket"] == "D"]
    rep_rows = [r for r in rows if r["party_bucket"] == "R"]
    other_rows = [r for r in rows if r["party_bucket"] == "other"]
    dem_votes = sum(r["votes"] for r in dem_rows)
    rep_votes = sum(r["votes"] for r in rep_rows)
    other_votes = sum(r["votes"] for r in other_rows)

    dem_nominee = max(dem_rows, key=lambda r: r["votes"])["name"] if dem_rows else None
    rep_nominee = max(rep_rows, key=lambda r: r["votes"])["name"] if rep_rows else None

    fmt = contest_format_for(race_id, rows)
    note_bits = [notes] if notes else []
    if fmt in {"top_two", "same_party_general", "rcv", "independent_challenger", "non_two_party"}:
        note_bits.append(
            "Party aggregates are sums of listed candidates; not a synthetic two-party split."
        )
    if fmt == "rcv":
        note_bits.append(
            "Primary vote fields use first-choice totals; rcv_final holds last-round tallies."
        )

    out: dict[str, Any] = {
        "race_id": race_id,
        "state": state,
        "dem_votes": int(dem_votes),
        "rep_votes": int(rep_votes),
        "other_votes": int(other_votes),
        "dem_nominee": dem_nominee,
        "rep_nominee": rep_nominee,
        "source_url": source_url,
        "contest_format": fmt,
        "candidates": [
            {"name": r["name"], "party": r["party"], "votes": r["votes"]} for r in rows
        ],
        "notes": " ".join(x for x in note_bits if x).strip() or None,
    }
    if rcv_final:
        out["rcv_final"] = [
            {"name": r["name"], "party": r["party"], "votes": r["votes"]} for r in rcv_final
        ]
    return out


def _rows_match_hint(rows: list[dict[str, Any]], hints: tuple[str, ...]) -> bool:
    """True when hints cover the top vote-getters of a short general table."""
    if not rows or len(rows) > 6:
        return False
    top = sorted(rows, key=lambda r: r["votes"], reverse=True)[: len(hints)]
    top_names = [r["name"].lower() for r in top]
    return all(any(h.lower() in n for n in top_names) for h in hints)


def _ca2024_generals(
    scored: list[tuple[int, list[dict[str, Any]], int]],
) -> list[tuple[int, list[dict[str, Any]], int]]:
    """Schiff/Garvey top-two generals only (exclude jungle primaries)."""
    hint = CANDIDATE_HINTS["senate-2024-CA"]
    out = [s for s in scored if _rows_match_hint(s[1], hint) and len(s[1]) <= 4]
    return out


def parse_page_for_race(
    html: bytes,
    *,
    race_id: str,
    state: str,
    source_url: str,
    prefer_nth: int | None = None,
) -> dict[str, Any] | None:
    tables = _safe_read_html(html)
    scored = pick_general_tables(tables)
    if not scored and not (
        race_id in FORMAT_HINTS and FORMAT_HINTS[race_id] == "rcv"
    ):
        return None

    if race_id in FORMAT_HINTS and FORMAT_HINTS[race_id] == "rcv":
        picked = _pick_rcv_from_tables(tables)
        if picked is not None:
            first_rows, final_rows, note = picked
            return aggregate_contest(
                race_id=race_id,
                state=state,
                rows=first_rows,
                source_url=source_url,
                notes=note,
                rcv_final=final_rows,
            )
        if not scored:
            return None

    if not scored:
        return None

    chosen: list[dict[str, Any]] | None = None

    if race_id in {"senate-2024-CA", "senate-2024-CA-unexpired"}:
        matching = _ca2024_generals(scored)
        if not matching:
            return None
        idx = 0 if race_id == "senate-2024-CA" else 1
        if idx >= len(matching):
            return None
        chosen = matching[idx][1]
        note = (
            "Selected largest Schiff/Garvey general on dual-election page (full term)."
            if idx == 0
            else "Selected 2nd Schiff/Garvey general on dual-election page (unexpired)."
        )
    elif prefer_nth is not None and prefer_nth < len(scored):
        chosen = scored[prefer_nth][1]
        note = f"Selected ranked general table index {prefer_nth}."
    else:
        chosen = scored[0][1]
        note = "Selected largest Party/Candidate/Votes table."

    assert chosen is not None
    return aggregate_contest(
        race_id=race_id,
        state=state,
        rows=chosen,
        source_url=source_url,
        notes=note,
    )


def ingest_race(spec: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    url = wiki_url_for(spec)
    try:
        html = _fetch(url)
    except HTTPError as exc:
        if exc.code == 404:
            return None, f"HTTP 404 for {url}"
        return None, f"HTTP {exc.code} for {url}: {exc}"
    except Exception as exc:  # noqa: BLE001 — surface scrape failures
        return None, f"{type(exc).__name__} for {url}: {exc}"

    try:
        contest = parse_page_for_race(
            html,
            race_id=spec["race_id"],
            state=spec["state"],
            source_url=url,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"parse error for {url}: {type(exc).__name__}: {exc}"

    if contest is None:
        return None, f"no general-election vote table found at {url}"
    return contest, None


def _cycle_hash(contests: list[dict[str, Any]]) -> str:
    payload = json.dumps(contests, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_certified_ledger() -> dict[str, Any]:
    specs = expected_race_specs()
    cycles: dict[str, Any] = {}
    missing: list[str] = []
    failures: list[dict[str, str]] = []

    by_year: dict[int, list[dict[str, Any]]] = {}
    for year in sorted({s["year"] for s in specs}):
        by_year[year] = []

    # Cache HTML by URL so CA dual races share one fetch.
    html_cache: dict[str, bytes] = {}

    for i, spec in enumerate(specs):
        rid = spec["race_id"]
        url = wiki_url_for(spec)
        print(f"[{i + 1}/{len(specs)}] {rid} <- {url}", flush=True)
        try:
            if url not in html_cache:
                html_cache[url] = _fetch(url)
                time.sleep(0.35)  # be polite to Wikipedia
            contest = parse_page_for_race(
                html_cache[url],
                race_id=rid,
                state=spec["state"],
                source_url=url,
            )
            if contest is None:
                missing.append(rid)
                failures.append({"race_id": rid, "error": f"no general table at {url}"})
                continue
            by_year[int(spec["year"])].append(contest)
        except HTTPError as exc:
            missing.append(rid)
            failures.append({"race_id": rid, "error": f"HTTP {exc.code} {url}"})
        except Exception as exc:  # noqa: BLE001
            missing.append(rid)
            failures.append({"race_id": rid, "error": f"{type(exc).__name__}: {exc}"})

    for year, contests in by_year.items():
        contests_sorted = sorted(contests, key=lambda c: c["race_id"])
        cycles[str(year)] = {
            "year": year,
            "n_contests": len(contests_sorted),
            "content_sha256": _cycle_hash(contests_sorted),
            "contests": contests_sorted,
        }

    return {
        "parser_version": PARSER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Wikipedia United States Senate election result tables (pandas.read_html)",
        "cycles": cycles,
        "missing": sorted(missing),
        "scrape_failures": failures,
        "n_expected": len(specs),
        "n_scraped": sum(len(v["contests"]) for v in cycles.values()),
    }


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = build_certified_ledger()
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    summary = {
        "path": str(OUT_PATH),
        "n_scraped": payload["n_scraped"],
        "n_missing": len(payload["missing"]),
        "per_year": {y: cycles["n_contests"] for y, cycles in payload["cycles"].items()},
        "missing": payload["missing"],
        "n_failures": len(payload["scrape_failures"]),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
