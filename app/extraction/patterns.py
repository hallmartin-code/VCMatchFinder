"""A small cue engine for locating values in deck text.

A ``Cue`` is a keyword regex plus where the value sits relative to it. Cues are narrow
and carry ``exclude`` guards, because a wrong confident number (a revenue *target*
reported as revenue) damages a match far more than a missing one.

Scopes:
    line    the value is on the cue line; the amount nearest the cue is taken
    window  as ``line``, falling back to the next line (label-above-value layouts)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.ingestion.models import DeckDocument, DeckLine
from app.utils.money import _AMOUNT_RE
from app.utils.provenance import Sourced

Scope = Literal["line", "window"]

#: Marks a figure as a plan rather than an achieved fact.
FORWARD_LOOKING = (
    r"\bmilestones?\b|\btargets?\b|\bgoals?\b|\bprojected\b|\bprojections?\b|\bforecasts?\b|"
    r"\bplan(?:ned|s)?\b|\bwithin\s+\d+\s+(?:months?|years?|quarters?)\b|\bby\s+(?:fy\s?)?20\d\d\b|"
    r"\bwe\s+will\b|\bexpects?\b|\bexpected\b|\bpath\s+to\b|\bpotential\b|\bestimated\b|"
    r"\b20\d\d\s*[eE]\b|\bgoal\b|\baim\b|\bopportunity\b"
)
RAISED_TO_DATE = (
    r"raised\s+to\s+date|to\s+date|previously|so\s+far|already\s+raised|since\s+inception|"
    r"prior\s+round|last\s+round|previous\s+round"
)
NOTES_CONFIDENCE_FACTOR = 0.9


@dataclass(frozen=True)
class Cue:
    pattern: str
    confidence: float
    scope: Scope = "line"
    exclude: str | None = None


@dataclass(frozen=True)
class Hit:
    value: Any
    location: str
    evidence: str
    confidence: float
    page: int


def money_mentions(text: str) -> list[tuple[int, float]]:
    """(position, amount) for every strict money mention (currency or magnitude present)."""
    out: list[tuple[int, float]] = []
    for match in _AMOUNT_RE.finditer(text):
        number = float(match.group("number").replace(",", ""))
        suffix = (match.group("suffix") or "").lower()
        word = (match.group("word") or "").lower()
        if suffix:
            number *= {"k": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "b": 1e9, "bn": 1e9}[suffix]
        elif word:
            number *= {"thousand": 1e3, "million": 1e6, "billion": 1e9}[word]
        elif not match.group("currency"):
            continue
        out.append((match.start(), number))
    return out


def nearest_money(text: str, anchor: int) -> float | None:
    mentions = money_mentions(text)
    if not mentions:
        return None
    return min(mentions, key=lambda m: abs(m[0] - anchor))[1]


def _excluded(pattern: str | None, text: str) -> bool:
    return bool(pattern) and re.search(pattern, text, re.IGNORECASE) is not None


def scan(
    doc: DeckDocument,
    cues: tuple[Cue, ...] | list[Cue],
    parse: Callable[[str, int], Any | None],
) -> list[Hit]:
    """Every hit for the cues. ``parse(text, anchor)`` returns a value or None."""
    hits: list[Hit] = []
    for line in doc.iter_lines():
        for cue in cues:
            match = re.search(cue.pattern, line.text, re.IGNORECASE)
            if not match or _excluded(cue.exclude, line.text):
                continue
            value = parse(line.text, match.end())
            evidence = line.text
            if value is None and cue.scope == "window" and line.next_text:
                if _excluded(cue.exclude, line.next_text):
                    continue
                value = parse(line.next_text, 0)
                evidence = f"{line.text} {line.next_text}"
            if value is None:
                continue
            confidence = cue.confidence
            if "speaker notes" in line.location:
                confidence *= NOTES_CONFIDENCE_FACTOR
            hits.append(Hit(value, line.location, evidence, round(confidence, 3), line.page))
    return hits


def best(hits: list[Hit]) -> Hit | None:
    """Highest confidence wins; ties go to the earliest page."""
    if not hits:
        return None
    return max(hits, key=lambda h: (h.confidence, -h.page))


def to_sourced(hit: Hit | None, doc: DeckDocument, *, method: str = "pattern",
               value: Any = None, note: str | None = None) -> Sourced[Any]:
    """Wrap a hit with provenance; user notes become ``user_supplied``."""
    if hit is None:
        return Sourced(value=None, source="missing")
    source = "user_supplied" if doc.source_format == "notes" else "pitch_deck"
    return Sourced(
        value=hit.value if value is None else value,
        source=source,
        location=hit.location,
        confidence=hit.confidence,
        evidence=hit.evidence,
        method=method,
        note=note,
    )


def money_parser(text: str, anchor: int) -> float | None:
    return nearest_money(text, anchor)


def lines_matching(doc: DeckDocument, pattern: str, *, exclude: str | None = None) -> list[DeckLine]:
    compiled = re.compile(pattern, re.IGNORECASE)
    return [ln for ln in doc.iter_lines()
            if compiled.search(ln.text) and not _excluded(exclude, ln.text)]
