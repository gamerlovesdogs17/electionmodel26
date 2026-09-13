"""Wikipedia 2026 Senate predictions table → expert-rating consensus.

Pulls the public Predictions / Ratings wikitable from
https://en.wikipedia.org/wiki/2026_United_States_Senate_elections

Consensus uses the three traditional "top" handicappers when present:
Cook Political Report, Inside Elections (IE), Sabato's Crystal Ball.

Wikipedia text is CC BY-SA; cell values remain third-party ratings attributed
via the page. This is a dated public multi-rater consensus with provenance —
not a substitute for a licensed Cook/IE feed.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from midterms.config import MANIFESTS_DIR, RAW_DIR
from midterms.model.overlays import RATING_ORDER

PARSER_VERSION = "wiki-ratings-v2"
WIKI_PAGE = "2026_United_States_Senate_elections"
WIKI_URL = f"https://en.wikipedia.org/wiki/{WIKI_PAGE}"
API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "midterms-senate-model/0.9 (research; Wikipedia ratings ingest)"

# Core panel always drives overlay consensus (ordinal median).
TOP_RATERS = ("Cook", "IE", "Sabato")
# Extended panel stored for audit; optional soft pull via extended_weight.
EXTENDED_RATERS = ("WH", "RCP", "DDHQ", "Fox", "Econ")
SOURCE_LABEL = "wikipedia:multi-rater"

STATE_NAME_TO_ABBR = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
}

_RATING_ALIASES = {
    "safe d": "Solid D",
    "solid d": "Solid D",
    "likely d": "Likely D",
    "lean d": "Lean D",
    "tilt d": "Tilt D",
    "tossup": "Tossup",
    "toss-up": "Tossup",
    "toss up": "Tossup",
    "tilt r": "Tilt R",
    "lean r": "Lean R",
    "likely r": "Likely R",
    "safe r": "Solid R",
    "solid r": "Solid R",
}

_ORD = {lab: i for i, lab in enumerate(RATING_ORDER)}


def _clean_cell(raw: str) -> str:
    text = html_lib.unescape(raw)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    return " ".join(text.split())


def normalize_wiki_rating(label: str | None) -> str | None:
    """Map Wikipedia cell text onto the model rating vocabulary."""
    if label is None:
        return None
    s = str(label).strip()
    if not s or s.lower() in {"—", "-", "n/a", "na", "tba", "tbd"}:
        return None
    s = re.sub(r"\s*\(flip\)\s*", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    key = s.lower()
    if key in _RATING_ALIASES:
        return _RATING_ALIASES[key]
    # Soft match: first two tokens (e.g. "Likely D hold")
    parts = key.split()
    if len(parts) >= 2:
        cand = f"{parts[0]} {parts[1]}"
        if cand in _RATING_ALIASES:
            return _RATING_ALIASES[cand]
    if key in {"tossup", "toss-up"}:
        return "Tossup"
    return None


def consensus_rating(ratings: list[str | None]) -> str | None:
    """Median ordinal among recognized ratings (ignores missing)."""
    idxs = [_ORD[r] for r in ratings if r in _ORD]
    if not idxs:
        return None
    idxs.sort()
    mid = idxs[len(idxs) // 2]
    return RATING_ORDER[mid]


def state_abbr_from_label(label: str) -> str | None:
    """Map 'Florida (special)' / 'North Carolina' → postal code."""
    s = _clean_cell(label)
    s = re.sub(r"\s*\(.*?\)\s*", " ", s).strip()
    if s in STATE_NAME_TO_ABBR:
        return STATE_NAME_TO_ABBR[s]
    # Already an abbreviation
    if re.fullmatch(r"[A-Z]{2}", s):
        return s
    return None


def _header_key(cell: str) -> str | None:
    """Collapse 'CookAug. 20,2026' / 'IE Sep. 3, 2026' into a rater key."""
    s = _clean_cell(cell)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return None
    low = s.lower()
    if low in {"state", "pvi", "senator", "incumbent", "constituency", "ratings"}:
        return None
    if low.startswith("last"):
        return None

    # Smashed wiki headers: CookAug..., IESep..., SabatoAug..., RTTWHSep...
    # Lookahead avoids matching State/Senator as ST.
    m = re.match(
        r"^(Cook|Sabato|DDHQ|Econ|FPO|Fox|RCP|Silver|RTTWH|VoteHub|IE|ST|WH)"
        r"(?=[A-Z0-9\s\.\-]|$)",
        s,
    )
    if m:
        raw = m.group(1).upper()
        canon = {
            "COOK": "Cook",
            "IE": "IE",
            "SABATO": "Sabato",
            "DDHQ": "DDHQ",
            "ECON": "Econ",
            "FPO": "FPO",
            "FOX": "Fox",
            "RCP": "RCP",
            "SILVER": "Silver",
            "ST": "ST",
            "RTTWH": "WH",
            "WH": "WH",
            "VOTEHUB": "VoteHub",
        }
        return canon.get(raw)

    aliases = (
        ("inside elections", "IE"),
        ("cook political", "Cook"),
        ("sabato", "Sabato"),
        ("decision desk", "DDHQ"),
        ("the economist", "Econ"),
        ("split ticket", "ST"),
        ("realclearpolitics", "RCP"),
        ("race to the white house", "WH"),
        ("votehub", "VoteHub"),
    )
    for needle, key in aliases:
        if needle in low:
            return key
    return None


def _parse_asof_from_header(cell: str) -> str | None:
    """Best-effort 'YYYY-MM-DD' from 'Cook Aug. 20, 2026'."""
    s = _clean_cell(cell)
    m = re.search(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s*(\d{4})",
        s,
        re.I,
    )
    if not m:
        return None
    months = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    mon = months[m.group(1)[:3].lower()]
    day = int(m.group(2))
    year = int(m.group(3))
    return f"{year:04d}-{mon:02d}-{day:02d}"


def parse_ratings_table_html(
    html: str,
    *,
    extended_weight: float = 0.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse the Predictions wikitable HTML into per-state rating rows."""
    tables = re.findall(
        r'<table[^>]*class="[^"]*wikitable[^"]*"[^>]*>(.*?)</table>',
        html,
        re.S | re.I,
    )
    if not tables:
        raise ValueError("No wikitable found in Wikipedia Predictions section")

    chosen = None
    header_keys: list[str | None] = []
    header_asofs: dict[str, str] = {}
    for body in tables:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S | re.I)
        for row in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)
            keys = [_header_key(c) for c in cells]
            if sum(1 for k in keys if k in TOP_RATERS) >= 2:
                chosen = rows
                header_keys = []
                for c in cells:
                    k = _header_key(c)
                    header_keys.append(k)
                    if k:
                        asof = _parse_asof_from_header(c)
                        if asof:
                            header_asofs[k] = asof
                break
        if chosen is not None:
            break
    if chosen is None:
        raise ValueError("Could not locate Cook/IE/Sabato ratings header row")

    w_ext = max(0.0, min(1.0, float(extended_weight or 0.0)))

    out: list[dict[str, Any]] = []
    for row in chosen:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)
        if len(cells) < 5:
            continue
        st_label = _clean_cell(cells[0])
        abbr = state_abbr_from_label(st_label)
        if not abbr:
            continue
        by_rater: dict[str, str | None] = {}
        for i, cell in enumerate(cells):
            if i >= len(header_keys):
                break
            key = header_keys[i]
            if not key:
                continue
            by_rater[key] = normalize_wiki_rating(_clean_cell(cell))
        top = [by_rater.get(r) for r in TOP_RATERS]
        cons = consensus_rating(top)
        if cons is None:
            cons = consensus_rating(list(by_rater.values()))
        if cons is None:
            continue
        extended = {r: by_rater.get(r) for r in EXTENDED_RATERS if by_rater.get(r)}
        ext_cons = consensus_rating(list(extended.values())) if extended else None
        if w_ext > 0 and ext_cons is not None and cons in _ORD and ext_cons in _ORD:
            # Soft ordinal blend toward extended median, then snap to nearest label
            blended = (1.0 - w_ext) * _ORD[cons] + w_ext * _ORD[ext_cons]
            cons = RATING_ORDER[int(round(blended))]
        out.append(
            {
                "state": abbr,
                "state_label": st_label,
                "rating": cons,
                "raters": {k: v for k, v in by_rater.items() if v},
                "core": {r: by_rater.get(r) for r in TOP_RATERS if by_rater.get(r)},
                "extended": extended,
                "cook": by_rater.get("Cook"),
                "ie": by_rater.get("IE"),
                "sabato": by_rater.get("Sabato"),
            }
        )

    top_asofs = [header_asofs[r] for r in TOP_RATERS if r in header_asofs]
    meta = {
        "n": len(out),
        "header_asofs": header_asofs,
        "top_raters": list(TOP_RATERS),
        "extended_raters": list(EXTENDED_RATERS),
        "extended_weight": w_ext,
        "available_at": max(top_asofs) if top_asofs else (max(header_asofs.values()) if header_asofs else None),
    }
    return out, meta


