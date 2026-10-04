"""Evidence eligibility tiers (audit P0.4).

Every evidence domain is classified into an explicit tier. Publishable runs
must not contain synthetic / untraceable / stale production inputs. Development
fallbacks are allowed only when the run is labeled ``non_publication``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from midterms.config import ARTIFACTS_DIR, MANIFESTS_DIR, MODEL_VERSION, NORMALIZED_DIR
from midterms.evidence.freshness import FRESHNESS_POLICY_VERSION, classify_freshness
from midterms.evidence.source_registry import canonical_domain_contract

DOMAIN_CONTRACT_VERSION = "production-domain-contract-v1"
ROLE_HARD = "required_core"
ROLE_CONDITIONAL = "conditionally_required"
ROLE_COMPARE = "compare_only"
ROLE_DISABLED = "disabled"
ROLE_QUARANTINE = "quarantine_only"


def effective_production_domain_contract(
    *, use_ratings: bool = False, use_markets: bool = False,
) -> dict[str, Any]:
    """Declare which evidence domains actually determine the requested fit."""
    roles = {
        "races": ROLE_HARD,
        "polls": ROLE_HARD,
        "structural_prior": ROLE_HARD,
        "candidate_timeline": ROLE_HARD,
        "finance": ROLE_HARD,
        "economics": ROLE_HARD,
        "approval": ROLE_HARD,
        "demographics": ROLE_HARD,
        "results": ROLE_COMPARE,
        "ratings": ROLE_CONDITIONAL if use_ratings else ROLE_DISABLED,
        "markets": ROLE_CONDITIONAL if use_markets else ROLE_DISABLED,
        "wiki_vote_scrape": ROLE_QUARANTINE,
    }
    return {
        "schema_version": DOMAIN_CONTRACT_VERSION,
        "roles": roles,
        "use_ratings": bool(use_ratings),
        "use_markets": bool(use_markets),
    }


def apply_domain_contract(
    domains: dict[str, dict[str, Any]], contract: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Attach roles and return failures only for evidence used by the fit."""
    roles = dict(contract.get("roles") or {})
    failures: list[str] = []
    out: dict[str, dict[str, Any]] = {}
    for name, original in domains.items():
        block = dict(original)
        role = roles.get(name, ROLE_COMPARE)
        block["production_role"] = role
        hard = role in {ROLE_HARD, ROLE_CONDITIONAL}
        fresh = block.get("freshness")
        freshness_required = bool(
            fresh is not None and fresh.get("required_for_effective_input", True)
        )
        freshness_ok = (
            fresh is None
            or not freshness_required
            or fresh.get("status") == "fresh"
        )
        domain_ok = bool(block.get("eligible", True)) and freshness_ok
        block["hard_dependency"] = hard
        block["effective_eligible"] = domain_ok if hard else None
        if hard and not domain_ok:
            reason = (
                block.get("blocked_reason") or block.get("reason")
                or (fresh or {}).get("status") or "ineligible"
            )
            failures.append(f"{name} domain blocked ({block.get('tier', 'unknown')}): {reason}")
        out[name] = block
    for name, role in roles.items():
        if role in {ROLE_HARD, ROLE_CONDITIONAL} and name not in out:
            failures.append(f"{name} required domain is missing")
    return out, failures


def _latest_value(frame: pd.DataFrame, column: str) -> str | None:
    if column not in frame.columns or frame.empty:
        return None
    parsed = pd.to_datetime(frame[column], errors="coerce", utc=True)
    return parsed.max().isoformat() if parsed.notna().any() else None


def _manifest_time(manifest: dict[str, Any] | None, *keys: str) -> str | None:
    if not manifest:
        return None
    for key in keys:
        value = manifest.get(key)
        if value:
            return str(value)
    nested = manifest.get("fetch_meta") or {}
    for key in keys:
        if nested.get(key):
            return str(nested[key])
    return None


def _load_manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "manifest is missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"manifest is invalid: {exc}"
    if not isinstance(payload, dict):
        return None, "manifest root must be an object"
    return payload, None


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _latest_nested_timestamp(payload: Any, key: str) -> str | None:
    """Find the latest declared source timestamp without consulting file mtimes."""
    values: list[pd.Timestamp] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                if child_key == key and child:
                    parsed = pd.to_datetime(child, errors="coerce", utc=True)
                    if pd.notna(parsed):
                        values.append(parsed)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return max(values).isoformat() if values else None


