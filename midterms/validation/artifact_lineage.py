"""Generic identity and staleness checks for linked validation artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

FROZEN_INDEX_SEMANTIC_VERSION = "frozen_prediction_index_semantic_v1"
_NON_SEMANTIC_INDEX_FIELDS = frozenset({"path", "generated_at", "created_at"})


def frozen_index_semantic_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the prediction-bearing content of a frozen-prediction index.

    JSON whitespace, object-key order, entry order, generated timestamps, and
    filesystem paths do not identify a prediction. Every field on every entry
    does: this includes model/case identity, fit settings, evidence lineage,
    status, errors, and the per-prediction digest.
    """
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise TypeError("frozen prediction index requires an entries list")
    declared_n = payload.get("n")
    if declared_n is not None and int(declared_n) != len(entries):
        raise ValueError("frozen prediction index count does not match entries")
    if any(not isinstance(entry, Mapping) for entry in entries):
        raise ValueError("frozen prediction index entries must be objects")

    # A JSON round trip normalizes Mapping subclasses without losing any JSON
    # value. Sorting by each entry's canonical representation makes the index a
    # semantic set while retaining duplicate entries if they exist.
    normalized_entries = [
        json.loads(json.dumps(dict(entry), sort_keys=True, separators=(",", ":"),
                              allow_nan=False, default=str))
        for entry in entries
    ]
    normalized_entries.sort(
        key=lambda entry: json.dumps(
            entry, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    )
    metadata = {
        str(key): value
        for key, value in payload.items()
        if key not in {"entries", "n"} and key not in _NON_SEMANTIC_INDEX_FIELDS
    }
    return {
        "schema": FROZEN_INDEX_SEMANTIC_VERSION,
        "entries": normalized_entries,
        "metadata": metadata,
    }


def frozen_index_semantic_sha256(source: Mapping[str, Any] | Path) -> str:
    """Hash frozen predictions independent of JSON serialization details."""
    if isinstance(source, Path):
        payload = json.loads(source.read_text(encoding="utf-8"))
    else:
        payload = source
    canonical = json.dumps(
        frozen_index_semantic_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def json_text_sha256_variants(path: Path) -> set[str]:
    """Return raw and LF/CRLF-normalized digests for legacy text lineage."""
    raw = path.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    return {
        hashlib.sha256(value).hexdigest()
        for value in (raw, lf, crlf)
    }


@dataclass(frozen=True)
class ArtifactIdentity:
    run_id: str
    model_version: str
    snapshot_id: str
    evidence_sha256: str
    stack_sha256: str
    prior_sha256: str | None = None
    market_sha256: str | None = None

    def __post_init__(self) -> None:
        if not all(getattr(self, name) for name in (
            "run_id", "model_version", "snapshot_id", "evidence_sha256", "stack_sha256"
        )):
            raise ValueError("all artifact identity fields are required")
        for name in ("evidence_sha256", "stack_sha256", "prior_sha256", "market_sha256"):
            digest = getattr(self, name)
            if digest is None:
                continue
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
                raise ValueError(f"{name} must be a SHA-256 digest")

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def check_lineage(expected: ArtifactIdentity, artifacts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Reject stale or cross-run artifacts; no field is silently inferred."""
    failures: list[str] = []
    expected_fields = {key: value for key, value in asdict(expected).items() if value is not None}
    for artifact_name, payload in sorted(artifacts.items()):
        for field_name, value in expected_fields.items():
            if payload.get(field_name) != value:
                failures.append(f"{artifact_name}.{field_name} differs from expected identity")
    return {
        "ok": not failures,
        "failures": failures,
        "expected_identity_sha256": expected.fingerprint(),
        "checked_artifacts": sorted(artifacts),
    }
