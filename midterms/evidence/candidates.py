"""2026 Senate candidate party lookup for VoteHub answer normalization.

VoteHub us-senator answers are candidate names without party labels.
This curated map covers major general-election matchups seen in the
VoteHub archive as of the ingest date. Unknown candidates cause the poll
to be excluded rather than guessed.
"""

from __future__ import annotations

# Canonical last-token / full-name keys (case-insensitive match in normalize).
CANDIDATE_PARTY: dict[str, str] = {
    # Alabama
    "alani bankhead": "D",
    "barry moore": "R",
    "everett wess": "D",
    # Alaska
    "mary peltola": "D",
    "dan sullivan": "R",
    "dan sullvian": "R",  # VoteHub typo variant
    "nick begich": "R",
    # Arkansas
    "hallie shoffner": "D",
    "tom cotton": "R",
    # Florida
    "ashley moody": "R",
    "angie nixon": "D",
    "lara trump": "R",
    # Georgia
    "jon ossoff": "D",
    "mike collins": "R",
    "derek dooley": "R",
    "buddy carter": "R",
    # Idaho
    "jim risch": "R",
    "todd achilles": "D",
    # Iowa
    "ashley hinson": "R",
    "josh turek": "D",
    "zach wahls": "D",
    # Kansas
    "roger marshall": "R",
    "adam hamilton": "D",
    # Maine
    "susan collins": "R",
    "graham platner": "D",
    "janet mills": "D",
    "troy jackson": "D",
    # Massachusetts
    "ed markey": "D",
    "seth moulton": "D",
    "john deaton": "R",
    "joe tache": "R",
    # Michigan
    "abdul el-sayed": "D",
    "haley stevens": "D",
    "mallory mcmorrow": "D",
    "mike rogers": "R",
    # Minnesota
    "angie craig": "D",
    "peggy flanagan": "D",
    "royce white": "R",
    "michele tafoya": "R",
    # Mississippi
    "cindy hyde-smith": "R",
    "scott colom": "D",
    # Montana
    "kurt alme": "R",
    "seth bodnar": "D",
    "reilly neill": "D",
    # New Hampshire
    "chris pappas": "D",
    "john sununu": "R",
    "scott brown": "R",
    # New Mexico
    "ben ray luján": "D",
    "ben ray lujan": "D",
    "larry marker": "R",
    # North Carolina
    "roy cooper": "D",
    "michael whatley": "R",
    "don brown": "R",
    # Ohio
    "sherrod brown": "D",
    "jon husted": "R",
    # Rhode Island
    "jack reed": "D",
    "raymond mckay": "R",
    # South Carolina
    "lindsey graham": "R",
    "annie andrews": "D",
    "darline graham": "D",
    # Tennessee
    "bill hagerty": "R",
    "marquita bradshaw": "D",
    # Texas
    "ken paxton": "R",
    "james talarico": "D",
    "john cornyn": "R",
    "colin allred": "D",
    # Virginia
    "mark warner": "D",
    "alexander vindman": "D",
    "kyle austin": "R",
    "bert mizusawa": "R",
    "kim farington": "R",
    "mark moran": "R",
    "david williams": "R",  # VA challenger name collision with ID Dem — prefer R for senate GE
    # South Dakota / others occasionally present
    "mike rounds": "R",
    "marie gladue": "D",
    # Idaho
    "jim risch": "R",
    "todd achilles": "D",
    # Generic labels sometimes present
    "dem": "D",
    "democrat": "D",
    "democratic": "D",
    "rep": "R",
    "republican": "R",
}

# Aliases from VoteHub / FTE pollster display names → scorecard keys.
POLLSTER_ALIASES: dict[str, str] = {
    "emerson college": "Emerson",
    "emerson college polling": "Emerson",
    "emerson": "Emerson",
    "the new york times/siena college": "Siena-NYT",
    "new york times/siena college": "Siena-NYT",
    "siena college": "Siena-NYT",
    "siena": "Siena-NYT",
    "siena-nyt": "Siena-NYT",
    "beacon research/shaw & co. research": "Beacon-Shaw",
    "beacon research/shaw & company": "Beacon-Shaw",
    "beacon-shaw": "Beacon-Shaw",
    "fabrizio ward/impact research": "Fabrizio-Impact",
    "fabrizio/impact": "Fabrizio-Impact",
    "fabrizio-impact": "Fabrizio-Impact",
    "marist college": "Marist",
    "marist poll": "Marist",
    "suffolk university": "Suffolk",
    "trafalgar": "Trafalgar Group",
    "the trafalgar group": "Trafalgar Group",
    "cnn/ssrs": "CNN-SSRS",
    "ssrs": "CNN-SSRS",
    "you gov": "YouGov",
    "redfield and wilton strategies": "Redfield & Wilton Strategies",
    "umass lowell/yougov": "University of Massachusetts Lowell-YouGov",
    "university of massachusetts lowell": "University of Massachusetts Lowell-YouGov",
    "florida atlantic university": "Florida Atlantic University-Mainstreet Research",
    "mainstreet research": "Florida Atlantic University-Mainstreet Research",
    "washington post/george mason university": "Washington Post-George Mason University",
    "tipp insights": "TIPP",
    "cyngal": "Cygnal",  # VoteHub typo
}


def normalize_candidate_key(name: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.strip().lower().split())


def candidate_party(name: str) -> str | None:
    key = normalize_candidate_key(name)
    if key in CANDIDATE_PARTY:
        return CANDIDATE_PARTY[key]
    # last-name fallback when unique enough is dangerous; keep strict.
    return None


def canonicalize_pollster(name: str) -> str:
    raw = str(name).strip()
    key = normalize_candidate_key(raw)
    if key in POLLSTER_ALIASES:
        return POLLSTER_ALIASES[key]
    # fuzzy contains for common patterns
    for alias, canon in POLLSTER_ALIASES.items():
        if alias in key or key in alias:
            return canon
    return raw
