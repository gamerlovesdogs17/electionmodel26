"""Cheap CLI exit-code and workflow plumbing tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from midterms.cli import main


def test_acceptance_gates_strict_exits_nonzero(monkeypatch):
    monkeypatch.setattr(
        "midterms.validation.acceptance_gates.evaluate_acceptance_gates",
        lambda: {"ok": False, "gates": {}},
    )
    with pytest.raises(SystemExit) as exc:
        main(["acceptance-gates", "--strict"])
    assert exc.value.code == 1


def test_acceptance_gates_default_remains_report_only(monkeypatch):
    monkeypatch.setattr(
        "midterms.validation.acceptance_gates.evaluate_acceptance_gates",
        lambda: {"ok": False, "gates": {}},
    )
    main(["acceptance-gates"])


def test_evidence_eligibility_strict_exits_nonzero(monkeypatch):
    monkeypatch.setattr(
        "midterms.evidence.eligibility.write_eligibility_report",
        lambda election_id, as_of=None, domain_contract=None: {
            "publishable": False, "as_of": as_of,
        },
    )
    with pytest.raises(SystemExit) as exc:
        main(["evidence-eligibility", "--as-of", "2099-01-01", "--strict"])
    assert exc.value.code == 1


def test_evidence_preflight_uses_effective_publication_domains(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "midterms.validation.overlay_validation.publication_overlay_policy",
        lambda **kwargs: {
            "use_ratings": True,
            "use_race_markets": False,
            "use_control_market": False,
        },
    )

    def fake_report(election_id, as_of=None, domain_contract=None):
        captured.update(domain_contract or {})
        return {"publishable": True, "as_of": as_of}

    monkeypatch.setattr(
        "midterms.evidence.eligibility.write_eligibility_report", fake_report,
    )
    main(["evidence-eligibility", "--publication-config"])
    assert captured["roles"]["ratings"] == "conditionally_required"
    assert captured["roles"]["markets"] == "disabled"


def test_sealed_evidence_eligibility_cli_is_read_only(monkeypatch):
    captured = {}

    def fake_load(**kwargs):
        captured.update(kwargs)
        return (
            {"publishable": True},
            {"evidence_bundle_id": "eb-test"},
            {"ok": True, "freshness_clock": "frozen_at_evidence_seal"},
        )

    monkeypatch.setattr(
        "midterms.evidence.eligibility.load_sealed_eligibility", fake_load,
    )
    monkeypatch.setattr(
        "midterms.evidence.eligibility.write_eligibility_report",
        lambda *args, **kwargs: pytest.fail("sealed verification must not rewrite eligibility"),
    )
    main([
        "evidence-eligibility", "--election-id", "senate-2026",
        "--as-of", "2026-10-10", "--publication-config",
        "--verify-sealed", "--evidence-bundle", "bundle.json", "--strict",
    ])
    assert captured["as_of"] == "2026-10-10"
    assert captured["evidence_bundle_path"] == "bundle.json"


def test_rebuild_workflow_has_strict_preflight_current_date_and_pages_deploy():
    path = Path(".github/workflows/rebuild-research.yml")
    text = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert parsed is not None
    assert "workflow_dispatch" in (parsed.get("on") or parsed.get(True))
    assert "--as-of \"$AS_OF\"" in text
    assert "date -u +%F" in text
    assert "evidence-eligibility" in text and "--strict" in text
    assert "--publication-config" in text
    # Publication eligibility must fail closed in prepare_evidence, not only rebuild.
    prepare_job = text.index("  prepare_evidence:")
    tests_job = text.index("  tests:")
    prepare_block = text[prepare_job:tests_job]
    assert "Strict publication-domain eligibility" in prepare_block
    assert prepare_block.index("Strict publication-domain eligibility") < prepare_block.index(
        "Strict blueprint source gates"
    )
    assert "acceptance-gates --strict" in text
    assert "independent_resumed=" in text
    assert "steps.resume.outputs.independent_resumed != 'true'" in text
    assert "fetch-markets" not in text
    assert "publish-live" not in text
    assert "deploy_pages:" in text
    assert "ref: main" in text
    assert "actions/deploy-pages@v4" in text
    assert "python -m midterms.ops.large_artifacts pack" in text
    assert "python -m midterms.ops.git_push" in text
    prepare_outputs = parsed["jobs"]["prepare_evidence"]["outputs"]
    assert "multiway_withheld" in prepare_outputs
    assert "needs.prepare_evidence.outputs.multiway_withheld" in text
    assert "POST_OOF_EXCEPTIONAL_MODEL_GATE" in text
    assert "multiway_withheld_publication" in text
    # No cross-job GITHUB_ENV leakage for multiway_withheld
    assert 'echo "MULTIWAY_WITHHELD=' not in text
    assert "-c constraints/research-rebuild.txt" in text


def test_rebuild_workflow_resume_preserves_sealed_bundle_identity():
    path = Path(".github/workflows/rebuild-research.yml")
    text = path.read_text(encoding="utf-8")
    assert "inputs.resume_run_id == ''" in text
    assert "Verify existing sealed evidence for resume" in text
    assert "resume path mutated sealed warehouse inputs; refusing commit" in text
    assert "research-rebuild-${{ needs.prepare_evidence.outputs.evidence_bundle_id }}" in text
    assert "resume defaulted AS_OF from sealed evidence" in text
    # Resume verification must precede the forecast-coverage gate.
    assert text.index("Verify existing sealed evidence for resume") < text.index(
        "Forecast coverage gate"
    )
    assert "Verify sealed publication eligibility for resume" in text
    assert "--verify-sealed" in text
    prepare = text[text.index("  prepare_evidence:"):text.index("  tests:")]
    assert "Strict publication-domain eligibility" in prepare
    assert "if: inputs.resume_run_id == ''" in prepare
    resume_step = prepare[prepare.index("Verify sealed publication eligibility for resume"):]
    assert "if: inputs.resume_run_id != ''" in resume_step
    rebuild = text[text.index("  rebuild:"):]
    eligibility_step = rebuild[rebuild.index("Strict effective publication-domain eligibility"):]
    assert "--verify-sealed" in eligibility_step
