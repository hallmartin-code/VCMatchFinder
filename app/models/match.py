"""Matching outputs: criterion scores, ranked matches, the rejection audit, and the IIP."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EvidenceLevel = Literal["verified", "strong", "moderate", "inferred", "unknown"]
ConfidenceLabel = Literal["High", "Medium", "Low"]
MatchKind = Literal["investor", "archetype"]
TierKey = Literal["tier_1", "tier_2", "tier_3", "tier_4", "do_not_target"]

EVIDENCE_LABELS: dict[str, str] = {
    "verified": "Verified",
    "strong": "Strong",
    "moderate": "Moderate",
    "inferred": "Inferred",
    "unknown": "Unknown",
}
TIER_LABELS: dict[str, str] = {
    "tier_1": "Tier 1 — Contact Now",
    "tier_2": "Tier 2 — Strong Target",
    "tier_3": "Tier 3 — Develop Relationship",
    "tier_4": "Tier 4 — Conditional Target",
    "do_not_target": "Do Not Target",
}
TIER_SHORT: dict[str, str] = {
    "tier_1": "T1 · Contact now",
    "tier_2": "T2 · Strong target",
    "tier_3": "T3 · Develop",
    "tier_4": "T4 · Conditional",
    "do_not_target": "Do not target",
}
TIER_ORDER: dict[str, int] = {"tier_1": 0, "tier_2": 1, "tier_3": 2, "tier_4": 3,
                              "do_not_target": 4}


class CriterionScore(BaseModel):
    criterion: str
    label: str
    weight: float = Field(description="Normalized weight; all nine sum to 100.")
    raw_score: float = Field(ge=0, le=100)
    weighted_score: float = Field(ge=0, le=100)
    evidence: str
    evidence_source: str
    evidence_level: EvidenceLevel
    confidence: float = Field(ge=0, le=1)
    missing_information: list[str] = Field(default_factory=list)
    known: bool = True


class FilterReason(BaseModel):
    code: str
    detail: str


class RejectedInvestor(BaseModel):
    investor_id: str
    investor_name: str
    investor_type: str
    data_source: str
    reasons: list[FilterReason]


class InvestorMatch(BaseModel):
    rank: int = 0
    investor_id: str
    investor_name: str
    investor_type: str
    investor_type_label: str
    kind: MatchKind = "investor"
    is_synthetic: bool = False
    data_source: str = ""
    fit_score: float = Field(ge=0, le=100)
    breakdown: list[CriterionScore]
    evidence_quality: EvidenceLevel
    confidence_score: float = Field(ge=0, le=1)
    confidence: ConfidenceLabel
    adjusted_score: float = Field(ge=0, le=100)
    preference_matches: list[str] = Field(default_factory=list)
    preference_bonus: float = 0.0
    ranking_score: float = 0.0
    outreach_priority: TierKey = "do_not_target"
    priority_reason: str = ""
    rationale: str = ""
    investment_evidence: list[str] = Field(default_factory=list)
    likely_concerns: list[str] = Field(default_factory=list)
    outreach_angle: str = ""
    required_materials: list[str] = Field(default_factory=list)
    text_source: Literal["deterministic", "llm_polished"] = "deterministic"

    def criterion(self, key: str) -> CriterionScore | None:
        return next((c for c in self.breakdown if c.criterion == key), None)

    @property
    def tier_label(self) -> str:
        return TIER_LABELS[self.outreach_priority]


class CategoryRecommendation(BaseModel):
    investor_type: str
    label: str
    rationale: str


class IdealInvestorProfile(BaseModel):
    """Who should fund this company now. Entirely analyst inference from the company record."""

    target_stages: list[str] = Field(default_factory=list)
    recommended_categories: list[CategoryRecommendation] = Field(default_factory=list)
    excluded_categories: list[CategoryRecommendation] = Field(default_factory=list)
    sector_specialization: str = ""
    geographic_fit: str = ""
    lead_check_range: tuple[float | None, float | None] = (None, None)
    follow_check_range: tuple[float | None, float | None] = (None, None)
    lead_required: bool = True
    lead_basis: str = ""
    strategic_vs_financial: str = ""
    technical_expertise: str | None = None
    commercialization_expertise: str | None = None
    regulatory_expertise: str | None = None
    cross_border_capability: str | None = None
    follow_on_capacity: str = ""
    strategic_partnership_potential: str | None = None
    customer_channel_access: str | None = None
    summary_points: list[str] = Field(default_factory=list)
    basis: str = "Analyst inference from the normalized company record."


class MatchRun(BaseModel):
    matches: list[InvestorMatch] = Field(default_factory=list)
    archetype_matches: list[InvestorMatch] = Field(default_factory=list)
    rejected: list[RejectedInvestor] = Field(default_factory=list)
    used_archetype_fallback: bool = False
    investors_considered: int = 0
    weights: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def top(self, n: int) -> list[InvestorMatch]:
        """Report rows: named investors first, topped up with archetypes if needed."""
        rows = [m for m in self.matches if m.outreach_priority != "do_not_target"][:n]
        if len(rows) < n:
            rows.extend(self.archetype_matches[: n - len(rows)])
        return rows
