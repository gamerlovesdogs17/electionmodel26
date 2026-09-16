"""Smoke-test certified vote ingest on a few known races."""
from __future__ import annotations

import json

from midterms.evidence.ingest_certified_votes import expected_race_specs, ingest_race, wiki_url_for

WANT = {
    "senate-2018-OH",
    "senate-2018-CA",
    "senate-2024-AZ",
    "senate-2024-NE",
    "senate-2022-AK",
    "senate-2018-MN-special",
    "senate-2024-CA",
    "senate-2024-CA-unexpired",
}

specs = [s for s in expected_race_specs() if s["race_id"] in WANT]
# de-dupe CA URL path: still one-by-one via ingest_race
for spec in specs:
    contest, err = ingest_race(spec)
    print("====", spec["race_id"], wiki_url_for(spec))
    if err:
        print("ERR", err)
    else:
        slim = {k: contest[k] for k in contest if k != "candidates"}
        slim["n_candidates"] = len(contest["candidates"])
        slim["candidates_top"] = contest["candidates"][:4]
        print(json.dumps(slim, indent=2))
