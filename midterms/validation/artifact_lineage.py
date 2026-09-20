"""Generic identity and staleness checks for linked validation artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ArtifactIdentity:
    run_id: str
    model_version: str
    snapshot_id: str
    evidence_sha256: str
    stack_sha256: str

    def __post_init__(self) -> None:
        if not all(asdict(self).values()):
            raise ValueError("all artifact identity fields are required")
        for name in ("evidence_sha256", "stack_sha256"):
            digest = getattr(self, name)
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
                raise ValueError(f"{name} must be a SHA-256 digest")

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def check_lineage(expected: ArtifactIdentity, artifacts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Reject stale or cross-run artifacts; no field is silently inferred."""
    failures: list[str] = []
    expected_fields = asdict(expected)
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
