"""Windows-safe forecast run ids for release archives."""

from __future__ import annotations

from midterms.pipeline.run_forecast import compact_run_id


def test_compact_run_id_shortens_exceptional_method_suffixes():
    long_method = (
        "ensemble_stack"
        "+limited_validation_exception_model-v1"
        "+limited_validation_alaska_rcv_model-v1"
    )
    legacy = (
        f"senate-2026_2026-10-05_{long_method}_20260901"
    )
    compact = compact_run_id(
        "senate-2026", "2026-10-05", long_method, 20260901,
    )
    assert len(compact) < len(legacy)
    assert len(compact) <= 80
    assert "limited_validation_alaska_rcv_model-v1" not in compact
    assert compact.startswith("senate-2026_2026-10-05_ensemble_stack_")

    # Nested release draws path must stay under typical Windows MAX_PATH budget.
    repo_prefix = r"C:\Users\zydlo\OneDrive\Desktop\electionmodel26"
    nested = (
        f"{repo_prefix}\\data\\releases\\{compact}\\draws_{compact}.npz"
    )
    assert len(nested) < 260


def test_compact_run_id_stable_for_same_method():
    method = "ensemble_stack+limited_validation_exception_model-v1"
    a = compact_run_id("senate-2026", "2026-10-05", method, 7)
    b = compact_run_id("senate-2026", "2026-10-05", method, 7)
    c = compact_run_id("senate-2026", "2026-10-05", method + "+x", 7)
    assert a == b
    assert a != c
