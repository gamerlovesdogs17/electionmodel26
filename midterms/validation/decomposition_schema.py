"""Generic internal decomposition schema with explicit mathematical roles."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from typing import Any, Literal

TermKind = Literal[
    "additive_location",
    "posterior_location",
    "overlay_shift",
    "uncertainty_component",
    "nonlinear_joint_effect",
    "observation_location",
]
TERM_KINDS = frozenset(TermKind.__args__)


@dataclass(frozen=True)
class DiagnosticTerm:
    name: str
    value: float
    kind: TermKind
    source: str
    note: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.source or not isfinite(self.value):
            raise ValueError("diagnostic term needs a name, source, and finite value")
        if self.kind not in TERM_KINDS:
            raise ValueError(f"unsupported mathematical term type: {self.kind}")


@dataclass(frozen=True)
class Decomposition:
    subject_id: str
    base_prior: DiagnosticTerm
    prior_provenance: dict[str, Any]
    fundamentals_anchor: DiagnosticTerm
    stacked_core_location: DiagnosticTerm
    final_location: DiagnosticTerm
    final_scale: DiagnosticTerm
    terms: tuple[DiagnosticTerm, ...] = field(default_factory=tuple)
    effective_sample_size: float | None = None
    observation_count: int | None = None
    exact_overlay_chain: bool = False

    def validate(self, *, atol: float = 1e-8) -> None:
        """Check only arithmetic explicitly declared exact by the producer."""
        if not self.subject_id or not self.prior_provenance:
            raise ValueError("subject and prior provenance are required")
        required_roles = (
            (self.base_prior, "additive_location"),
            (self.fundamentals_anchor, "posterior_location"),
            (self.stacked_core_location, "posterior_location"),
            (self.final_location, "posterior_location"),
            (self.final_scale, "uncertainty_component"),
        )
        if any(term.kind != kind for term, kind in required_roles):
            raise ValueError("required diagnostic field has the wrong mathematical type")
        if self.final_scale.value <= 0:
            raise ValueError("final scale must be positive")
        if self.effective_sample_size is not None and self.effective_sample_size < 0:
            raise ValueError("effective sample size cannot be negative")
        if self.observation_count is not None and self.observation_count < 0:
            raise ValueError("observation count cannot be negative")
        if self.exact_overlay_chain:
            shifts = sum(term.value for term in self.terms if term.kind == "overlay_shift")
            expected = self.stacked_core_location.value + shifts
            if abs(self.final_location.value - expected) > atol:
                raise ValueError("final location disagrees with declared exact overlay shifts")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