def _api_get(params: dict[str, Any]) -> dict[str, Any]:
    q = urlencode(params)
    req = Request(f"{API}?{q}", headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_predictions_html(page: str = WIKI_PAGE) -> tuple[str, dict[str, Any]]:
    """Fetch Predictions-section HTML (fallback: full page text)."""
    sections = _api_get(
        {
            "action": "parse",
            "page": page,
            "prop": "sections",
            "format": "json",
            "formatversion": "2",
        }
    )
    section_idx = None
    for s in sections.get("parse", {}).get("sections") or []:
        line = str(s.get("line") or "").lower()
        if "prediction" in line or "rating" in line:
            section_idx = str(s.get("index"))
            break
    params: dict[str, Any] = {
        "action": "parse",
        "page": page,
        "prop": "text|revid",
        "format": "json",
        "formatversion": "2",
    }
    if section_idx is not None:
        params["section"] = section_idx
    parsed = _api_get(params)
    html = parsed.get("parse", {}).get("text") or ""
    meta = {
        "page": page,
        "url": WIKI_URL if page == WIKI_PAGE else f"https://en.wikipedia.org/wiki/{page}",
        "section_index": section_idx,
        "revid": parsed.get("parse", {}).get("revid"),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    return html, meta


def fetch_wikipedia_expert_ratings(
    *,
    election_id: str = "senate-2026",
    page: str = WIKI_PAGE,
    extended_weight: float = 0.0,
) -> dict[str, Any]:
    """
    Live fetch → parse → return consensus rows ready for write_expert_ratings_store.

    Also seals a raw detail JSON under data/raw/external/.
    """
    html, fetch_meta = fetch_predictions_html(page=page)
    rows, table_meta = parse_ratings_table_html(html, extended_weight=extended_weight)
    available_at = table_meta.get("available_at") or datetime.now(timezone.utc).date().isoformat()

    stamped = []
    for r in rows:
        stamped.append(
            {
                "election_id": election_id,
                "state": r["state"],
                "rating": r["rating"],
                "source": SOURCE_LABEL,
                "available_at": available_at,
                "cook": r.get("cook"),
                "ie": r.get("ie"),
                "sabato": r.get("sabato"),
                "core": r.get("core") or {},
                "extended": r.get("extended") or {},
                "raters": r.get("raters") or {},
            }
        )

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / "external" / "wiki_senate_ratings.json"
    payload = {
        "parser_version": PARSER_VERSION,
        "license_note": (
            "Wikipedia page text CC BY-SA; rating labels attributed to source handicappers "
            "via the Wikipedia Predictions table — not vendor redistribution of Cook/IE feeds."
        ),
        "core_raters": list(TOP_RATERS),
        "extended_raters": list(EXTENDED_RATERS),
        "fetch": fetch_meta,
        "table": table_meta,
        "rows": stamped,
    }
    raw_path.write_text(json.dumps(payload, indent=2))
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parser_version": PARSER_VERSION,
        "election_id": election_id,
        "available_at": available_at,
        "n": len(stamped),
        "source": SOURCE_LABEL,
        "page_url": fetch_meta.get("url"),
        "revid": fetch_meta.get("revid"),
        "header_asofs": table_meta.get("header_asofs"),
        "raw": str(raw_path),
    }
    (MANIFESTS_DIR / "wiki_ratings.json").write_text(json.dumps(man, indent=2))
    return {
        **man,
        "rows": [{"state": r["state"], "rating": r["rating"], "source": r["source"]} for r in stamped],
        "detail_rows": stamped,
    }


def write_from_wikipedia(
    election_id: str = "senate-2026",
    *,
    available_at: str | None = None,
    extended_weight: float = 0.0,
) -> dict[str, Any]:
    """Fetch Wikipedia consensus and persist via the expert ratings store."""
    from midterms.evidence.expert_ratings import write_expert_ratings_store

    fetched = fetch_wikipedia_expert_ratings(
        election_id=election_id, extended_weight=extended_weight
    )
    avail = available_at or fetched.get("available_at") or "2026-09-01"
    man = write_expert_ratings_store(
        election_id=election_id,
        available_at=str(avail)[:10],
        rows=fetched["rows"],
        source_label=SOURCE_LABEL,
    )
    return {**man, "wiki": {k: fetched[k] for k in ("n", "revid", "page_url", "header_asofs", "raw")}}
