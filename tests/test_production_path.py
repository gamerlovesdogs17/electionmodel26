"""Production-path: licensed ratings adapter, VoteHub dumps, auth, Ed25519."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from midterms.evidence.licensed_ratings import (
    normalize_rating_label,
    try_ingest_licensed_ratings,
    write_example_licensed_csv,
)
from midterms.ops.signing import generate_keypair, sign_payload, verify_signature


def test_normalize_cook_style_labels():
    assert normalize_rating_label("Likely Democrat") == "Likely D"
    assert normalize_rating_label("Toss-up") == "Tossup"
    assert normalize_rating_label("Solid R") == "Solid R"


def test_licensed_ingest_absent_is_ok(tmp_path, monkeypatch):
    monkeypatch.delenv("COOK_RATINGS_CSV", raising=False)
    monkeypatch.delenv("LICENSED_RATINGS_CSV", raising=False)
    # Point default path away by using env to missing file
    monkeypatch.setenv("COOK_RATINGS_CSV", str(tmp_path / "missing.csv"))
    man = try_ingest_licensed_ratings()
    assert man["licensed_present"] is False
    assert man["ok"] is False


def test_licensed_ingest_present(tmp_path, monkeypatch):
    csv = tmp_path / "cook.csv"
    csv.write_text("state,rating,available_at,election_id,source\nGA,Lean D,2026-09-01,senate-2026,licensed:cook\n")
    monkeypatch.setenv("COOK_RATINGS_CSV", str(csv))
    man = try_ingest_licensed_ratings(available_at="2026-09-01")
    assert man["ok"] is True
    assert man["licensed_present"] is True
    assert "GA" in man["states"]


def test_ed25519_sign_verify(tmp_path, monkeypatch):
    # Generate into temp paths via monkeypatch of module paths would be heavy;
    # use generate_keypair then sign with env private key.
    from midterms.ops import signing as signing_mod

    keys = generate_keypair(write_private=True)
    priv = Path(keys["private_key_path"]).read_text()
    monkeypatch.setenv("MIDTERMS_SIGNING_PRIVATE_KEY", priv)
    monkeypatch.delenv("MIDTERMS_SIGNING_KEY", raising=False)
    payload = {"run_id": "test", "v": 1}
    sig = sign_payload(payload)
    assert sig["alg"] == "Ed25519"
    assert verify_signature(payload, sig["signature"], alg="Ed25519")


def test_require_signing_fails_without_key(monkeypatch):
    monkeypatch.setenv("MIDTERMS_REQUIRE_SIGNING", "1")
    monkeypatch.delenv("MIDTERMS_SIGNING_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("MIDTERMS_SIGNING_KEY_PATH", raising=False)
    from midterms.ops import signing as signing_mod

    monkeypatch.setattr(signing_mod, "PRIVATE_KEY_DEFAULT_PATH", Path("/no/such/key.pem"))
    with pytest.raises(RuntimeError):
        sign_payload({"a": 1})


def test_api_auth_bearer():
    os.environ["MIDTERMS_API_KEY"] = "test-secret-key"
    # Re-import app bindings pick up env via require_api_key reading os.environ live
    from midterms.api.app import app

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/forecast/latest").status_code == 401
    ok = client.get("/forecast/latest", headers={"Authorization": "Bearer test-secret-key"})
    # 200 if artifact exists, else 404 — either proves auth passed
    assert ok.status_code in {200, 404}
    del os.environ["MIDTERMS_API_KEY"]


def test_example_licensed_schema_exists():
    p = write_example_licensed_csv()
    assert p.exists()
    assert "state,rating" in p.read_text()
