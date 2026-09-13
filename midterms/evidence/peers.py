"""Peer forecast snapshots for research comparison (not ensemble inputs)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midterms.config import MANIFESTS_DIR, NORMALIZED_DIR, RAW_DIR
from midterms.evidence.markets import load_control_market, load_race_markets

PARSER_VERSION = "peer-compare-v1"

# Curated public snapshots — provenance in notes. Update via write_peer_snapshots.
# Peers are design evidence / context only (blueprint §2); never averaged in.
DEFAULT_PEER_SNAPSHOT: dict[str, Any] = {
    "as_of": "2026-09-12",
    "retrieved_at": None,
    "sources": {
        "ddhq": {
            "label": "Decision Desk HQ",
            "url": "https://votes.decisiondeskhq.com/forecast/2026/senate",
            "p_dem_control": 0.48,
            "note": "Curated research snapshot from public DDHQ Senate page (approx).",
            "races": {
                "ME": 0.48,
                "TX": 0.47,
                "OH": 0.46,
                "FL": 0.20,
                "NC": 0.22,
                "GA": 0.82,
                "NH": 0.78,
                "MI": 0.69,
                "AK": 0.42,
                "IA": 0.41,
            },
        },
        "economist": {
            "label": "Economist / FiftyPlusOne",
            "url": "https://www.economist.com/interactive/2026/us-midterms/prediction-model/senate",
            "p_dem_control": None,
            "note": "Control probability not always published as a single scalar; race cells filled when available.",
            "races": {},
        },
        "kalshi": {
            "label": "Kalshi",
            "url": "https://kalshi.com",
            "p_dem_control": None,
            "note": "Filled live from markets store at write time.",
            "races": {},
        },
    },
    "key_races": ["ME", "NC", "OH", "FL", "TX", "GA", "NH", "MI", "AK", "IA"],
    "disclaimer": (
        "Peer numbers are research context only — not averaged into this model's "
        "ensemble (blueprint §2). Snapshots may lag live sites."
    ),
}


def write_peer_snapshots(path: Path | None = None) -> dict[str, Any]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    payload = json.loads(json.dumps(DEFAULT_PEER_SNAPSHOT))
    payload["retrieved_at"] = datetime.now(timezone.utc).isoformat()

    control = load_control_market()
    races = load_race_markets()
    kalshi = payload["sources"]["kalshi"]
    if control.get("p_dem") is not None:
        kalshi["p_dem_control"] = float(control["p_dem"])
    if len(races):
        kalshi["races"] = {
            str(r["state"]): float(r["p_dem"]) for _, r in races.iterrows()
        }

    raw_path = RAW_DIR / "external" / "peer_snapshots.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(payload, indent=2))

    out = path or (NORMALIZED_DIR / "peer_snapshots.json")
    out.write_text(json.dumps(payload, indent=2))

    man = {
        "generated_at": payload["retrieved_at"],
        "as_of": payload["as_of"],
        "sources": list(payload["sources"].keys()),
        "parser_version": PARSER_VERSION,
        "paths": {"raw": str(raw_path), "normalized": str(out)},
    }
    (MANIFESTS_DIR / "peer_snapshots.json").write_text(json.dumps(man, indent=2))
    return man


def load_peer_snapshots() -> dict[str, Any]:
    path = NORMALIZED_DIR / "peer_snapshots.json"
    if not path.exists():
        write_peer_snapshots()
    return json.loads(path.read_text())


def compare_to_peers(artifact: dict[str, Any]) -> dict[str, Any]:
    """Build side-by-side control + key-race table vs peer snapshots."""
    peers = load_peer_snapshots()
    chamber = artifact.get("chamber") or {}
    races = {str(r["state"]): r for r in artifact.get("races") or []}
    key = peers.get("key_races") or []

    our_control = chamber.get("p_dem_majority")
    rows = []
    for st in key:
        r = races.get(st)
        row: dict[str, Any] = {
            "state": st,
            "ours": None if r is None else float(r["p_dem"]),
            "rating": None if r is None else r.get("rating"),
        }
        for src, block in (peers.get("sources") or {}).items():
            race_map = block.get("races") or {}
            row[src] = race_map.get(st)
        rows.append(row)

    control = {"ours": our_control}
    for src, block in (peers.get("sources") or {}).items():
        control[src] = block.get("p_dem_control")

    return {
        "as_of_peers": peers.get("as_of"),
        "disclaimer": peers.get("disclaimer"),
        "control_p_dem": control,
        "races": rows,
        "sources": {
            k: {"label": v.get("label"), "url": v.get("url"), "note": v.get("note")}
            for k, v in (peers.get("sources") or {}).items()
        },
    }
