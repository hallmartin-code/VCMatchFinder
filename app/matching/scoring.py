"""Deterministic 0-100 fit scoring across nine weighted criteria.

Each criterion produces a raw score (0-100), and weighted = raw x weight / 100 with the
weights normalized to total 100. The fit score is the sum. No model invents a number:
every raw score comes from an explicit rule below, with its evidence, source, evidence
level and confidence retained for the breakdown.

Missing data: a criterion that cannot be judged scores ``unknown_raw_score`` (default 40)
- below any positive evidence - and contributes zero confidence. The exception is
historical evidence, which measures evidence itself: no recorded history scores 0.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from app.config import CRITERIA, MatchingConfig
from app.matching.taxonomy import (
    REVENUE_STAGE_INDEX,
    REVENUE_STAGES,
    STAGE_INDEX,
    STRATEGIC_TYPES,
    business_model_label,
    describe_sector_tokens,
    family_label,
    family_of,
    revenue_stage_for,
    stage_distance,
    stage_label,
    strategic_label,
    subsector_label,
)
from app.models.company import CompanyProfile, InvestorPreferences
from app.models.investor import HistoricalInvestment, InvestorRecord
from app.models.match import CriterionScore, EvidenceLevel
from app.utils.geography import describe_location, describe_token, geography_match
from app.utils.money import format_money, format_money_range
from app.utils.provenance import Sourced

#: Share-of-round bands used by the check-size rule.
LEAD_BAND = (0.25, 0.60)
FOLLOW_FLOOR = 0.10
SMALL_FLOOR = 0.03

_ACHIEVED = re.compile(r"granted|received|cleared|approved|designation|ce[\s-]marked", re.I)
#: Revenue stage an investor's earliest entry stage implies it expects.
_REVENUE_BY_ENTRY_STAGE = {
    "pre_seed": "pre_revenue", "seed": "pre_revenue", "series_a": "early_revenue",
    "series_b": "revenue", "series_c": "scaling_revenue", "growth": "scaling_revenue",
}


@dataclass
class Evaluation:
    raw: float | None
    evidence: str
    level: EvidenceLevel = "moderate"
    confidence: float = 0.0
    missing: list[str] = field(default_factory=list)
    known: bool = True
    company_ref: str = ""


@dataclass(frozen=True)
class ScoringContext:
    company: CompanyProfile
    preferences: InvestorPreferences
    config: MatchingConfig
    today: date
    needs_lead: bool


def is_fresh(when: date | None, today: date, max_age_days: int) -> bool:
    return when is not None and 0 <= (today - when).days <= max_age_days


def investor_side(inv: InvestorRecord, ctx: ScoringContext) -> tuple[float, str]:
    """Confidence in the investor-side data and its evidence level."""
    if inv.is_archetype:
        return 0.5, "inferred"
    if inv.has_verification and is_fresh(inv.last_verified, ctx.today,
                                         ctx.config.scoring.verification_max_age_days):
        return 1.0, "verified"
    if inv.source_urls:
        return 0.85, "moderate"
    return 0.75, "moderate"


def _level(inv_level: str, item: Sourced | None, strong: bool) -> EvidenceLevel:
    if inv_level == "inferred":
        return "inferred"
    if item is not None and item.known and item.source == "inference":
        return "inferred" if item.confidence < 0.6 else "moderate"
    if inv_level == "verified":
        return "verified"
    return "strong" if strong else "moderate"


def _unknown(evidence: str, *missing: str) -> Evaluation:
    return Evaluation(raw=None, evidence=evidence, level="unknown", confidence=0.0,
                      missing=list(missing), known=False)


def _eval(ctx: ScoringContext, inv: InvestorRecord, raw: float, evidence: str,
          item: Sourced | None, *, strong: bool = False, inferred: bool = False,
          missing: tuple[str, ...] = ()) -> Evaluation:
    inv_conf, inv_level = investor_side(inv, ctx)
    company_conf = item.confidence if item is not None and item.known else 0.6
    level: EvidenceLevel = "inferred" if inferred else _level(inv_level, item, strong)
    confidence = inv_conf * company_conf * (0.8 if inferred else 1.0)
    return Evaluation(raw=raw, evidence=evidence, level=level, confidence=round(min(confidence, 1.0), 3),
                      missing=list(missing), known=True,
                      company_ref=item.cite() if item is not None and item.known else "")


# ------------------------------------------------------------------------ criteria


def eval_stage(ctx: ScoringContext, inv: InvestorRecord) -> Evaluation:
    item = ctx.company.company_stage
    if not item.known:
        return _unknown("Company stage not established.", "Company stage")
    if not inv.stage_preferences:
        return _unknown(f"Company is raising {stage_label(item.value)}; investor stage focus not recorded.",
                        "Investor stage focus")
    distance = stage_distance(item.value, inv.stage_preferences)
    focus = ", ".join(stage_label(s) for s in inv.stage_preferences)
    here = STAGE_INDEX[item.value]
    if distance == 0:
        raw, note = 100.0, f"Invests at {stage_label(item.value)}"
    elif distance == 1:
        earliest = min(STAGE_INDEX[s] for s in inv.stage_preferences)
        raw = 60.0
        note = "Usually enters one round later" if earliest > here else "Usually invests one round earlier"
    else:
        raw, note = 0.0, "Stage outside its focus"
    return _eval(ctx, inv, raw, f"{note} (company: {stage_label(item.value)}; focus: {focus}).", item)


def eval_sector(ctx: ScoringContext, inv: InvestorRecord) -> Evaluation:
    company = ctx.company
    sub = company.subsector.value if company.subsector.known else None
    fam = company.sector.value if company.sector.known else family_of(sub)
    item = company.subsector if company.subsector.known else company.sector
    if not sub and not fam:
        return _unknown("Company sector not established.", "Company sector")
    tokens = inv.sector_tokens()
    if not tokens:
        return _unknown("Investor sector focus not recorded.", "Investor sector focus")
    focus = describe_sector_tokens(tokens)
    if sub and f"sub:{sub}" in tokens:
        raw, note = 100.0, f"Core {subsector_label(sub)} mandate"
    elif fam and f"fam:{fam}" in tokens:
        raw, note = 75.0, f"Invests across {family_label(fam)}"
    elif fam and any(t.startswith("sub:") and family_of(t[4:]) == fam for t in tokens):
        raw, note = 65.0, f"Adjacent {family_label(fam)} focus"
    elif "generalist" in tokens:
        raw, note = 50.0, "Generalist mandate"
    else:
        raw, note = 0.0, "No sector overlap"
    return _eval(ctx, inv, raw, f"{note} (investor focus: {focus}).", item)


def eval_check_size(ctx: ScoringContext, inv: InvestorRecord) -> Evaluation:
    item = ctx.company.raise_amount
    low, high = inv.check_range()
    missing: list[str] = []
    if not item.known:
        missing.append("Round size")
    if low is None and high is None:
        missing.append("Investor check size")
    if missing:
        return _unknown("Check-size fit cannot be judged: " + " and ".join(m.lower() for m in missing)
                        + " missing.", *missing)
    round_size = float(item.value)
    low = low if low is not None else high
    high = high if high is not None else low
    assert low is not None and high is not None
    span = format_money_range(low, high)
    if low > round_size:
        raw, note = 0.0, f"Minimum check {format_money(low)} exceeds the round"
    elif low > LEAD_BAND[1] * round_size:
        raw, note = 60.0, f"{span} checks would take most of the round"
    elif high >= LEAD_BAND[0] * round_size:
        raw, note = 100.0, f"{span} checks fit a lead-sized allocation"
    elif high >= FOLLOW_FLOOR * round_size:
        raw, note = 75.0, f"{span} checks fit a meaningful follow-on allocation"
    elif high >= SMALL_FLOOR * round_size:
        raw, note = 40.0, f"{span} checks make it a small participant"
    else:
        raw, note = 15.0, f"{span} checks are too small to matter"
    return _eval(ctx, inv, raw, f"{note} in a {format_money(round_size)} round.", item)


def eval_geography(ctx: ScoringContext, inv: InvestorRecord) -> Evaluation:
    location = ctx.company.location()
    if location is None:
        return _unknown("Company headquarters not established.", "Company headquarters")
    tokens = inv.geo_tokens()
    if not tokens:
        return _unknown("Investor geographic mandate not recorded.", "Investor geographic mandate")
    raw, token = geography_match(location.tokens, tokens)
    mandate = ", ".join(inv.investment_geographies)
    note = f"Mandate covers {describe_token(token)}" if token else "Outside its stated mandate"
    return _eval(ctx, inv, raw, f"{note} (company: {describe_location(location)}; mandate: {mandate}).",
                 ctx.company.headquarters)


def eval_business_model(ctx: ScoringContext, inv: InvestorRecord) -> Evaluation:
    item = ctx.company.business_model
    tags = set(item.value or [])
    if not tags:
        return _unknown("Company business model not established.", "Company business model")
    prefs = inv.business_model_tags()
    if not prefs:
        return _unknown("Investor business-model preferences not recorded.",
                        "Investor business-model preferences")
    overlap = tags & prefs
    if overlap:
        return _eval(ctx, inv, 100.0, f"Backs {business_model_label(sorted(overlap))} models.", item)
    return _eval(ctx, inv, 25.0, f"Prefers {business_model_label(sorted(prefs))}; company is "
                 f"{business_model_label(sorted(tags))}.", item)


def eval_traction(ctx: ScoringContext, inv: InvestorRecord) -> Evaluation:
    company = ctx.company
    item = company.revenue_stage
    if not item.known:
        return _unknown("Company revenue/traction stage not established.", "Company revenue / traction")
    company_index = REVENUE_STAGE_INDEX[item.value]
    inferred = False
    if inv.min_revenue is not None:
        required = revenue_stage_for(inv.min_revenue) or "pre_revenue"
        basis = f"minimum revenue {format_money(inv.min_revenue)}"
    elif inv.stage_preferences:
        earliest = min(inv.stage_preferences, key=STAGE_INDEX.__getitem__)
        required = _REVENUE_BY_ENTRY_STAGE[earliest]
        basis = f"implied by its {stage_label(earliest)} entry point"
        inferred = True
    else:
        return _unknown("Investor traction requirement not recorded.", "Investor traction requirement")
    gap = REVENUE_STAGE_INDEX[required] - company_index
    validation = company.customers.known or company.partnerships.known or bool(
        company.regulatory_status.known and _ACHIEVED.search(company.regulatory_status.value or ""))
    if gap <= 0:
        raw, note = 100.0, "Traction meets its entry bar"
    elif gap == 1:
        raw = 70.0 if validation else 55.0
        note = "One step below its usual revenue bar" + (" (offset by customer/regulatory validation)"
                                                         if validation else "")
    elif gap == 2:
        raw, note = 20.0, "Well below its usual revenue bar"
    else:
        raw, note = 5.0, "Far below its usual revenue bar"
    evidence = (f"{note}: company is {REVENUE_STAGES[item.value].lower()}; expectation "
                f"{REVENUE_STAGES[required].lower()} ({basis}).")
    return _eval(ctx, inv, raw, evidence, item, inferred=inferred)


def relevant_deals(inv: InvestorRecord, company: CompanyProfile,
                   today: date) -> list[tuple[HistoricalInvestment, float]]:
    """Recorded deals relevant to this company, with a relevance weight, strongest first."""
    sub = company.subsector.value if company.subsector.known else None
    fam = company.sector.value if company.sector.known else family_of(sub)
    stage = company.company_stage.value if company.company_stage.known else None
    out: list[tuple[HistoricalInvestment, float]] = []
    for deal in inv.historical_investments:
        tokens = deal.sector_tokens()
        if sub and f"sub:{sub}" in tokens:
            weight = 1.0
        elif fam and (f"fam:{fam}" in tokens or any(t.startswith("sub:") and family_of(t[4:]) == fam
                                                      for t in tokens)):
            weight = 0.6
        else:
            continue
        deal_stage = deal.stage_key()
        if stage and deal_stage:
            distance = abs(STAGE_INDEX[stage] - STAGE_INDEX[deal_stage])
            weight *= 1.0 if distance == 0 else 0.85 if distance == 1 else 0.6
        if deal.year and today.year - deal.year > 5:
            weight *= 0.5
        out.append((deal, round(weight, 3)))
    return sorted(out, key=lambda pair: -pair[1])


def describe_deal(deal: HistoricalInvestment) -> str:
    bits = [b for b in (stage_label(deal.stage_key()) if deal.stage_key() else None,
                        str(deal.year) if deal.year else None) if b]
    detail = " ".join(bits)
    led = ", led" if deal.led else ""
    return f"{deal.company} ({detail}{led})" if detail or led else deal.company


def eval_historical(ctx: ScoringContext, inv: InvestorRecord) -> Evaluation:
    if inv.is_archetype:
        return Evaluation(raw=0.0, evidence="Investor profile - no investment history.",
                          level="unknown", missing=["Investment history"], known=False)
    if not inv.historical_investments and not inv.portfolio_companies:
        return Evaluation(raw=0.0, evidence="No investment history in the dataset.", level="unknown",
                          missing=["Investment history"], known=False)
    deals = relevant_deals(inv, ctx.company, ctx.today)
    points = sum(w for _d, w in deals)
    if points > 0:
        raw = 100.0 * (1 - 0.5 ** points)
        named = "; ".join(describe_deal(d) for d, _w in deals[:3])
        evidence = f"{len(deals)} relevant recorded deal{'s' if len(deals) != 1 else ''}: {named}."
        return _eval(ctx, inv, round(raw, 1), evidence, ctx.company.subsector, strong=len(deals) >= 2)
    if inv.historical_investments:
        return _eval(ctx, inv, 10.0, "Recorded deals are outside the company's sector.", ctx.company.subsector)
    return Evaluation(raw=15.0, evidence=f"Portfolio listed without sector detail ({', '.join(inv.portfolio_companies[:3])}).",
                      level="inferred", confidence=0.3, missing=["Deal sector/stage detail"], known=True)


def eval_strategic(ctx: ScoringContext, inv: InvestorRecord) -> Evaluation:
    item = ctx.company.strategic_needs
    needs = set(item.value or [])
    if not needs:
        return _unknown("Company strategic needs not established.", "Company strategic needs")
    caps = inv.strategic_tags()
    if not caps:
        if inv.investor_type in STRATEGIC_TYPES:
            return _eval(ctx, inv, 50.0, "Strategic investor type; specific capabilities not recorded.",
                         item, inferred=True)
        return _unknown("Investor strategic capabilities not recorded.", "Investor strategic capabilities")
    overlap = needs & caps
    if overlap:
        raw = min(100.0, 40.0 + 30.0 * len(overlap))
        return _eval(ctx, inv, raw, f"Offers {strategic_label(sorted(overlap)).lower()}.", item)
    return _eval(ctx, inv, 15.0, f"Capabilities ({strategic_label(sorted(caps)).lower()}) do not match "
                 f"needs ({strategic_label(sorted(needs)).lower()}).", item)


def eval_lead(ctx: ScoringContext, inv: InvestorRecord) -> Evaluation:
    company = ctx.company
    capability = inv.lead_capability
    inferred = False
    if capability == "unknown" and any(d.led for d in inv.historical_investments):
        capability, inferred = "sometimes", True
    if ctx.preferences.lead_required is not None:
        item: Sourced = Sourced(value=ctx.preferences.lead_required, source="user_supplied",
                                location="Manual entry", confidence=1.0)
    elif company.lead_investor.known:
        item = company.lead_investor
    elif company.seeking_lead.known:
        item = company.seeking_lead
    else:
        item = Sourced(value=True, source="inference", confidence=0.5,
                       note="No lead named; assumed still needed")
    if capability == "unknown":
        return _unknown("Investor lead capability not recorded.", "Investor lead capability")
    table = {True: {"yes": 100.0, "sometimes": 60.0, "no": 10.0},
             False: {"yes": 85.0, "sometimes": 90.0, "no": 100.0}}
    raw = table[ctx.needs_lead][capability]
    role = {"yes": "leads rounds", "sometimes": "sometimes leads", "no": "follows only"}[capability]
    situation = "Round still needs a lead" if ctx.needs_lead else "Lead already in place"
    suffix = " (inferred from led deals)" if inferred else ""
    return _eval(ctx, inv, raw, f"{situation}; investor {role}{suffix}.", item, inferred=inferred)


EVALUATORS: dict[str, Callable[[ScoringContext, InvestorRecord], Evaluation]] = {
    "stage": eval_stage,
    "sector": eval_sector,
    "check_size": eval_check_size,
    "geography": eval_geography,
    "business_model": eval_business_model,
    "traction": eval_traction,
    "historical_evidence": eval_historical,
    "strategic_value": eval_strategic,
    "lead_capability": eval_lead,
}


def score_investor(inv: InvestorRecord, company: CompanyProfile, preferences: InvestorPreferences,
                   config: MatchingConfig, *, today: date, needs_lead: bool) -> list[CriterionScore]:
    ctx = ScoringContext(company=company, preferences=preferences, config=config, today=today,
                         needs_lead=needs_lead)
    weights = config.weights.normalized()
    unknown_raw = config.scoring.unknown_raw_score
    breakdown: list[CriterionScore] = []
    for key, label in CRITERIA.items():
        ev = EVALUATORS[key](ctx, inv)
        raw = ev.raw if ev.raw is not None else unknown_raw
        weight = weights[key]
        source = f"Investor: {inv.data_source}" + (f" · Company: {ev.company_ref}" if ev.company_ref else "")
        breakdown.append(CriterionScore(
            criterion=key, label=label, weight=round(weight, 4), raw_score=round(raw, 1),
            weighted_score=round(raw * weight / 100.0, 3), evidence=ev.evidence, evidence_source=source,
            evidence_level=ev.level if ev.known else "unknown",
            confidence=ev.confidence if ev.known else 0.0,
            missing_information=ev.missing, known=ev.known,
        ))
    return breakdown


def fit_score(breakdown: list[CriterionScore]) -> float:
    return round(sum(c.raw_score * c.weight / 100.0 for c in breakdown), 1)


def known_weight_share(breakdown: list[CriterionScore]) -> float:
    total = sum(c.weight for c in breakdown) or 1.0
    return sum(c.weight for c in breakdown if c.known) / total
