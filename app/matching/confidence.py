"""Evidence quality, confidence, and the confidence-adjusted ranking score.

A 90/100 fit built on assumptions must not automatically outrank an 85/100 fit backed by
investment history. Confidence blends the investor's overall evidence quality with the
per-criterion confidence (which is zero wherever data was missing), and the adjusted
score discounts the fit accordingly.
"""

from __future__ import annotations

from datetime import date

from app.config import MatchingConfig
from app.matching.scoring import is_fresh, relevant_deals
from app.models.company import CompanyProfile
from app.models.investor import InvestorRecord
from app.models.match import ConfidenceLabel, CriterionScore, EvidenceLevel

QUALITY_FACTOR: dict[str, float] = {
    "verified": 1.0, "strong": 0.9, "moderate": 0.78, "inferred": 0.6, "unknown": 0.42,
}
HIGH_CONFIDENCE = 0.75
MEDIUM_CONFIDENCE = 0.55


def months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def _raw(breakdown: list[CriterionScore], key: str) -> float:
    item = next((c for c in breakdown if c.criterion == key), None)
    return item.raw_score if item is not None and item.known else 0.0


def evidence_quality(inv: InvestorRecord, company: CompanyProfile, breakdown: list[CriterionScore],
                     config: MatchingConfig, today: date) -> EvidenceLevel:
    """Classify the evidence behind this investor match.

    verified  reliable, recently verified source AND relevant recorded deals
    strong    multiple credible indicators (2+ relevant deals, or a deal plus a matching
              stated mandate, or a verified mandate)
    moderate  stated mandate (sector + stage or check) but little deal evidence
    inferred  fit rests mainly on investor type/category (all archetypes)
    unknown   too little investor data to judge
    """
    if inv.is_archetype:
        return "inferred"
    mandate = sum([bool(inv.sector_preferences), bool(inv.stage_preferences),
                   any(v is not None for v in inv.check_range())])
    if mandate < 2 and not inv.historical_investments:
        return "unknown"
    deals = relevant_deals(inv, company, today)
    fresh = inv.has_verification and is_fresh(inv.last_verified, today,
                                              config.scoring.verification_max_age_days)
    if fresh and deals:
        return "verified"
    if len(deals) >= 2 or (deals and _raw(breakdown, "sector") >= 65 and _raw(breakdown, "stage") >= 60) \
            or (fresh and mandate >= 2):
        return "strong"
    if mandate >= 2:
        return "moderate"
    return "inferred"


def confidence_score(quality: EvidenceLevel, breakdown: list[CriterionScore]) -> float:
    total = sum(c.weight for c in breakdown) or 1.0
    criterion_confidence = sum(c.weight * c.confidence for c in breakdown) / total
    return round(0.5 * QUALITY_FACTOR[quality] + 0.5 * criterion_confidence, 3)


def confidence_label(score: float) -> ConfidenceLabel:
    if score >= HIGH_CONFIDENCE:
        return "High"
    if score >= MEDIUM_CONFIDENCE:
        return "Medium"
    return "Low"


def adjusted_score(fit: float, confidence: float, blend: float) -> float:
    """fit x (1 - blend + blend x confidence): blend=0 ignores confidence, 1 applies it fully."""
    return round(fit * (1.0 - blend + blend * confidence), 1)
