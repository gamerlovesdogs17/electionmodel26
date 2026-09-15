"""Reseal: independent rebuild + acceptance gates, then optional publish-live."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    from midterms.ops.public_publish import assert_public_ready, publish_live
    from midterms.ops.reproducibility import independent_rebuild, verify_rebuild
    from midterms.validation.acceptance_gates import evaluate_acceptance_gates

    art = json.loads(Path("data/artifacts/forecast_latest.json").read_text(encoding="utf-8"))
    print("version", art.get("model_version"))
    print("run_id", art.get("run_id"))
    print(
        "surface",
        art.get("publication_surface"),
        "publishable",
        art.get("publishable"),
        "run_class",
        art.get("run_class"),
    )
    print("nq_ok", (art.get("numerical_quality") or {}).get("ok"))
    print(
        "chamber",
        {
            k: art["chamber"][k]
            for k in ("p_dem_majority", "expected_dem_seats", "p_fifty_fifty")
        },
    )
    print("lite_rebuild", verify_rebuild().get("ok"))
    ind = independent_rebuild()
    print("independent", ind.get("ok"), ind.get("failures") or ind.get("notes"))
    Path("data/artifacts/independent_rebuild_latest.json").write_text(
        json.dumps(ind, indent=2, default=str),
        encoding="utf-8",
    )
    gates = evaluate_acceptance_gates(write=True)
    print(
        "gates",
        gates["ok"],
        f"{gates['n_pass']}/11",
        "fail",
        gates.get("failures"),
        "partial",
        gates.get("partials"),
    )
    # Reload artifact after rebuild may have left latest untouched (rebuild_mode uses temp)
    art = json.loads(Path("data/artifacts/forecast_latest.json").read_text(encoding="utf-8"))
    ready = assert_public_ready(artifact=art, gates=gates)
    print("public_ready", ready.get("ok"), ready.get("reasons"))
    if not ready.get("ok"):
        return 2
    if "--publish" in sys.argv:
        try:
            out = publish_live(seal_shadow=True, sync_web=True)
        except OSError as exc:
            # Windows OneDrive / mapped-section lock on web copy — retry without web sync
            print("publish_web_copy_failed", exc)
            out = publish_live(seal_shadow=True, sync_web=False)
            # Best-effort web sync
            try:
                import shutil
                from midterms.config import ROOT

                src = Path("data/artifacts/forecast_latest.json")
                dst = ROOT / "web" / "public" / "data" / "forecast_latest.json"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                out["web_sync_retry"] = True
            except Exception as exc2:  # noqa: BLE001
                out["web_sync_retry"] = False
                out["web_sync_error"] = str(exc2)
        print("published", json.dumps(out, indent=2, default=str)[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
