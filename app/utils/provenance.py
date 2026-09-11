"""Source provenance for every fact the engine uses.

The engine distinguishes five kinds of information and never blurs them:

=================  ==============================================================
``pitch_deck``      read from the uploaded deck, with its slide/page location
``user_supplied``   entered or corrected by the user
``external_source`` taken from an approved investor dataset or data provider
``inference``       derived by the engine (classification, derivation) - labeled
``missing``         not found; the value is ``None``
=================  ==============================================================
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")

SourceType = Literal["pitch_deck", "user_supplied", "external_source", "inference", "missing"]

SOURCE_LABELS: dict[str, str] = {
    "pitch_deck": "Pitch deck",
    "user_supplied": "User supplied",
    "external_source": "External data",
    "inference": "Analyst inference",
    "missing": "Missing",
}

#: When two candidates exist for one field, the higher priority wins; confidence breaks ties.
#: A user correction always beats the deck, and any stated fact beats an inference.
SOURCE_PRIORITY: dict[str, int] = {
    "user_supplied": 4,
    "pitch_deck": 3,
    "external_source": 2,
    "inference": 1,
    "missing": 0,
}

EVIDENCE_MAX_CHARS = 240


class Sourced(BaseModel, Generic[T]):
    """A value plus where it came from and how sure we are."""

    value: T | None = None
    source: SourceType = "missing"
    location: str | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    evidence: str | None = None
    method: str | None = None
    note: str | None = None

    @field_validator("evidence")
    @classmethod
    def _short_evidence(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        return value if len(value) <= EVIDENCE_MAX_CHARS else value[: EVIDENCE_MAX_CHARS - 1] + "…"

    @property
    def known(self) -> bool:
        return _is_known(self.value) and self.source != "missing"

    @property
    def is_fact(self) -> bool:
        """Stated by the deck, the user, or an approved source - not inferred."""
        return self.known and self.source in {"pitch_deck", "user_supplied", "external_source"}

    def cite(self) -> str:
        """Short provenance label: "Pitch deck, Slide 8" / "Analyst inference"."""
        if not self.known:
            return "Missing"
        label = SOURCE_LABELS[self.source]
        return f"{label}, {self.location}" if self.location else label


def _is_known(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in {"unknown", "n/a", "none"}
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def missing(note: str | None = None) -> Sourced[Any]:
    return Sourced(value=None, source="missing", confidence=0.0, note=note)


def from_deck(
    value: Any,
    location: str | None,
    confidence: float,
    evidence: str | None = None,
    method: str = "pattern",
) -> Sourced[Any]:
    return Sourced(
        value=value, source="pitch_deck", location=location, confidence=confidence,
        evidence=evidence, method=method,
    )


def inferred(
    value: Any,
    confidence: float,
    evidence: str | None = None,
    location: str | None = None,
    method: str = "derived",
    note: str | None = None,
) -> Sourced[Any]:
    return Sourced(
        value=value, source="inference", location=location, confidence=confidence,
        evidence=evidence, method=method, note=note,
    )


def user_supplied(value: Any, note: str | None = None) -> Sourced[Any]:
    return Sourced(
        value=value, source="user_supplied", location="Manual entry", confidence=1.0,
        method="manual", note=note,
    )


def pick_best(*candidates: Sourced[Any] | None) -> Sourced[Any]:
    """The strongest known candidate by (source priority, confidence); else missing."""
    known = [c for c in candidates if c is not None and c.known]
    if not known:
        return missing()
    return max(known, key=lambda c: (SOURCE_PRIORITY[c.source], c.confidence))
