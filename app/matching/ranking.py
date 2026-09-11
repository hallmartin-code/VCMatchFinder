"""Filter -> score -> confidence -> tier -> rank.

Ranking order is outreach priority first (Tier 1 before Tier 2 ...), then the ranking
score (confidence-adjusted fit plus a small, explicit preference bonus). Archetype
profiles are scored with the same engine but kept in a separate list and capped at
Tier 2, because a category cannot be contacted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.config import MatchingConfig
from app.matching.confidence import (
    adjusted_score,
    confidence_label,
    confidence_score,
    evidence_quality,
    months_between,
)
from app.matching.filters import apply_hard_filters, is_existing_investor
from app.matching.investor_profile import build_archetypes
from app.matching.scoring import fit_score, score_investor
from app.matching.taxonomy import STAGE_INDEX, STAGES, investor_type_label, stage_label
from app.models.company import CompanyProfile, InvestorPreferences
from app.models.investor import InvestorRecord
from app.models.match import TIER_ORDER, IdealInvestorProfile, InvestorMatch, MatchRun, TierKey
from app.utils.geography import parse_geo_tokens


@dataclass
class RankingOutput:
    run: MatchRun
    records: dict[str, InvestorRecord]


def preference_bonus(inv: InvestorRecord, preferences: InvestorPreferences,
                     config: MatchingConfig) -> tuple[float, list[str]]:
    matches: list[str] = []
    if inv.investor_type in preferences.preferred_types:
        matches.append(f"Preferred type: {investor_type_label(inv.investor_type)}")
    if preferences.preferred_geographies:
        wanted = set().union(*(parse_geo_tokens(g) for g in preferences.preferred_geographies))
        where = inv.geo_tokens() | parse_geo_tokens(inv.headquarters)
        if wanted & where:
            matches.append("Preferred geography")
    per = config.scoring.preference_bonus_per_match
    return min(per * len(matches), config.scoring.preference_bonus_cap), matches


def assign_tier(match: InvestorMatch, inv: InvestorRecord, company: CompanyProfile,
                config: MatchingConfig, *, needs_lead: bool, today: date) -> tuple[TierKey, str]:
    r = config.ranking
    adj = match.adjusted_score
    if adj < r.tier3_min_adjusted:
        return "do_not_target", f"Confidence-adjusted score {adj:.0f} is below the {r.tier3_min_adjusted:.0f} threshold."
    if inv.is_archetype:
        tier: TierKey = "tier_2" if adj >= r.tier2_min_adjusted else "tier_3"
        return tier, "Investor profile, not a named firm: source and verify named investors in this category."
    if is_existing_investor(inv, company):
        return "tier_4", "Existing investor: approach for pro-rata or insider support, not as a new lead."
    if needs_lead and inv.lead_capability == "no":
        return "tier_4", "Invests only behind a lead: approach once a lead has committed."
    if match.confidence == "Low":
        return "tier_4", "Fit rests on missing or unverified data: verify the mandate before outreach."
    stage = company.company_stage.value if company.company_stage.known else None
    if stage and inv.stage_preferences:
        earliest = min(STAGE_INDEX[s] for s in inv.stage_preferences)
        if earliest > STAGE_INDEX[stage]:
            return "tier_3", f"Enters at {stage_label(STAGES[earliest])}: build the relationship now for the next round."
    last = inv.last_known_investment
    if last and months_between(last, today) > config.scoring.stale_activity_months:
        return "tier_3", f"No recorded investment since {last:%b %Y}: confirm the fund is still active."
    stage_c = match.criterion("stage")
    check_c = match.criterion("check_size")
    lead_ok = (not needs_lead) or inv.lead_capability in {"yes", "sometimes"}
    if (adj >= r.tier1_min_adjusted and stage_c is not None and stage_c.known and stage_c.raw_score >= 100
            and check_c is not None and check_c.known and check_c.raw_score >= 60 and lead_ok):
        return "tier_1", "Strong current-round fit on stage, check size and lead role, with adequate evidence."
    if adj >= r.tier2_min_adjusted:
        return "tier_2", "Good fit; one or more criteria are partial or unverified."
    return "tier_3", "Partial fit: worth a relationship, not a priority for this round."


def build_match(inv: InvestorRecord, company: CompanyProfile, preferences: InvestorPreferences,
                config: MatchingConfig, *, today: date, needs_lead: bool) -> InvestorMatch:
    breakdown = score_investor(inv, company, preferences, config, today=today, needs_lead=needs_lead)
    fit = fit_score(breakdown)
    quality = evidence_quality(inv, company, breakdown, config, today)
    confidence = confidence_score(quality, breakdown)
    adjusted = adjusted_score(fit, confidence, config.scoring.confidence_blend)
    bonus, preference_matches = preference_bonus(inv, preferences, config)
    match = InvestorMatch(
        investor_id=inv.investor_id,
        investor_name=inv.name,
        investor_type=inv.investor_type,
        investor_type_label=investor_type_label(inv.investor_type),
        kind="archetype" if inv.is_archetype else "investor",
        is_synthetic=inv.is_synthetic,
        data_source=inv.data_source,
        fit_score=fit,
        breakdown=breakdown,
        evidence_quality=quality,
        confidence_score=confidence,
        confidence=confidence_label(confidence),
        adjusted_score=adjusted,
        preference_matches=preference_matches,
        preference_bonus=bonus,
        ranking_score=round(min(100.0, adjusted + bonus), 1),
    )
    match.outreach_priority, match.priority_reason = assign_tier(
        match, inv, company, config, needs_lead=needs_lead, today=today)
    return match


def _order(matches: list[InvestorMatch]) -> list[InvestorMatch]:
    return sorted(matches, key=lambda m: (TIER_ORDER[m.outreach_priority], -m.ranking_score, m.investor_name))


def rank_investors(investors: list[InvestorRecord], company: CompanyProfile,
                   preferences: InvestorPreferences, config: MatchingConfig,
                   iip: IdealInvestorProfile, *, today: date | None = None) -> RankingOutput:
    today = today or date.today()
    needs_lead = company.needs_lead(preferences.lead_required)
    records = {inv.investor_id: inv for inv in investors}

    viable, rejected = apply_hard_filters(investors, company, preferences)
    matches = _order([build_match(inv, company, preferences, config, today=today, needs_lead=needs_lead)
                      for inv in viable])
    for rank, match in enumerate(matches, start=1):
        match.rank = rank

    archetypes = build_archetypes(company, iip, preferences)
    for archetype in archetypes:
        records[archetype.investor_id] = archetype
    archetype_matches = [m for m in _order([
        build_match(a, company, preferences, config, today=today, needs_lead=needs_lead) for a in archetypes
    ]) if m.outreach_priority != "do_not_target"]

    named = [m for m in matches if m.outreach_priority != "do_not_target"]
    for offset, match in enumerate(archetype_matches, start=1):
        match.rank = len(named) + offset
    fallback = len(named) < min(config.ranking.top_n, 5)

    warnings: list[str] = []
    if not investors:
        warnings.append("No investor records were available; results are investor archetypes only.")
    elif not viable:
        warnings.append(f"All {len(investors)} investors failed hard filters; results are archetypes only.")
    if fallback and investors:
        warnings.append(f"Only {len(named)} viable named investor(s); archetype profiles fill the remaining rows.")

    run = MatchRun(
        matches=matches, archetype_matches=archetype_matches, rejected=rejected,
        used_archetype_fallback=fallback, investors_considered=len(investors),
        weights={k: round(v, 2) for k, v in config.weights.normalized().items()}, warnings=warnings,
    )
    return RankingOutput(run=run, records=records)
