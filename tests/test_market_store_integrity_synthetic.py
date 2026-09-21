"""Synthetic byte and parser checks for candidate-aware market storage."""

import hashlib
import json

import pandas as pd
import pytest

from midterms.evidence import markets


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_market_store_requires_current_parser_complete_audit_and_unchanged_bytes(tmp_path, monkeypatch):
    raw, normalized, manifests = (tmp_path / part for part in ("raw", "normalized", "manifests"))
    for path in (raw / "external", normalized, manifests):
        path.mkdir(parents=True)
    monkeypatch.setattr(markets, "RAW_DIR", raw)
    monkeypatch.setattr(markets, "NORMALIZED_DIR", normalized)
    monkeypatch.setattr(markets, "MANIFESTS_DIR", manifests)
    raw_path = raw / "external" / "kalshi_senate.json"
    race_path = normalized / "markets.parquet"
    control_path = normalized / "markets_control.json"
    audit_path = normalized / "markets_mapping_audit.json"
    raw_path.write_text("{}", encoding="utf-8")
    pd.DataFrame(columns=["race_id", "parser_version", "overlay_enabled"]).to_parquet(race_path)
    control_path.write_text("{}", encoding="utf-8")
    events = [
        {"race_id": f"synthetic-2026-{state}", "event_id": f"EVENT-{state}",
         "parser_version": markets.PARSER_VERSION, "overlay_enabled": False,
         "fetch_status": "no_event_contracts",
         "disable_reason": "synthetic event has no priced contracts"}
        for state in markets.SENATE_2026_STATES
    ]
    audit_path.write_text(json.dumps({"parser_version": markets.PARSER_VERSION, "events": events}))
    manifest = {
        "election_id": "synthetic-2026", "available_at": "2026-01-01",
        "retrieval_date": "2026-01-01",
        "parser_version": markets.PARSER_VERSION,
        "raw_sha256": _sha(raw_path), "normalized_races_sha256": _sha(race_path),
        "normalized_control_sha256": _sha(control_path),
        "mapping_audit": {"sha256": _sha(audit_path)},
    }
    manifest_path = manifests / "markets_kalshi.json"
    manifest_path.write_text(json.dumps(manifest))
    assert markets.verify_market_store_integrity(as_of="2026-01-02")["ok"]
    assert not markets.verify_market_store_integrity(as_of="2025-12-31")["ok"]
    manifest["parser_version"] = "obsolete-parser"
    manifest_path.write_text(json.dumps(manifest))
    assert "stale" in markets.verify_market_store_integrity()["reason"]
    manifest["parser_version"] = markets.PARSER_VERSION
    manifest_path.write_text(json.dumps(manifest))
    audit_path.write_text(audit_path.read_text() + " ")
    assert "changed" in markets.verify_market_store_integrity()["reason"]


def test_live_market_refresh_cannot_backdate_retrieval(tmp_path, monkeypatch):
    monkeypatch.setattr(markets, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(markets, "NORMALIZED_DIR", tmp_path / "normalized")
    monkeypatch.setattr(markets, "MANIFESTS_DIR", tmp_path / "manifests")
    monkeypatch.setattr(markets, "_utc_today", lambda: "2026-09-20")
    with pytest.raises(ValueError, match="cannot be backdated"):
        markets.write_markets_store("synthetic-2026", available_at="2026-09-13")
