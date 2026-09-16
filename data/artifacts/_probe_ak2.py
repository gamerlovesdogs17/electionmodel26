from __future__ import annotations

from urllib.request import Request, urlopen

from midterms.evidence.ingest_certified_votes import (
    USER_AGENT,
    _flatten_cols,
    _is_result_table,
    _safe_read_html,
    extract_candidate_rows,
    score_general_table,
)

url = "https://en.wikipedia.org/wiki/2022_United_States_Senate_election_in_Alaska"
html = urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=90).read()
tables = _safe_read_html(html)
print("n_tables", len(tables))
for i, df in enumerate(tables):
    cols = _flatten_cols(df)
    is_res = _is_result_table(df)
    rows = extract_candidate_rows(df) if is_res else []
    total, n_cand, n_party = score_general_table(rows) if rows else (0, 0, 0)
    flag = ""
    if any("murkowski" in str(x).lower() for x in df.astype(str).values.flatten()[:50]):
        flag = " HAS_MURKOWSKI"
    if total or "vote" in " ".join(cols).lower() or flag:
        print(f"t{i} shape={df.shape} is_res={is_res} total={total} n={n_cand} cols={cols[:6]}{flag}")
        if total:
            print("  top", [(r["name"], r["votes"]) for r in rows[:4]])
