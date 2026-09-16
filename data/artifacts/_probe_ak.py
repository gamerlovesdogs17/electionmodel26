from __future__ import annotations

import json
from urllib.request import Request, urlopen

from midterms.evidence.ingest_certified_votes import (
    USER_AGENT,
    _pick_rcv_rows,
    _safe_read_html,
    pick_general_tables,
)

url = "https://en.wikipedia.org/wiki/2022_United_States_Senate_election_in_Alaska"
html = urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=90).read()
tables = _safe_read_html(html)
scored = pick_general_tables(tables)
print("n_scored", len(scored))
for total, rows, idx in scored[:12]:
    parties = sorted({r["party_bucket"] for r in rows})
    top = rows[:3]
    print(
        json.dumps(
            {
                "table": idx,
                "total": total,
                "n": len(rows),
                "parties": parties,
                "top": [(r["name"], r["party"], r["votes"]) for r in top],
            }
        )
    )
chosen = _pick_rcv_rows(scored)
print("CHOSEN", [(r["name"], r["votes"]) for r in chosen[:5]], "n=", len(chosen))
