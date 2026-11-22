"""The structured TEN Capital record: the machine-readable twin of the PDF report."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.company import InvestorPreferences
from app.models.match import (
    TIER_LABELS,
    CriterionScore,
    IdealInvestorProfile,
    InvestorMatch,
    RejectedInvestor,
)

SCHEMA_VERSION = "1.1"


class UserInfo(BaseModel):
    name: str = ""
    email: str = ""
    organization: str = ""


class DeckInfo(BaseModel):
    filename: str = ""
    format: str = ""
    pages: int = 0
    scanned: bool = False


class CompanyRecord(BaseModel):
    """Flat company block. ``extra="forbid"`` keeps it in lock-step with CompanyProfile.summary()."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    sector: str = ""
    subsector: str = ""
    stage: str = ""
    geography: str = ""
    business_model: str = ""
    product_stage: str = ""
    revenue_stage: str = ""
    revenue: float | None = None
    revenue_growth_pct: float | None = None
    traction: str = ""
    market_size: float | None = None
    round_type: str = ""
    raise_amount: float | None = None
    amount_raised: float | None = None
    round_committed: float | None = None
    valuation: float | None = None
    valuation_type: str = ""
    use_of_funds: list[str] = Field(default_factory=list)
    strategic_needs: list[str] = Field(default_factory=list)
    regulatory_status: str = ""
    intellectual_property: str = ""
    existing_investors: list[str] = Field(default_factory=list)
    lead_investor: str = ""
    competitors: list[str] = Field(default_factory=list)


class TopMatchRecord(BaseModel):
    rank: int
    investor: str
    investor_id: str
    investor_type: str
    kind: str
    fit_score: float
    adjusted_score: float
    ranking_score: float
    confidence: str
    confidence_score: float
    evidence_quality: str
    rationale: str
    investment_evidence: list[str]
    likely_concerns: list[str]
    outreach_angle: str
    required_materials: list[str]
    outreach_priority: str
    outreach_priority_key: str
    priority_reason: str
    preference_matches: list[str]
    score_breakdown: list[CriterionScore]
    data_source: str
    is_synthetic: bool
    text_source: str

    @classmethod
    def from_match(cls, match: InvestorMatch) -> TopMatchRecord:
        return cls(
            rank=match.rank, investor=match.investor_name, investor_id=match.investor_id,
            investor_type=match.investor_type_label, kind=match.kind, fit_score=match.fit_score,
            adjusted_score=match.adjusted_score, ranking_score=match.ranking_score,
            confidence=match.confidence, confidence_score=match.confidence_score,
            evidence_quality=match.evidence_quality, rationale=match.rationale,
            investment_evidence=match.investment_evidence, likely_concerns=match.likely_concerns,
            outreach_angle=match.outreach_angle, required_materials=match.required_materials,
            outreach_priority=TIER_LABELS[match.outreach_priority],
            outreach_priority_key=match.outreach_priority, priority_reason=match.priority_reason,
            preference_matches=match.preference_matches, score_breakdown=match.breakdown,
            data_source=match.data_source, is_synthetic=match.is_synthetic, text_source=match.text_source,
        )


class DatasetSummary(BaseModel):
    sources: list[str] = Field(default_factory=list)
    is_synthetic: bool = False
    record_count: int = 0
    unverified_records: int = 0
    warnings: list[str] = Field(default_factory=list)


class ExternalProcessing(BaseModel):
    llm_enabled: bool = False
    llm_used: bool = False
    provider: str | None = None
    model: str | None = None
    purposes: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class NotificationOutcome(BaseModel):
    """What happened to the automated results email for this analysis."""

    attempted: bool = False
    sent: bool = False
    provider: str | None = None
    to: list[str] = Field(default_factory=list)
    sender: str | None = None
    message_id: str | None = None
    attachments: list[str] = Field(default_factory=list)
    error: str | None = None
    skipped_reason: str | None = None
    attempted_at: datetime | None = None

    def summary(self) -> str:
        if self.sent:
            files = f" with {', '.join(self.attachments)}" if self.attachments else ""
            return f"Results emailed to {', '.join(self.to)}{files}."
        if self.skipped_reason:
            return f"Results email not sent: {self.skipped_reason}."
        if self.error:
            return f"Results email failed: {self.error}"
        return "Results email not attempted."


class ReportMeta(BaseModel):
    pdf_generated: bool = False
    pdf_filename: str | None = None
    page_count: int | None = None
    layout_rung: int | None = None
    content_reductions: list[str] = Field(default_factory=list)


class TenCapitalRecord(BaseModel):
    schema_version: str = SCHEMA_VERSION
    analysis_id: str
    timestamp: datetime
    user: UserInfo = Field(default_factory=UserInfo)
    source_deck: DeckInfo = Field(default_factory=DeckInfo)
    company: CompanyRecord
    company_provenance: dict[str, dict[str, Any]] = Field(default_factory=dict)
    investor_preferences: InvestorPreferences = Field(default_factory=InvestorPreferences)
    match_criteria: dict[str, float] = Field(default_factory=dict)
    scoring_policy: dict[str, Any] = Field(default_factory=dict)
    ideal_investor_profile: IdealInvestorProfile
    top_matches: list[TopMatchRecord] = Field(default_factory=list)
    rejected_investors: list[RejectedInvestor] = Field(default_factory=list)
    recommended_next_action: str = ""
    data_gaps: list[str] = Field(default_factory=list)
    investor_dataset: DatasetSummary = Field(default_factory=DatasetSummary)
    warnings: list[str] = Field(default_factory=list)
    external_processing: ExternalProcessing = Field(default_factory=ExternalProcessing)
    report: ReportMeta = Field(default_factory=ReportMeta)
    notification: NotificationOutcome = Field(default_factory=NotificationOutcome)
