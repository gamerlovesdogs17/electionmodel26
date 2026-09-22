"""Synthetic byte and parser checks for candidate-aware market storage."""

import hashlib
import json

import pandas as pd
import pytest

from midterms.evidence import markets


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(path):
    return markets.canonical_json_sha256(path)


def _write_synthetic_store(tmp_path, monkeypatch):
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
        "json_hash_mode": markets.JSON_HASH_MODE,
        "raw_sha256": _sha(raw_path), "normalized_races_sha256": _sha(race_path),
        "raw_canonical_json_sha256": _canonical(raw_path),
        "normalized_control_sha256": _sha(control_path),
        "normalized_control_canonical_json_sha256": _canonical(control_path),
        "mapping_audit": {
            "sha256": _sha(audit_path),
            "hash_mode": markets.JSON_HASH_MODE,
            "canonical_json_sha256": _canonical(audit_path),
        },
    }
    manifest_path = manifests / "markets_kalshi.json"
    manifest_path.write_text(json.dumps(manifest))
    return {
        "raw": raw_path, "races": race_path, "control": control_path,
        "audit": audit_path, "manifest": manifest_path,
        "manifest_payload": manifest,
    }


def test_canonical_json_hash_normalizes_transport_but_detects_value_change(tmp_path):
    lf = tmp_path / "lf.json"
    crlf = tmp_path / "crlf.json"
    reordered = tmp_path / "reordered.json"
    changed = tmp_path / "changed.json"
    lf.write_bytes(b'{\n  "price": 0.4,\n  "ticker": "SYNTHETIC"\n}\n')
    crlf.write_bytes(b'{\r\n  "price": 0.4,\r\n  "ticker": "SYNTHETIC"\r\n}\r\n')
    reordered.write_text('{"ticker":"SYNTHETIC", "price": 0.4}', encoding="utf-8")
    changed.write_text('{"ticker":"SYNTHETIC", "price": 0.5}', encoding="utf-8")
    assert _canonical(lf) == _canonical(crlf) == _canonical(reordered)
    assert _canonical(changed) != _canonical(lf)


def test_market_store_requires_current_parser_complete_audit_and_semantic_json(
    tmp_path, monkeypatch,
):
    store = _write_synthetic_store(tmp_path, monkeypatch)
    manifest = store["manifest_payload"]
    manifest_path = store["manifest"]
    audit_path = store["audit"]
    assert markets.verify_market_store_integrity(as_of="2026-01-02")["ok"]
    assert not markets.verify_market_store_integrity(as_of="2025-12-31")["ok"]
    manifest["parser_version"] = "obsolete-parser"
    manifest_path.write_text(json.dumps(manifest))
    assert "stale" in markets.verify_market_store_integrity()["reason"]
    manifest["parser_version"] = markets.PARSER_VERSION
    manifest_path.write_text(json.dumps(manifest))
    # Transport-only whitespace does not change the evidence fingerprint.
    audit_path.write_text(audit_path.read_text() + " ")
    assert markets.verify_market_store_integrity()["ok"]
    audit = json.loads(audit_path.read_text())
    audit["events"][0]["synthetic_price"] = 0.5
    audit_path.write_text(json.dumps(audit))
    assert "changed" in markets.verify_market_store_integrity()["reason"]


def test_market_store_missing_json_and_changed_parquet_fail_closed(tmp_path, monkeypatch):
    store = _write_synthetic_store(tmp_path, monkeypatch)
    raw_bytes = store["raw"].read_bytes()
    store["raw"].unlink()
    assert "missing or changed" in markets.verify_market_store_integrity()["reason"]
    store["raw"].write_bytes(raw_bytes)
    parquet_bytes = store["races"].read_bytes()
    store["races"].write_bytes(parquet_bytes + b"\x00")
    result = markets.verify_market_store_integrity()
    assert not result["ok"]
    assert "normalized_races_sha256" in result["reason"]


def test_checked_in_market_store_is_stale_after_semantic_parser_upgrade():
    result = markets.verify_market_store_integrity(as_of="2026-09-20")
    assert result["ok"] is False
    assert "stale" in result.get("reason", "")


def test_live_market_refresh_cannot_backdate_retrieval(tmp_path, monkeypatch):
    monkeypatch.setattr(markets, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(markets, "NORMALIZED_DIR", tmp_path / "normalized")
    monkeypatch.setattr(markets, "MANIFESTS_DIR", tmp_path / "manifests")
    monkeypatch.setattr(markets, "_utc_today", lambda: "2026-09-20")
    with pytest.raises(ValueError, match="cannot be backdated"):
        markets.write_markets_store("synthetic-2026", available_at="2026-09-13")