def audit_canonical_finance(
    *,
    election_id: str,
    as_of: str,
    normalized_dir: Path = NORMALIZED_DIR,
    manifests_dir: Path = MANIFESTS_DIR,
) -> dict[str, Any]:
    """Validate the receipt-safe FEC store registered for v0.9.22."""
    contract = canonical_domain_contract("finance")
    data_path = normalized_dir / str(contract["normalized_name"])
    manifest_path = manifests_dir / str(contract["manifest_name"])
    manifest, manifest_error = _load_manifest(manifest_path)
    reasons: list[str] = []
    if manifest_error:
        reasons.append(manifest_error)
    if not data_path.is_file():
        reasons.append("canonical finance store is missing")
        frame = pd.DataFrame()
    else:
        frame = pd.read_parquet(data_path)
    cutoff = pd.Timestamp(as_of).date()
    selected = frame.copy()
    if len(selected) and "election_id" in selected.columns:
        selected = selected[selected["election_id"].astype(str).eq(election_id)]
    for column in ("feature_as_of", "available_at"):
        if len(selected) and column in selected.columns:
            values = pd.to_datetime(selected[column], errors="coerce").dt.date
            selected = selected[values.notna() & (values <= cutoff)]
    if len(selected) and "feature_as_of" in selected.columns:
        latest_cutoff = pd.to_datetime(selected["feature_as_of"], errors="coerce").max()
        selected = selected[
            pd.to_datetime(selected["feature_as_of"], errors="coerce").eq(latest_cutoff)
        ]
    if not len(selected):
        reasons.append("canonical finance store has no rows available at the cutoff")
    receipt_safe = bool(
        len(frame)
        and "availability_basis" in frame.columns
        and frame["availability_basis"].astype(str).eq("fec_receipt_date").all()
    )
    if not receipt_safe:
        reasons.append("finance availability is not uniformly based on FEC receipt date")
    if manifest and manifest.get("schema_version") != "fec-report-finance-v1":
        reasons.append("finance manifest is not the receipt-safe report-level schema")
    if manifest and not bool(manifest.get("production_eligible")):
        reasons.append("finance manifest is not production eligible")
    normalized = ((manifest or {}).get("normalized") or {}).get("fundraising_shares") or {}
    expected_hash = normalized.get("sha256")
    if not expected_hash or expected_hash != _file_sha256(data_path):
        reasons.append("finance normalized artifact hash mismatch")
    retrieved_at = _latest_nested_timestamp((manifest or {}).get("sources") or [], "retrieved_at")
    observed_at = _latest_value(selected, "available_at")
    return {
        "tier": str((manifest or {}).get("tier") or "first_party"),
        "eligible": not reasons,
        "n": len(selected),
        "source_url": (manifest or {}).get("source_url"),
        "source_mix": (
            selected["source"].astype(str).value_counts().to_dict()
            if len(selected) and "source" in selected.columns else {}
        ),
        "receipt_date_safe": receipt_safe,
        "blocked_reason": "; ".join(reasons) if reasons else None,
        "canonical_source_contract": contract,
        "freshness_provenance": {
            "retrieved_at": retrieved_at,
            "observed_at": observed_at,
            "source_available": bool(manifest and len(selected)),
        },
    }


def audit_canonical_demographics(
    *,
    as_of: str,
    normalized_dir: Path = NORMALIZED_DIR,
    manifests_dir: Path = MANIFESTS_DIR,
) -> dict[str, Any]:
    """Validate and select the official Census vintage registered for v0.9.22."""
    from midterms.evidence.demographic_vintages import (
        demographic_semantic_sha256,
        select_demographic_vintage,
    )

    contract = canonical_domain_contract("demographics")
    data_path = normalized_dir / str(contract["normalized_name"])
    manifest_path = manifests_dir / str(contract["manifest_name"])
    manifest, manifest_error = _load_manifest(manifest_path)
    reasons: list[str] = []
    selected = pd.DataFrame()
    selection: dict[str, Any] = {}
    frame = pd.DataFrame()
    if manifest_error:
        reasons.append(manifest_error)
    if not data_path.is_file():
        reasons.append("canonical demographic-vintage store is missing")
    else:
        frame = pd.read_parquet(data_path)
        try:
            selected, selection = select_demographic_vintage(frame, as_of=as_of)
        except (KeyError, TypeError, ValueError) as exc:
            reasons.append(f"demographic vintage validation failed: {exc}")
    if manifest and not bool(manifest.get("production_eligible")):
        reasons.append("demographic-vintage manifest is not production eligible")
    if manifest and len(frame):
        expected = manifest.get("normalized_semantic_sha256")
        if not expected or expected != demographic_semantic_sha256(frame):
            reasons.append("demographic-vintage semantic hash mismatch")
    if not selection.get("production_eligible") or selected.empty:
        reasons.append(
            str(selection.get("reason") or "no official demographic vintage is available at cutoff")
        )
    retrieved_at = _latest_value(selected, "retrieved_at")
    observed_at = _latest_value(selected, "official_release_date")
    source_urls = (
        sorted(selected["source_url"].dropna().astype(str).unique())
        if len(selected) and "source_url" in selected.columns else []
    )
    return {
        "tier": "official",
        "eligible": not reasons,
        "n": len(selected),
        "source_url": source_urls,
        "selected_vintages": selection.get("selected_vintages") or [],
        "selected_release_date": selection.get("selected_release_date"),
        "blocked_reason": "; ".join(dict.fromkeys(reasons)) if reasons else None,
        "canonical_source_contract": contract,
        "freshness_provenance": {
            "retrieved_at": retrieved_at,
            "observed_at": observed_at,
            "source_available": bool(manifest and len(selected)),
        },
    }


