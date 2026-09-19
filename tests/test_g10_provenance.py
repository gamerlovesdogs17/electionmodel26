"""G10 independent rebuild freshness + provenance/signature honesty."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from midterms.ops.signing import (
    resolve_historical_public_key,
    sign_payload,
    verify_signature,
)
from midterms.ops.shadow_publish import verify_shadow
from midterms.validation.acceptance_gates import evaluate_acceptance_gates


def _minimal_gate_stubs(art: Path, *, forecast: dict) -> None:
    art.mkdir(parents=True, exist_ok=True)
    (art / "chamber_reconcile_latest.json").write_text(
        json.dumps({"ok": True, "gate_years": [2022], "by_cycle": {}, "failures": []}),
        encoding="utf-8",
    )
    (art / "poll_coverage_latest.json").write_text(
        json.dumps({"ok": True, "years": [2022], "failures": []}),
        encoding="utf-8",
    )
    (art / "evidence_eligibility_latest.json").write_text(
        json.dumps(
            {"ok": False, "publishable": False, "run_class": "non_publication", "reasons": ["stub"]}
        ),
        encoding="utf-8",
    )
    (art / "nested_component_loo.json").write_text(
        json.dumps(
            {
                "freeze_before_truth": True,
                "no_weight_remapping": True,
                "spine_label": "pymc",
                "hierarchical_method": "pymc",
                "crps_by_fold": {"2022": {"pymc": 4.0}},
                "g8_recommendations": {"state_space": {"recommend": "keep"}},
                "reliability": {
                    "spine": "pymc",
                    "n": 20,
                    "reliability": [],
                    "reliability_gate": {
                        "sample_sizes_disclosed": True,
                        "n_overconfident": 0,
                        "thin_sample": True,
                        "calibration_claim_allowed": False,
                    },
                    "ok": False,
                },
            }
        ),
        encoding="utf-8",
    )
    (art / "stack_weights_oof.json").write_text(
        json.dumps(
            {
                "no_weight_remapping": True,
                "source_spine_label": "pymc",
                "source_hierarchical_method": "pymc",
                "stack_weights": {"pymc": 1.0},
                "reproduction": {"ok": True},
            }
        ),
        encoding="utf-8",
    )
    (art / "forecast_latest.json").write_text(json.dumps(forecast), encoding="utf-8")
    (art / "validation_report_latest.json").write_text(
        json.dumps({"calibration": {"n": 1}, "limitations": ["stub"]}),
        encoding="utf-8",
    )


def test_g10_rejects_stale_independent_rebuild(tmp_path: Path, monkeypatch):
    art = tmp_path / "artifacts"
    forecast = {
        "run_id": "run-new",
        "generated_at": "2026-09-19T12:00:00+00:00",
        "method": "pymc",
        "run_class": "non_publication",
        "publishable": False,
        "model_version": "test",
        "numerical_quality": {"ok": True, "chamber_mcse": {"mcse_p_dem_control": 0.01}},
    }
    _minimal_gate_stubs(art, forecast=forecast)
    (art / "independent_rebuild_latest.json").write_text(
        json.dumps(
            {
                "ok": True,
                "run_id": "run-new",
                "generated_at": "2026-09-10T12:00:00+00:00",  # days before forecast
                "comparison": {},
                "domain_ok": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "midterms.ops.reproducibility.verify_rebuild",
        lambda: {"ok": True, "run_id": "run-new"},
    )
    monkeypatch.setattr(
        "midterms.ops.release_identity.verify_release_identity",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        "midterms.ops.shadow_publish.list_shadow_publications",
        lambda: [],
    )
    report = evaluate_acceptance_gates(artifacts_dir=art, write=False)
    g10 = report["gates"]["G10"]
    assert g10["ok"] is False
    detail = g10["detail"]["independent_rebuild"]
    assert detail["ok"] is False
    assert "precedes" in (detail.get("block") or "").lower() or detail["freshness"][
        "rebuild_after_forecast"
    ] is False


def test_g10_rejects_rebuild_for_different_release(tmp_path: Path, monkeypatch):
    art = tmp_path / "artifacts"
    forecast = {
        "run_id": "run-A",
        "generated_at": "2026-09-19T12:00:00+00:00",
        "method": "pymc",
        "run_class": "non_publication",
        "publishable": False,
        "model_version": "test",
        "numerical_quality": {"ok": True, "chamber_mcse": {"mcse_p_dem_control": 0.01}},
    }
    _minimal_gate_stubs(art, forecast=forecast)
    (art / "independent_rebuild_latest.json").write_text(
        json.dumps(
            {
                "ok": True,
                "run_id": "run-B",
                "generated_at": "2026-09-19T13:00:00+00:00",
                "comparison": {},
                "domain_ok": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "midterms.ops.reproducibility.verify_rebuild",
        lambda: {"ok": True, "run_id": "run-A"},
    )
    monkeypatch.setattr(
        "midterms.ops.release_identity.verify_release_identity",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        "midterms.ops.shadow_publish.list_shadow_publications",
        lambda: [],
    )
    report = evaluate_acceptance_gates(artifacts_dir=art, write=False)
    detail = report["gates"]["G10"]["detail"]["independent_rebuild"]
    assert detail["ok"] is False
    assert detail["run_id_match"] is False


def test_verify_shadow_distinguishes_hash_and_signature(tmp_path: Path):
    shadow = tmp_path / "shadow_x"
    shadow.mkdir()
    payload = {"hello": "world"}
    body = json.dumps(payload, indent=2)
    art = shadow / "artifact.json"
    art.write_bytes(body.encode("utf-8"))
    digest = hashlib.sha256(art.read_bytes()).hexdigest()
    man = {
        "shadow_id": "shadow_x",
        "file_hashes": {"artifact.json": digest},
        "mode": "test",
    }
    (shadow / "SHADOW_MANIFEST.json").write_bytes(json.dumps(man).encode("utf-8"))

    # Hashes ok, signatures missing — must not claim signature verified.
    ver = verify_shadow(path=shadow)
    assert ver["hashes_ok"] is True
    assert ver["signature_status"] == "missing"
    assert ver["signatures_ok"] is None
    assert "not" in ver["claim"].lower() or "missing" in ver["claim"].lower()

    # Correct hash but invalid signature sidecar → unverifiable when hashes hold
    # (rotated key), not an integrity "failed".
    bad_sig = {
        "alg": "Ed25519",
        "signature": base64.b64encode(b"not-a-real-signature-bytes!!!!").decode(),
        "key_id": "signing_public_key.pem",
    }
    (shadow / "artifact.json.sig.json").write_text(json.dumps(bad_sig), encoding="utf-8")
    ver2 = verify_shadow(path=shadow)
    assert ver2["hashes_ok"] is True
    assert ver2["signature_status"] == "unverifiable"
    assert ver2["signatures_ok"] is False


def test_signature_wrong_key_fails(tmp_path: Path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    # Key A signs; verify with Key B.
    priv_a = Ed25519PrivateKey.generate()
    pub_a = priv_a.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_b = Ed25519PrivateKey.generate()
    pub_b = priv_b.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    keys = tmp_path / "keys"
    keys.mkdir()
    key_a = keys / "key_a.pem"
    key_b = keys / "key_b.pem"
    key_a.write_bytes(pub_a)
    key_b.write_bytes(pub_b)

    payload = "release-artifact-v1"
    sig = base64.b64encode(priv_a.sign(payload.encode())).decode()
    assert verify_signature(payload, sig, alg="Ed25519", public_key_path=key_a) is True
    assert verify_signature(payload, sig, alg="Ed25519", public_key_path=key_b) is False

    # Historical key_id resolution prefers manifests/keys/<id>
    monkeypatch.setattr("midterms.ops.signing.MANIFESTS_DIR", tmp_path)
    (tmp_path / "keys").mkdir(exist_ok=True)
    (tmp_path / "keys" / "rotated_old.pem").write_bytes(pub_a)
    resolved = resolve_historical_public_key("rotated_old.pem")
    assert resolved is not None
    assert verify_signature(payload, sig, alg="Ed25519", public_key_path=resolved) is True


def test_altered_manifest_fails_hash_check(tmp_path: Path):
    shadow = tmp_path / "shadow_y"
    shadow.mkdir()
    body = b'{"ok": true}'
    (shadow / "artifact.json").write_bytes(body)
    man = {
        "shadow_id": "shadow_y",
        "file_hashes": {"artifact.json": "0" * 64},  # wrong
        "mode": "test",
    }
    (shadow / "SHADOW_MANIFEST.json").write_bytes(json.dumps(man).encode("utf-8"))
    ver = verify_shadow(path=shadow)
    assert ver["hashes_ok"] is False
    assert ver["mismatches"]


def test_sign_and_verify_roundtrip_with_key_id(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("midterms.ops.signing.MANIFESTS_DIR", tmp_path)
    monkeypatch.setenv("MIDTERMS_REQUIRE_SIGNING", "0")
    # Use HMAC path when no Ed25519 private key — still must report alg honestly.
    sig = sign_payload({"run_id": "x"})
    assert "alg" in sig and "sha256" in sig
    if sig["alg"] == "HMAC-SHA256":
        assert verify_signature({"run_id": "x"}, sig["signature"], alg=sig["alg"])
