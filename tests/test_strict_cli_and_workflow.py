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
    assert "acceptance-gates --strict" in text
    assert "fetch-markets" not in text
    assert "publish-live" not in text
    assert "deploy_pages:" in text
    assert "ref: main" in text
    assert "actions/deploy-pages@v4" in text