def audit_canonical_approval(
    *,
    election_id: str,
    as_of: str,
    normalized_dir: Path = NORMALIZED_DIR,
    manifests_dir: Path = MANIFESTS_DIR,
) -> dict[str, Any]:
    """Validate the current-cycle approval input and its point-in-time clock."""
    contract = canonical_domain_contract("approval")
    data_path = normalized_dir / str(contract["normalized_name"])
    manifest_path = manifests_dir / str(contract["manifest_name"])
    manifest, manifest_error = _load_manifest(manifest_path)
    reasons: list[str] = []
    if manifest_error:
        reasons.append(manifest_error)
    frame = pd.read_parquet(data_path) if data_path.is_file() else pd.DataFrame()
    if frame.empty:
        reasons.append("canonical approval store is missing or empty")
    year = int(str(election_id).rsplit("-", 1)[-1])
    selected = frame.copy()
    required_columns = {"year", "available_at", "source", "production_eligible"}
    missing_columns = sorted(required_columns - set(selected.columns))
    if len(selected) and missing_columns:
        reasons.append("approval store missing columns: " + ", ".join(missing_columns))
        selected = selected.iloc[0:0]
    elif len(selected):
        years = pd.to_numeric(selected["year"], errors="coerce")
        available = pd.to_datetime(selected["available_at"], errors="coerce").dt.date
        selected = selected[
            (years == year)
            & available.notna()
            & (available <= pd.Timestamp(as_of).date())
        ]
    if selected.empty:
        reasons.append("approval store has no source-backed current-cycle row at the cutoff")
    elif (
        "production_eligible" not in selected.columns
        or not selected["production_eligible"].fillna(False).astype(bool).all()
    ):
        reasons.append("current-cycle approval rows are not production eligible")
    expected_hash = (manifest or {}).get("normalized_sha256")
    if not expected_hash or expected_hash != _file_sha256(data_path):
        reasons.append("approval normalized artifact hash mismatch")
    retrieved_at = _latest_value(selected, "retrieved_at") or _manifest_time(
        manifest, "generated_at", "retrieved_at"
    )
    observed_at = _latest_value(selected, "max_poll_end") or _latest_value(
        selected, "available_at"
    )
    return {
        "tier": str((manifest or {}).get("tier") or "untraceable"),
        "eligible": not reasons,
        "n": len(selected),
        "source_url": (manifest or {}).get("source_url"),
        "blocked_reason": "; ".join(dict.fromkeys(reasons)) if reasons else None,
        "canonical_source_contract": contract,
        "freshness_provenance": {
            "retrieved_at": retrieved_at,
            "observed_at": observed_at,
            "source_available": bool(manifest and len(selected)),
        },
    }


def domain_freshness_from_provenance(
    domain: str,
    *,
    checked_at: str,
    manifest: dict[str, Any] | None = None,
    retrieved_at: str | None = None,
    observed_at: str | None = None,
    source_available: bool = True,
) -> dict[str, Any]:
    """Classify provenance timestamps without consulting filesystem mtimes."""
    parser_status = "ok"
    refresh_status = "ok"
    schema_status = "ok"
    if manifest:
        if manifest.get("fetch_error") or manifest.get("refresh_error"):
            refresh_status = str(manifest.get("fetch_error") or manifest.get("refresh_error"))
        if manifest.get("parser_status") not in (None, "ok"):
            parser_status = str(manifest["parser_status"])
        if manifest.get("schema_status") not in (None, "ok"):
            schema_status = str(manifest["schema_status"])
    return classify_freshness(
        domain, checked_at=checked_at,
        retrieved_at=retrieved_at or _manifest_time(
            manifest, "retrieved_at", "retrieval_date", "generated_at", "available_at"
        ),
        observed_at=observed_at or _manifest_time(
            manifest, "observed_at", "data_through", "latest_observation", "available_at"
        ),
        source_available=source_available,
        refresh_status=refresh_status,
        parser_status=parser_status,
        schema_status=schema_status,
    )


def candidate_timeline_freshness(
    audit: dict[str, Any],
    metadata: dict[str, Any] | None,
    *,
    checked_at: str,
) -> dict[str, Any]:
    """Require operational freshness only when candidate identity is an input."""
    identity_required = int(audit.get("n_identity_required_and_resolved") or 0) + int(
        audit.get("n_identity_required_and_missing") or 0
    )
    if audit.get("conditional_identity_contract") and identity_required == 0:
        return {
            "policy_version": FRESHNESS_POLICY_VERSION,
            "domain": "candidate_ballot",
            "status": "not_applicable",
            "source_available": True,
            "reasons": [
                "no current race requires candidate identity under the conditional contract"
            ],
            "retrieval_age_days": None,
            "observation_age_days": None,
            "required_for_effective_input": False,
        }
    meta = metadata or {}
    result = domain_freshness_from_provenance(
        "candidate_ballot",
        checked_at=checked_at,
        retrieved_at=meta.get("latest_retrieved_at"),
        observed_at=meta.get("latest_effective_at"),
        source_available=bool(identity_required),
    )
    result["required_for_effective_input"] = True
    result["identity_required_races"] = identity_required
    return result

