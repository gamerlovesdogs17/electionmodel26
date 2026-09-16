from __future__ import annotations

from urllib.request import Request, urlopen

from midterms.evidence.ingest_certified_votes import USER_AGENT, _flatten_cols, _safe_read_html

url = "https://en.wikipedia.org/wiki/2022_United_States_Senate_election_in_Alaska"
html = urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=90).read()
tables = _safe_read_html(html)
df = tables[18]
print("cols:", _flatten_cols(df))
print(df.to_string())
