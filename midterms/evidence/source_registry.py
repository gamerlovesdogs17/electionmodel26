"""Single machine-readable registry for evidence preparation and gating."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SOURCE_PREPARATION_REGISTRY_VERSION = "source-preparation-registry-v1"
CANONICAL_DOMAIN_CONTRACT_VERSION = "canonical-domain-contract-v1"


@dataclass(frozen=True)
class SourceDomain:
    name: str
    required_for_core: bool
    required_for_historical_validation: bool
    optional_feature_dependency: str | None
    adapter: str
    refresh_supported: bool
    historical_backfill_supported: bool
    secrets_required: tuple[str, ...]
    normalized_output: str | None
    manifest_output: str | None
    freshness_policy: str
    eligibility_checker: str
    refresh_class: str = "manual"
    secrets_required_if_no_sealed_source: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SOURCE_DOMAINS: tuple[SourceDomain, ...] = (
    SourceDomain("polls", True, True, None, "midterms.evidence.ingest", True, True, (),
                 "data/normalized/polls.parquet", "data/manifests/historical_poll_cycles.json",
                 "poll-observation-and-retrieval-v1", "midterms.evidence.eligibility.audit_evidence", "safe_network"),
    SourceDomain("races", True, True, None, "midterms.evidence.official_ballot", True, True, (),
                 "data/normalized/races_official.parquet", "data/manifests/official_senate_ballots.json",
                 "official-ballot-versioned-v1", "midterms.evidence.eligibility.audit_evidence", "safe_offline"),
    SourceDomain("candidate_timeline", True, True, None, "midterms.evidence.candidate_timeline", False, True, (),
                 "data/normalized/candidate_timeline.parquet", "data/manifests/candidate_timeline.json",
                 "candidate-bitemporal-v1", "midterms.evidence.candidate_timeline.audit_candidate_timeline_history"),
    SourceDomain("pollster_ratings", True, True, None, "midterms.evidence.ratings", True, True, (),
                 "data/normalized/pollster_ratings.parquet", "data/manifests/pollster_ratings.json",
                 "rating-available-at-v1", "midterms.evidence.ratings.build_rating_lookup_with_meta", "safe_network"),
    SourceDomain("presidential_prior", True, True, None, "midterms.evidence.presidential_results", True, True, (),
                 "data/normalized/presidential_vote_counts.parquet", "data/manifests/presidential_vote_sources.json",
                 "immutable-certified-results-v1", "midterms.evidence.presidential_results.verified_source_set_sha256", "safe_offline"),
    SourceDomain("demographics", True, True, "similarity", "midterms.evidence.demographic_vintages", False, True, (),
                 "data/normalized/demographic_vintages.parquet", "data/manifests/demographic_vintages.json",
                 "official-release-date-v1", "midterms.evidence.demographic_vintages.select_demographic_vintage"),
    SourceDomain("economics", True, True, None, "midterms.evidence.economics", True, True, (),
                 "data/normalized/economics_vintages.parquet", "data/manifests/economics_vintages.json",
                 "alfred-realtime-vintage-v1", "midterms.evidence.economics.audit_realtime_economic_coverage",
                 "safe_local_or_network", ("FRED_API_KEY",)),
    SourceDomain("finance", True, True, None, "midterms.evidence.fec", True, True, (),
                 "data/normalized/fundraising_shares.parquet", "data/manifests/fundraising_shares.json",
                 "fec-filing-availability-v1", "midterms.evidence.eligibility.audit_evidence", "safe_network"),
    SourceDomain("approval", True, True, None, "midterms.evidence.approval", True, True, (),
                 "data/normalized/pres_approval.parquet", "data/manifests/pres_approval.json",
                 "approval-observation-v1", "midterms.evidence.eligibility.audit_evidence", "safe_network"),
    SourceDomain("generic_ballot", True, True, None, "midterms.evidence.ingest", True, True, (),
                 "data/normalized/polls.parquet", "data/manifests/merge_live_polls.json",
                 "generic-ballot-observation-v1", "midterms.evidence.ingest.generic_ballot_aggregate", "safe_network"),
    SourceDomain("expert_ratings", False, False, "expert_ratings_overlay", "midterms.evidence.wiki_ratings", True, False, (),
                 "data/normalized/expert_ratings.parquet", "data/manifests/expert_ratings.json",
                 "ratings-retrieval-v1", "midterms.evidence.eligibility.audit_evidence", "safe_network"),
    SourceDomain("markets", False, False, "market_overlay", "midterms.evidence.markets", True, False, (),
                 "data/normalized/markets.parquet", "data/manifests/markets_kalshi.json",
                 "market-retrieval-v1", "midterms.evidence.markets.verify_market_store_integrity", "safe_network"),
    SourceDomain("official_results", False, True, None, "midterms.evidence.official_ledger", True, True, (),
                 "data/normalized/results_certified.parquet", "data/manifests/official_senate_ballots.json",
                 "certification-availability-v1", "midterms.evidence.truth_contract.validate_truth_contract", "safe_offline"),
)


DOMAIN_CLOCKS: dict[str, dict[str, str]] = {
    "polls": {
        "availability_clock": "available_at",
        "operational_freshness_clock": "retrieved_at + field_end",
    },
    "races": {
        "availability_clock": "official ballot source available_at",
        "operational_freshness_clock": "official ballot source retrieval",
    },
    "presidential_prior": {
        "availability_clock": "presidential source available_at",
        "operational_freshness_clock": "immutable certified-result source",
    },
    "candidate_timeline": {
        "availability_clock": "event available_at",
        "operational_freshness_clock": "required identity event retrieved_at",
    },
    "finance": {
        "availability_clock": "FEC receipt date",
        "operational_freshness_clock": "source retrieved_at + latest receipt date",
    },
    "economics": {
        "availability_clock": "ALFRED realtime_start",
        "operational_freshness_clock": "source retrieved_at + observation_date",
    },
    "approval": {
        "availability_clock": "poll record available_at",
        "operational_freshness_clock": "source retrieved_at + poll observation date",
    },
    "demographics": {
        "availability_clock": "official_release_date",
        "operational_freshness_clock": "retrieved_at + official_release_date",
    },
}


def canonical_domain_contract(name: str) -> dict[str, Any]:
    """Return the single registered store, manifest, and clock contract for a domain."""
    entry = next((item for item in SOURCE_DOMAINS if item.name == name), None)
    if entry is None:
        raise KeyError(f"unknown source domain: {name}")
    clocks = DOMAIN_CLOCKS.get(name, {})
    return {
        "schema_version": CANONICAL_DOMAIN_CONTRACT_VERSION,
        "domain": name,
        "normalized_output": entry.normalized_output,
        "manifest_output": entry.manifest_output,
        "normalized_name": (
            Path(entry.normalized_output).name if entry.normalized_output else None
        ),
        "manifest_name": (
            Path(entry.manifest_output).name if entry.manifest_output else None
        ),
        **clocks,
    }


def source_preparation_registry(
    *,
    enabled_optional_features: set[str] | None = None,
) -> dict[str, Any]:
    enabled = set(enabled_optional_features or ())
    domains: dict[str, Any] = {}
    for entry in SOURCE_DOMAINS:
        row = entry.to_dict()
        dependency = entry.optional_feature_dependency
        row["enabled"] = entry.required_for_core or entry.required_for_historical_validation or (
            dependency is None or dependency in enabled
        )
        row["hard_for_current_run"] = entry.required_for_core or bool(dependency and dependency in enabled)
        row["hard_for_historical_run"] = entry.required_for_historical_validation or bool(
            dependency and dependency in enabled
        )
        domains[entry.name] = row
    return {
        "registry_version": SOURCE_PREPARATION_REGISTRY_VERSION,
        "enabled_optional_features": sorted(enabled),
        "domains": domains,
    }