# Ordered from strongest to weakest
TIERS = (
    "official",
    "first_party",
    "aggregator",
    "curated",
    "imputed",
    "synthetic",
    "untraceable",
)

# Curated is research-traceable but not publication-eligible by default (A-04).
PUBLICATION_ELIGIBLE = frozenset({"official", "first_party", "aggregator"})
PUBLICATION_BLOCKED = frozenset({"synthetic", "imputed", "untraceable", "curated"})


def classify_poll_row(row: dict[str, Any] | pd.Series) -> str:
    get = row.get if isinstance(row, dict) else lambda k, d=None: row[k] if k in row.index else d
    url = str(get("source_url") or "").lower()
    parser = str(get("parser_version") or "").lower()
    if "synthetic" in url or parser.startswith("fixtures"):
        return "synthetic"
    if "fivethirtyeight" in url or "datasette" in url or parser.startswith("fte-"):
        return "aggregator"
    if "votehub" in url or parser.startswith("votehub"):
        return "aggregator"
    if url.startswith("http") and url not in {"nan", "none", ""}:
        # FTE-normalized rows often keep the pollster's primary URL
        if parser.startswith("fte-"):
            return "aggregator"
        return "first_party"
    if not url or url in {"nan", "none", "null"}:
        return "untraceable"
    return "curated"


def classify_result_row(row: dict[str, Any] | pd.Series) -> str:
    get = row.get if isinstance(row, dict) else lambda k, d=None: row[k] if k in row.index else d
    url = str(get("source_url") or "").lower()
    if "synthetic" in url:
        return "synthetic"
    if "certified" in url or "medsl" in url or "harvard" in url or "dataverse" in url:
        return "official"
    if url.startswith("http"):
        return "curated"
    # Prefer certified archive presence via results_certified merge provenance
    if get("release_version") is not None and url and "synthetic" not in url:
        return "curated"
    if not url or url in {"nan", "none"}:
        return "untraceable"
    return "curated"


def classify_race_election(election_id: str, races: pd.DataFrame | None = None) -> str:
    """Historical official ballots are official; 2026 uses Wikipedia/Class-II schedule."""
    eid = str(election_id)
    if eid == "senate-2026":
        # Prospective cycle: contested Class II (+ documented specials) from the
        # public Wikipedia Senate elections page / constitutional class schedule
        # is aggregator-grade when stamped; bare fixture generator stays curated.
        if races is not None and len(races) and "ballot_source" in races.columns:
            src = races["ballot_source"].dropna().astype(str)
            if len(src) and src.str.startswith(("wikipedia", "constitutional", "aggregator")).any():
                return "aggregator"
        man_path = MANIFESTS_DIR / "official_senate_ballots.json"
        if man_path.exists():
            try:
                man = json.loads(man_path.read_text(encoding="utf-8"))
                if str(man.get("tier_2026") or "") in {"aggregator", "official"}:
                    return str(man["tier_2026"])
            except Exception:  # noqa: BLE001,S110
                pass
        return "curated"
    path = NORMALIZED_DIR / "races_official.parquet"
    if path.exists():
        return "official"
    if races is not None and len(races) and "seat_class" in races.columns:
        return "curated"
    return "untraceable"


def _tier_counts(series: pd.Series) -> dict[str, int]:
    vc = series.value_counts()
    return {str(k): int(v) for k, v in vc.items()}


def audit_structural_prior(races: pd.DataFrame) -> dict[str, Any]:
    """A raw source manifest alone cannot certify a derived model input."""
    required = ("prior_source", "prior_provenance_sha256", "prior_production_eligible")
    missing = [name for name in required if name not in races.columns]
    if missing:
        return {"eligible": False, "blocked_n": len(races),
                "reason": f"structural prior metadata missing: {', '.join(missing)}"}
    active = races[~races["not_up"].fillna(False)] if "not_up" in races.columns else races
    valid_sha = active["prior_provenance_sha256"].astype(str).str.fullmatch(r"[0-9a-fA-F]{64}")
    valid = (
        active["prior_source"].eq("observed_presidential_relative_v1")
        & active["prior_production_eligible"].eq(True)
        & valid_sha
    )
    blocked = int((~valid).sum())
    return {"eligible": blocked == 0, "blocked_n": blocked,
            "reason": None if blocked == 0 else f"{blocked} structural priors lack verified derived provenance"}


def classify_polls(polls: pd.DataFrame) -> pd.Series:
    if polls is None or polls.empty:
        return pd.Series(dtype=str)
    return polls.apply(classify_poll_row, axis=1)


