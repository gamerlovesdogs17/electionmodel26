"""Temporary probe of Wikipedia Senate result tables."""
from __future__ import annotations

import io
from urllib.request import Request, urlopen

import pandas as pd

UA = "midterms-senate-model/0.9.20 (research; certified vote ingest)"


def fetch(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": UA})
    return urlopen(req, timeout=90).read()


def probe(url: str) -> None:
    print("\n####", url)
    html = fetch(url)
    print("bytes", len(html))
    tables = pd.read_html(io.BytesIO(html))
    print("n_tables", len(tables))
    for i, t in enumerate(tables):
        cols = [str(c) for c in t.columns.tolist()]
        joined = " ".join(cols).lower()
        if not any(k in joined for k in ("vote", "candidate", "party", "%")):
            continue
        print(f"\n=== table {i} shape={t.shape} ===")
        print("cols:", cols)
        print(t.head(8).to_string())


if __name__ == "__main__":
    for u in [
        "https://en.wikipedia.org/wiki/2018_United_States_Senate_election_in_Ohio",
        "https://en.wikipedia.org/wiki/2018_United_States_Senate_election_in_California",
        "https://en.wikipedia.org/wiki/2018_United_States_Senate_special_election_in_Minnesota",
        "https://en.wikipedia.org/wiki/2022_United_States_Senate_election_in_Alaska",
        "https://en.wikipedia.org/wiki/2024_United_States_Senate_election_in_Nebraska",
        "https://en.wikipedia.org/wiki/2024_United_States_Senate_elections_in_California",
    ]:
        try:
            probe(u)
        except Exception as e:
            print("FAIL", u, type(e).__name__, e)
