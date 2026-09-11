"""Hard filters: remove clearly incompatible investors before scoring.

A filter fires only on *known* data on both sides. Missing data never eliminates an
investor (it lowers the score and confidence instead). Every rejection is kept, with all
of its reasons, in the audit record.
"""

from __future__ import annotations

from app.matching.taxonomy import (
    family_label,
    family_of,
    investor_type_label,
    stage_distance,
    stage_label,
    subsector_label,
)
from app.models.company import CompanyProfile, InvestorPreferences
from app.models.investor import NOT_INVESTING_STATUSES, InvestorRecord
from app.models.match import FilterReason, RejectedInvestor
from app.utils.geography import geography_match
from app.utils.money import format_money
from app.utils.text import normalize_name


def company_sector(company: CompanyProfile) -> tuple[str | None, str | None]:
    sub = company.subsector.value if company.subsector.known else None
    fam = company.sector.value if company.sector.known else family_of(sub)
    return sub, fam


def sector_overlap(tokens: set[str], sub: str | None, fam: str | None) -> bool:
    for token in tokens:
        if token == "generalist":
            return True
        if fam and token == f"fam:{fam}":
            return True
        if token.startswith("sub:") and (token[4:] == sub or (fam and family_of(token[4:]) == fam)):
            return True
    return False


def is_existing_investor(investor: InvestorRecord, company: CompanyProfile) -> bool:
    names = {normalize_name(n) for n in (company.existing_investors.value or [])}
    return normalize_name(investor.name) in names


def check_investor(investor: InvestorRecord, company: CompanyProfile,
                   preferences: InvestorPreferences) -> list[FilterReason]:
    reasons: list[FilterReason] = []
    add = lambda code, detail: reasons.append(FilterReason(code=code, detail=detail))  # noqa: E731

    excluded_names = {normalize_name(n) for n in preferences.excluded_investors}
    if normalize_name(investor.name) in excluded_names:
        add("excluded_by_user", "Excluded by the user.")
    if investor.investor_type in preferences.excluded_types:
        add("excluded_type", f"Investor type excluded by user: {investor_type_label(investor.investor_type)}.")

    if investor.fund_status in NOT_INVESTING_STATUSES:
        note = f" ({investor.fund_status_note})" if investor.fund_status_note else ""
        add("fund_not_investing", f"Fund not actively investing{note}.")

    stage = company.company_stage.value if company.company_stage.known else None
    if stage and investor.stage_preferences:
        distance = stage_distance(stage, investor.stage_preferences)
        if distance is not None and distance >= 2:
            focus = ", ".join(stage_label(s) for s in investor.stage_preferences)
            add("stage_mismatch", f"Invests at {focus}; company is raising {stage_label(stage)}.")

    sub, fam = company_sector(company)
    if sub or fam:
        excluded_tokens = investor.excluded_sector_tokens()
        hit = [t for t in excluded_tokens if t in {f"sub:{sub}", f"fam:{fam}"}]
        if hit:
            add("sector_exclusion", f"Excludes {subsector_label(sub) if sub else family_label(fam)}.")
        tokens = investor.sector_tokens()
        if tokens and not sector_overlap(tokens, sub, fam):
            add("mandate_mismatch", (f"Sector mandate ({', '.join(investor.sector_preferences)}) does not "
                                     f"cover {subsector_label(sub) if sub else family_label(fam)}."))

    location = company.location()
    if location is not None:
        geo_tokens = investor.geo_tokens()
        if geo_tokens and "GLOBAL" not in geo_tokens:
            score, _token = geography_match(location.tokens, geo_tokens)
            if score == 0:
                add("geography_restriction", (f"Mandate limited to {', '.join(investor.investment_geographies)}; "
                                              f"company is in {location.label}."))
        if location.tokens & investor.excluded_geo_tokens():
            add("excluded_geography", f"Excludes companies in {location.label}.")

    raise_amount = company.raise_amount.value if company.raise_amount.known else None
    low, _high = investor.check_range()
    if raise_amount and low is not None and low > raise_amount:
        add("check_size_incompatible", (f"Minimum check {format_money(low)} exceeds the "
                                        f"{format_money(raise_amount)} round."))

    models = set(company.business_model.value or [])
    if models & investor.excluded_business_model_tags():
        add("business_model_exclusion", "Excludes the company's business model.")

    competitors = {normalize_name(c): c for c in (company.competitors.value or [])}
    if competitors:
        holdings = list(investor.portfolio_companies) + [d.company for d in investor.historical_investments]
        clashes = sorted({competitors[normalize_name(h)] for h in holdings if normalize_name(h) in competitors})
        if clashes:
            where = f" ({company.competitors.location})" if company.competitors.location else ""
            add("portfolio_conflict", f"Portfolio includes {', '.join(clashes)}, named as a competitor in the deck{where}.")
    return reasons


def apply_hard_filters(investors: list[InvestorRecord], company: CompanyProfile,
                       preferences: InvestorPreferences) -> tuple[list[InvestorRecord], list[RejectedInvestor]]:
    viable: list[InvestorRecord] = []
    rejected: list[RejectedInvestor] = []
    for investor in investors:
        reasons = check_investor(investor, company, preferences)
        if reasons:
            rejected.append(RejectedInvestor(
                investor_id=investor.investor_id, investor_name=investor.name,
                investor_type=investor.investor_type, data_source=investor.data_source,
                reasons=reasons,
            ))
        else:
            viable.append(investor)
    return viable, rejected
