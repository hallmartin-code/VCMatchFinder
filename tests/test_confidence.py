"""Evidence quality and confidence-adjusted ranking."""

from __future__ import annotations

from datetime import date

from helpers import TODAY, make_investor

from app.config import CRITERIA, MatchingConfig, MatchWeights
from app.matching.confidence import adjusted_score, confidence_label, confidence_score, evidence_quality
from app.matching.scoring import score_investor
from app.models.company import InvestorPreferences
from app.models.investor import HistoricalInvestment, InvestorRecord
from app.models.match import CriterionScore

WEIGHTS = MatchWeights().normalized()
DEAL = HistoricalInvestment(company="Cortexa", sector="diagnostics", stage="Series A", year=2025, led=True)


def quality(inv, company):
    config = MatchingConfig()
    items = score_investor(inv, company, InvestorPreferences(), config, today=TODAY, needs_lead=True)
    return evidence_quality(inv, company, items, config, TODAY)


def uniform(raw: float, conf: float) -> list[CriterionScore]:
    return [CriterionScore(criterion=k, label=k, weight=WEIGHTS[k], raw_score=raw, weighted_score=raw * WEIGHTS[k] / 100,
                           evidence="x", evidence_source="x", evidence_level="moderate", confidence=conf)
            for k in CRITERIA]


def test_evidence_levels(lumora_company):
    verified = make_investor(historical_investments=[DEAL], source_urls=["https://a.example"],
                             last_verified=date(2026, 6, 1))
    assert quality(verified, lumora_company) == "verified"
    stale = verified.model_copy(update={"last_verified": date(2024, 1, 1)})
    assert quality(stale, lumora_company) == "strong"
    assert quality(make_investor(), lumora_company) == "moderate"
    assert quality(InvestorRecord(investor_id="x", name="Name Only"), lumora_company) == "unknown"
    assert quality(make_investor(is_archetype=True), lumora_company) == "inferred"


def test_assumption_heavy_90_does_not_outrank_evidenced_85():
    inferred_conf = confidence_score("inferred", uniform(90, 0.35))
    strong_conf = confidence_score("strong", uniform(85, 0.8))
    assert adjusted_score(90, inferred_conf, 0.5) < adjusted_score(85, strong_conf, 0.5)


def test_confidence_labels_and_blend():
    assert confidence_label(0.8) == "High"
    assert confidence_label(0.6) == "Medium"
    assert confidence_label(0.4) == "Low"
    assert adjusted_score(80, 0.5, 0.0) == 80  # blend 0 ignores confidence
    assert adjusted_score(80, 0.5, 1.0) == 40  # blend 1 applies it fully