def _classify_manifest_domain(
    *,
    name: str,
    manifest: dict[str, Any] | None,
    fixture_markers: tuple[str, ...] = ("fixture", "FIXTURE", "synthetic"),
) -> dict[str, Any]:
    """Classify a configured production domain from its manifest / store."""
    if not manifest:
        return {
            "tier": "untraceable",
            "eligible": False,
            "n": 0,
            "reason": f"missing {name} manifest",
        }
    blob = json.dumps(manifest, default=str)
    declared = str(manifest.get("tier") or "").strip().lower()
    # Respect an explicit declared tier; never upgrade curated/hand-entered via URL (A-04).
    tier = declared if declared in TIERS else "curated"
    blocked_reason = None
    # Explicit fixture / synthetic markers
    if any(m in blob for m in fixture_markers):
        # Count fixture-dominated source mixes
        mix = manifest.get("source_mix") or {}
        if isinstance(mix, dict) and mix:
            fixture_n = int(mix.get("fixture_hash") or mix.get("fixture") or 0)
            total_n = sum(int(v) for v in mix.values() if isinstance(v, (int, float)))
            if total_n > 0 and fixture_n >= total_n:
                tier = "synthetic"
                blocked_reason = f"{name} source_mix entirely fixture-backed"
            elif fixture_n > 0:
                tier = "synthetic"
                blocked_reason = f"{name} includes fixture_hash inputs ({fixture_n}/{total_n})"
        series = manifest.get("series") or []
        if any("FIXTURE" in str(s) for s in series):
            tier = "synthetic"
            blocked_reason = f"{name} series includes *_FIXTURE"
        if (
            "fixture" in blob.lower()
            and tier != "synthetic"
            and ("RDPI_YOY_FIXTURE" in blob or "fixture_hash" in blob)
        ):
            tier = "synthetic"
            blocked_reason = blocked_reason or f"{name} fixture markers present"
    n = int(
        manifest.get("n_shares")
        or manifest.get("n_rows")
        or manifest.get("n_races")
        or manifest.get("n")
        or len(manifest.get("rows") or [])
        or 0
    )
    eligible = tier in PUBLICATION_ELIGIBLE and blocked_reason is None
    out = {
        "tier": tier if blocked_reason is None else "synthetic",
        "eligible": eligible and blocked_reason is None,
        "n": n,
        "blocked_reason": blocked_reason,
    }
    if blocked_reason:
        out["tier"] = "synthetic"
        out["eligible"] = False
    elif tier not in PUBLICATION_ELIGIBLE:
        out["eligible"] = False
        out["blocked_reason"] = out.get("blocked_reason") or f"{name} tier={tier} not publication-eligible"
    return out


