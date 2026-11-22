"""Turns scored matches into explained recommendations, a next action, and data gaps."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from app.config import MatchingConfig
from app.intelligence.concerns import likely_concerns
from app.intelligence.outreach import (
    investment_evidence,
    outreach_angle,
    required_materials,
    why_it_fits,
)
from app.matching.taxonomy import REGULATED_SUBSECTORS
from app.models.company import FIELD_SPECS, CompanyProfile, InvestorPreferences
from app.models.investor import InvestorRecord
from app.models.match import MatchRun

if TYPE_CHECKING:
    from app.services.investor_data_service import InvestorDataset


def explain_matches(run: MatchRun, company: CompanyProfile, records: dict[str, InvestorRecord],
                    config: MatchingConfig, *, needs_lead: bool, today: date) -> None:
    """Fill rationale, evidence, concerns, angle and materials on every match (in place)."""
    for match in [*run.matches, *run.archetype_matches]:
        inv = records.get(match.investor_id)
        if inv is None:
            continue
        match.rationale = why_it_fits(inv, company, match, today)
        match.investment_evidence = investment_evidence(inv, company, today)
        match.likely_concerns = likely_concerns(inv, company, match, needs_lead=needs_lead,
                                                config=config, today=today)
        match.outreach_angle = outreach_angle(inv, company, match, needs_lead=needs_lead, today=today)
        match.required_materials = required_materials(inv, company, match, needs_lead=needs_lead)


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def recommended_next_action(run: MatchRun, company: CompanyProfile, preferences: InvestorPreferences,
                            *, manual_fields: list[str]) -> str:
    """The single most important next step for investor outreach."""
    critical = [FIELD_SPECS[f].label.lower() for f in manual_fields if FIELD_SPECS[f].ranking_impact >= 3]
    if critical:
        return (f"Confirm the {_join(critical)} with the company before any outreach — these drive stage "
                "and check-size fit and could reorder every match.")
    needs_lead = company.needs_lead(preferences.lead_required)
    tier1 = [m for m in run.matches if m.outreach_priority == "tier_1"]
    if tier1:
        names = _join([m.investor_name for m in tier1[:2]])
        if needs_lead:
            regulated = company.subsector.value in REGULATED_SUBSECTORS
            package = "deck, financial model, cap table, terms" + (", regulatory roadmap" if regulated else "")
            return (f"Secure a lead: after partner review, request warm introductions to {names} with a lead "
                    f"package ({package}).")
        return f"After partner review, request warm introductions to {names} with the deck and executive summary."
    tier2 = [m for m in run.matches if m.outreach_priority == "tier_2"]
    if tier2:
        names = _join([m.investor_name for m in tier2[:2]])
        return (f"Verify the mandates of {names} (check size, current fund activity) before outreach; no "
                "investor yet qualifies as Contact Now.")
    if run.archetype_matches:
        return (f"Source named investors matching the top profile ({run.archetype_matches[0].investor_name}); "
                "no named investor in the dataset is a strong current-round fit.")
    return "Add investor data or complete the missing company fields, then re-run the match."


def data_gaps(company: CompanyProfile, run: MatchRun, dataset: InvestorDataset | None, *,
              deck_scanned: bool = False) -> list[str]:
    """Missing or weak information that could materially change the ranking, most important first."""
    gaps: list[str] = []
    if deck_scanned:
        gaps.append("Deck text was not extractable (scanned); company fields rely on manual entry.")
    if dataset is not None and dataset.any_synthetic:
        gaps.append("Investor data is a SYNTHETIC test dataset — named firms are fictitious; do not use for outreach.")
    high = [FIELD_SPECS[f].label for f in company.missing_fields(min_impact=3)]
    if high:
        gaps.append(f"{', '.join(high)} not in deck — related fit criteria scored as unknown and confidence reduced.")
    mid = [FIELD_SPECS[f].label for f in company.missing_fields(min_impact=2)
           if FIELD_SPECS[f].ranking_impact == 2]
    if mid:
        gaps.append(f"Not in deck: {', '.join(mid)}.")
    inferred = [FIELD_SPECS[n].label.lower() for n in ("subsector", "company_stage", "business_model",
                                                       "strategic_needs", "product_stage")
                if company.field(n).known and company.field(n).source == "inference"]
    if inferred:
        gaps.append(f"Inferred, not stated in the deck: {', '.join(inferred)} — confirm in review.")
    named = [m for m in run.matches if m.outreach_priority != "do_not_target"]
    weak = [m for m in named if m.evidence_quality not in {"verified", "strong"}]
    if weak:
        gaps.append(f"{len(weak)} of {len(named)} ranked investors rest on moderate or weaker evidence; "
                    "verify mandates before outreach.")
    if run.rejected:
        gaps.append(f"{len(run.rejected)} investor(s) removed by hard filters (reasons in the JSON audit).")
    if run.used_archetype_fallback:
        gaps.append("Too few viable named investors; archetype profiles (categories, not firms) fill the list.")
    return gaps