def _audit_configured_domains(
    *, as_of: str | None = None, election_id: str = "senate-2026",
) -> dict[str, Any]:
    """All-domain evidence registry (fresh audit R-04)."""
    domains: dict[str, Any] = {}

    def _load(name: str) -> dict[str, Any] | None:
        path = MANIFESTS_DIR / name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    if as_of:
        domains["finance"] = audit_canonical_finance(
            election_id=election_id,
            as_of=as_of,
        )
    else:
        fund = _load("fundraising_shares.json")
        domains["finance"] = _classify_manifest_domain(name="finance", manifest=fund)

    # Quarantine: Wikipedia scrape must never appear as canonical results truth.
    from midterms.config import RAW_DIR
    from midterms.evidence.truth_contract import WIKI_QUARANTINE_LABEL

    wiki_path = RAW_DIR / "external" / "certified_vote_counts.json"
    domains["wiki_vote_scrape"] = {
        "tier": "synthetic",
        "eligible": False,
        "n": 1 if wiki_path.exists() else 0,
        "quarantine": WIKI_QUARANTINE_LABEL,
        "path": str(wiki_path.as_posix()) if wiki_path.exists() else None,
        "note": "parser_development_only — not canonical truth (v0.9.21)",
    }

    econ = _load("economics_vintages.json")
    if econ and (econ.get("production_series") or []) and bool(econ.get("publication_eligible")):
        domains["economics"] = {
            "tier": str(econ.get("tier") or "first_party"),
            "eligible": True,
            "n": int(econ.get("n_rows") or 0),
            "production_series": econ.get("production_series"),
            "source_url": econ.get("source_url"),
            "note": "production YoY present; fixture series retained only as leakage canaries",
        }
    else:
        domains["economics"] = _classify_manifest_domain(name="economics", manifest=econ)

    if as_of:
        domains["approval"] = audit_canonical_approval(
            election_id=election_id,
            as_of=as_of,
        )
    else:
        approval = _load("pres_approval.json")
        domains["approval"] = _classify_manifest_domain(name="approval", manifest=approval)

    # v0.9.22 uses the cycle-aware official Census vintage store. The legacy
    # demography store remains readable for development but is never canonical.
    if as_of:
        domains["demographics"] = audit_canonical_demographics(as_of=as_of)
    else:
        domains["demographics"] = {
            "tier": "untraceable",
            "eligible": False,
            "n": 0,
            "blocked_reason": "demographic eligibility requires an explicit as_of cutoff",
            "canonical_source_contract": canonical_domain_contract("demographics"),
        }

    ratings = _load("expert_ratings.json") or _load("wiki_ratings.json") or _load("peer_snapshots.json")
    for cand in ("expert_ratings.json", "wiki_ratings.json", "ratings.json"):
        if (MANIFESTS_DIR / cand).exists():
            ratings = _load(cand)
            break
    if ratings and str(ratings.get("tier") or "") in PUBLICATION_ELIGIBLE:
        domains["ratings"] = {
            "tier": str(ratings.get("tier")),
            "eligible": True,
            "n": int(ratings.get("n") or 0),
            "source_url": ratings.get("source_url") or ratings.get("page_url"),
            "note": ratings.get("note") or ratings.get("source"),
        }
    elif ratings is None and (NORMALIZED_DIR / "expert_ratings.parquet").exists():
        domains["ratings"] = {
            "tier": "curated",
            "eligible": False,
            "n": len(pd.read_parquet(NORMALIZED_DIR / "expert_ratings.parquet")),
            "blocked_reason": "expert_ratings.parquet tier=curated not publication-eligible",
            "note": "expert_ratings.parquet present",
        }
    else:
        domains["ratings"] = _classify_manifest_domain(name="ratings", manifest=ratings or {})
        if ratings is None:
            domains["ratings"] = {
                "tier": "curated",
                "eligible": True,
                "n": 0,
                "note": "optional overlay; absent is allowed if with_ratings disabled",
            }

    markets = _load("markets_kalshi.json")
    from midterms.evidence.markets import verify_market_store_integrity

    market_integrity = verify_market_store_integrity(as_of=as_of)
    if not market_integrity["ok"]:
        domains["markets"] = {
            "tier": "untraceable", "eligible": False,
            "n": int((markets or {}).get("n_races") or 0),
            "blocked_reason": str(market_integrity["reason"]),
        }
    else:
        domains["markets"] = {
            **_classify_manifest_domain(name="markets", manifest=markets),
            "mapping_integrity": market_integrity,
        }

    return domains


def audit_evidence(
    *,
    election_id: str,
    polls: pd.DataFrame | None = None,
    races: pd.DataFrame | None = None,
    results: pd.DataFrame | None = None,
    max_poll_age_days: float | None = 21.0,
    as_of: str | None = None,
    domain_contract: dict[str, Any] | None = None,
    candidate_timeline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Classify warehouse evidence for an election and decide publishability.

    Returns a structured report with ``publishable`` and ``run_class``.
    """
    from midterms.evidence.warehouse import Warehouse

    wh = None
    snapshot = None
    if polls is None or races is None or results is None:
        wh = Warehouse(ensure_fixtures=False)
        if as_of:
            snapshot = wh.build_as_of(as_of, election_id)
        polls = polls if polls is not None else (snapshot.polls if snapshot else wh.polls)
        races = races if races is not None else (snapshot.races if snapshot else wh.races)
        results = results if results is not None else wh.results
        candidate_timeline = candidate_timeline or (
            snapshot.candidate_timeline if snapshot else None
        )

    polls_e = polls[polls["election_id"].astype(str) == election_id].copy() if len(polls) else polls
    races_e = races[races["election_id"].astype(str) == election_id].copy() if len(races) else races
    results_e = (
        results[results["election_id"].astype(str) == election_id].copy() if len(results) else results
    )

    poll_tiers = classify_polls(polls_e) if len(polls_e) else pd.Series(dtype=str)
    result_tiers = (
        results_e.apply(classify_result_row, axis=1) if len(results_e) else pd.Series(dtype=str)
    )
    race_tier = classify_race_election(election_id, races_e)

    reasons: list[str] = []
    domains: dict[str, Any] = {
        "races": {
            "tier": race_tier,
            "n": len(races_e),
            "eligible": race_tier in PUBLICATION_ELIGIBLE,
        },
        "polls": {
            "n": len(polls_e),
            "tier_counts": _tier_counts(poll_tiers) if len(poll_tiers) else {},
            "blocked_n": int(poll_tiers.isin(list(PUBLICATION_BLOCKED)).sum())
            if len(poll_tiers)
            else 0,
            "eligible_n": int(poll_tiers.isin(list(PUBLICATION_ELIGIBLE)).sum())
            if len(poll_tiers)
            else 0,
            "eligible": False,
        },
        "results": {
            "n": len(results_e),
            "tier_counts": _tier_counts(result_tiers) if len(result_tiers) else {},
            "blocked_n": int(result_tiers.isin(list(PUBLICATION_BLOCKED)).sum())
            if len(result_tiers)
            else 0,
        },
    }
    prior_error = None
    if as_of:
        try:
            from midterms.evidence.presidential_prior import (
                attach_prior_snapshot,
                materialize_prior_snapshot,
            )

            prior_snapshot, _ = materialize_prior_snapshot(pd.Timestamp(as_of).date())
            races_e = attach_prior_snapshot(races_e, prior_snapshot)
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            prior_error = str(exc)
    prior_audit = audit_structural_prior(races_e)
    if prior_error:
        prior_audit = {"eligible": False, "blocked_n": len(races_e),
                       "reason": f"structural prior source verification failed: {prior_error}"}
    domains["structural_prior"] = prior_audit
    if not prior_audit["eligible"]:
        reasons.append(str(prior_audit["reason"]))

    from midterms.evidence.candidate_timeline import audit_candidate_timeline

    domains["candidate_timeline"] = audit_candidate_timeline(
        races_e, candidate_timeline,
    )

    # Fresh audit R-04: every configured live domain
    extra = _audit_configured_domains(as_of=as_of, election_id=election_id)
    domains.update(extra)
    canonical_contract_names = {
        "polls": "polls",
        "races": "races",
        "structural_prior": "presidential_prior",
        "candidate_timeline": "candidate_timeline",
        "finance": "finance",
        "economics": "economics",
        "approval": "approval",
        "demographics": "demographics",
    }
    for domain_name, registry_name in canonical_contract_names.items():
        if domain_name in domains:
            contract_block = canonical_domain_contract(registry_name)
            if domain_name == "structural_prior":
                contract_block = {**contract_block, "domain": "structural_prior"}
            domains[domain_name].setdefault(
                "canonical_source_contract", contract_block,
            )

    # Poll eligibility: target election must not be majority synthetic/untraceable
    n_polls = len(polls_e)
    blocked_polls = int(domains["polls"]["blocked_n"])
    if n_polls == 0:
        reasons.append("no polls for election_id")
    elif blocked_polls > 0 and blocked_polls >= max(1, int(0.05 * n_polls)):
        reasons.append(
            f"polls include blocked tiers ({blocked_polls}/{n_polls}): "
            f"{domains['polls']['tier_counts']}"
        )
    if race_tier not in PUBLICATION_ELIGIBLE:
        reasons.append(f"race universe tier={race_tier} not publication-eligible")
    domains["polls"]["eligible"] = n_polls > 0 and blocked_polls < max(
        1, int(0.05 * n_polls)
    )
    if election_id == "senate-2026":
        try:
            from midterms.evidence.ingest import verify_votehub_poll_lineage

            domains["polls"]["source_lineage"] = verify_votehub_poll_lineage()
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            domains["polls"]["source_lineage"] = {"ok": False, "error": str(exc)}
            domains["polls"]["eligible"] = False
            reasons.append(f"current poll source lineage failed: {exc}")

    # Staleness for live cycle
    stale = False
    max_age_h = None
    if election_id == "senate-2026" and n_polls and "field_end" in polls_e.columns:
        ends = pd.to_datetime(polls_e["field_end"], errors="coerce")
        if ends.notna().any():
            latest = ends.max()
            ref = pd.Timestamp(as_of) if as_of else pd.Timestamp.now(tz="UTC").tz_localize(None)
            if getattr(ref, "tzinfo", None) is not None:
                ref = ref.tz_localize(None)
            age_days = float((ref - latest).total_seconds() / 86400.0)
            max_age_h = age_days * 24.0
            if max_poll_age_days is not None and age_days > float(max_poll_age_days):
                stale = True
                reasons.append(
                    f"live polls stale: latest field_end age {age_days:.1f}d > {max_poll_age_days}d"
                )
    domains["polls"]["stale"] = stale
    domains["polls"]["latest_age_hours"] = max_age_h
    # Operational freshness is a current-cycle concern. Historical replay uses
    # point-in-time availability/vintage gates; old observations are expected
    # and must not be mislabeled as an operational outage.
    if as_of and election_id == "senate-2026":
        freshness_checked_at = datetime.now(UTC).isoformat()
        domains["polls"]["freshness"] = domain_freshness_from_provenance(
            "polls", checked_at=freshness_checked_at,
            retrieved_at=_latest_value(polls_e, "retrieved_at"),
            observed_at=_latest_value(polls_e, "field_end"),
            source_available=bool(n_polls),
        )
        timeline_meta = candidate_timeline or {}
        domains["candidate_timeline"]["freshness"] = candidate_timeline_freshness(
            domains["candidate_timeline"],
            timeline_meta,
            checked_at=freshness_checked_at,
        )

        manifest_names = {
            "finance": "fundraising_shares.json",
            "economics": "economics_vintages.json",
            "approval": "pres_approval.json",
            "demographics": canonical_domain_contract("demographics")["manifest_name"],
            "ratings": "expert_ratings.json",
            "markets": "markets_kalshi.json",
        }
        freshness_names = {
            "finance": "finance", "economics": "economics",
            "approval": "approval", "demographics": "demographics",
            "ratings": "ratings", "markets": "markets",
        }
        observation_sources = {
            "economics": ("economics_vintages.parquet", "observation_date"),
            "ratings": ("expert_ratings.parquet", "available_at"),
        }
        for name, filename in manifest_names.items():
            path = MANIFESTS_DIR / filename
            manifest = None
            if path.exists():
                try:
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    manifest = {"parser_status": "manifest_json_invalid"}
            observed_at = None
            source = observation_sources.get(name)
            if source and (NORMALIZED_DIR / source[0]).exists():
                observed_frame = pd.read_parquet(
                    NORMALIZED_DIR / source[0], columns=[source[1]],
                )
                observed_at = _latest_value(observed_frame, source[1])
            provenance = domains[name].get("freshness_provenance") or {}
            domains[name]["freshness"] = domain_freshness_from_provenance(
                freshness_names[name], checked_at=freshness_checked_at, manifest=manifest,
                retrieved_at=provenance.get("retrieved_at"),
                observed_at=provenance.get("observed_at") or observed_at,
                source_available=bool(
                    provenance.get("source_available", manifest is not None)
                ),
            )

    contract = domain_contract or effective_production_domain_contract()
    domains, contract_failures = apply_domain_contract(domains, contract)
    # Domain-role evaluation supersedes unconditional optional-domain blocking.
    reasons = [reason for reason in reasons if "domain blocked" not in reason]
    reasons.extend(contract_failures)
    reasons = list(dict.fromkeys(reasons))

    chamber_ok = None
    coverage_ok = None
    try:
        from midterms.validation.chamber_reconcile import GATE_YEARS, reconcile_cycle

        year = int(election_id.split("-")[-1])
        if year in GATE_YEARS:
            chamber_ok = bool(reconcile_cycle(year, races=races, results=results).get("ok"))
            if not chamber_ok:
                reasons.append("chamber reconcile failed for gate year")
    except Exception as exc:  # noqa: BLE001
        chamber_ok = None
        domains["chamber_reconcile_error"] = str(exc)

    if election_id != "senate-2026":
        try:
            from midterms.validation.poll_coverage import poll_coverage_report

            year = int(election_id.split("-")[-1])
            if year >= 2018:
                coverage_ok = bool(poll_coverage_report(year, polls=polls_e).get("ok"))
                if not coverage_ok:
                    reasons.append("poll coverage gate failed")
        except Exception as exc:  # noqa: BLE001
            coverage_ok = None
            domains["poll_coverage_error"] = str(exc)

    publishable = len(reasons) == 0 and n_polls > 0
    run_class = "publication" if publishable else "non_publication"
    report = {
        "model_version": MODEL_VERSION,
        "ok": publishable,
        "publishable": publishable,
        "run_class": run_class,
        "election_id": election_id,
        "as_of": as_of,
        "reasons": reasons,
        "domains": domains,
        "effective_domain_contract": contract,
        "chamber_reconcile_ok": chamber_ok,
        "poll_coverage_ok": coverage_ok,
        "tiers": list(TIERS),
        "publication_eligible_tiers": sorted(PUBLICATION_ELIGIBLE),
        "publication_blocked_tiers": sorted(PUBLICATION_BLOCKED),
        "note": (
            "Fresh audit R-04: publishable runs reject synthetic/imputed/untraceable "
            "evidence across races/polls/results/finance/economics/approval/ratings/markets."
        ),
        "checked_at": datetime.now(UTC).isoformat(),
    }
    return report


def write_eligibility_report(
    election_id: str = "senate-2026",
    *,
    as_of: str | None = None,
    run_id: str | None = None,
    snapshot_id: str | None = None,
    forecast_generated_at: str | None = None,
    domain_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = audit_evidence(
        election_id=election_id, as_of=as_of, domain_contract=domain_contract,
    )
    from midterms.ops.run_coherence import stamp_eligibility_identity

    report = stamp_eligibility_identity(
        report,
        run_id=run_id,
        snapshot_id=snapshot_id,
        forecast_generated_at=forecast_generated_at,
    )
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / "evidence_eligibility_latest.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    (MANIFESTS_DIR / "evidence_eligibility.json").write_text(
        json.dumps(report, indent=2, default=str)
    )
    report["path"] = str(path)
    return report


def assert_publishable(
    election_id: str,
    *,
    as_of: str | None = None,
    allow_non_publication: bool = True,
    domain_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Evaluate eligibility.

    If not publishable and ``allow_non_publication`` is False, raise.
    Otherwise return the report (caller must stamp run_class on the artifact).
    """
    report = audit_evidence(
        election_id=election_id, as_of=as_of, domain_contract=domain_contract,
    )
    if not report["publishable"] and not allow_non_publication:
        raise ValueError(
            "Evidence not publication-eligible for "
            f"{election_id}: {'; '.join(report.get('reasons') or [])}"
        )
    return report
