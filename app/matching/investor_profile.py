"""Ideal Investor Profile (IIP) and investor archetypes.

The IIP answers "who should fund this company *now*". It is analyst inference from the
normalized company record and is labeled as such. The category heuristics below (typical
stages, check sizes and lead behaviour by investor type) are generic market conventions,
not claims about any specific investor. When named-investor evidence is thin, the same
specs produce archetype profiles that are ranked alongside - never disguised as - named
investors.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.matching.taxonomy import (
    CAPITAL_INTENSIVE_SUBSECTORS,
    REGULATED_SUBSECTORS,
    REVENUE_STAGE_INDEX,
    REVENUE_STAGES,
    STRATEGIC_TAGS,
    family_label,
    family_of,
    investor_type_label,
    stage_label,
    subsector_label,
)
from app.models.company import CompanyProfile, InvestorPreferences
from app.models.investor import InvestorRecord
from app.models.match import CategoryRecommendation, IdealInvestorProfile
from app.utils.geography import describe_location, describe_token
from app.utils.money import format_money, format_money_range

#: Share of the round a lead typically takes, and the range a meaningful follower takes.
LEAD_SHARE = (0.25, 0.60)
FOLLOW_SHARE = (0.05, 0.25)
ARCHETYPE_SOURCE = "TEN Capital archetype library (analyst heuristics)"


@dataclass(frozen=True)
class CategorySpec:
    key: str
    stages: tuple[str, ...]
    check_min: float
    check_max: float
    leads: str
    families: frozenset[str] | None = None
    subsectors: frozenset[str] | None = None
    revenue_floor: str | None = None
    strategic: tuple[str, ...] = ()
    note: str = ""


_LATER = ("series_a", "series_b", "series_c", "growth")

CATEGORY_SPECS: dict[str, CategorySpec] = {s.key: s for s in (
    CategorySpec("angel", ("pre_seed", "seed"), 10e3, 150e3, "no",
                 note="Individual checks; rarely sets terms."),
    CategorySpec("angel_group", ("pre_seed", "seed", "series_a"), 50e3, 750e3, "sometimes",
                 note="Syndicates member checks; usually wants a named lead."),
    CategorySpec("syndicate", ("pre_seed", "seed", "series_a"), 100e3, 1.5e6, "no",
                 note="SPV that invests behind a lead."),
    CategorySpec("seed_fund", ("pre_seed", "seed"), 250e3, 2e6, "yes"),
    CategorySpec("micro_vc", ("pre_seed", "seed", "series_a"), 250e3, 3e6, "yes"),
    CategorySpec("venture_capital", ("seed", "series_a", "series_b", "series_c"), 1e6, 15e6, "yes",
                 strategic=("follow_on_capital", "recruiting", "commercialization")),
    CategorySpec("growth_equity", ("series_c", "growth"), 10e6, 100e6, "yes", revenue_floor="revenue"),
    CategorySpec("family_office", ("seed", "series_a", "series_b", "series_c", "growth"), 250e3, 10e6,
                 "sometimes", note="Often co-invests alongside a specialist lead."),
    CategorySpec("corporate_vc", ("series_a", "series_b", "series_c"), 1e6, 10e6, "sometimes",
                 strategic=("distribution", "customer_access", "technical", "partnering")),
    CategorySpec("strategic", _LATER, 2e6, 50e6, "sometimes",
                 strategic=("distribution", "partnering", "manufacturing")),
    CategorySpec("healthcare_system", ("seed", "series_a", "series_b"), 500e3, 5e6, "no",
                 subsectors=frozenset({"diagnostics", "medtech", "digital_health", "healthcare_services"}),
                 strategic=("clinical_validation", "customer_access", "reimbursement")),
    CategorySpec("pharma", _LATER, 5e6, 50e6, "sometimes",
                 subsectors=frozenset({"therapeutics", "diagnostics"}),
                 strategic=("clinical_validation", "regulatory", "partnering", "commercialization")),
    CategorySpec("medtech_strategic", _LATER, 2e6, 25e6, "sometimes",
                 subsectors=frozenset({"medtech", "diagnostics", "digital_health"}),
                 strategic=("distribution", "manufacturing", "regulatory", "commercialization")),
    CategorySpec("industrial_strategic", _LATER, 2e6, 25e6, "sometimes",
                 families=frozenset({"industrial", "climate"}),
                 strategic=("manufacturing", "distribution", "customer_access")),
    CategorySpec("private_equity", ("growth",), 20e6, 200e6, "yes", revenue_floor="scaling_revenue"),
    CategorySpec("impact", ("seed", "series_a", "series_b"), 500e3, 5e6, "sometimes",
                 families=frozenset({"healthcare", "climate"}),
                 subsectors=frozenset({"edtech", "fintech", "agtech_food"})),
    CategorySpec("government_fund", ("pre_seed", "seed", "series_a"), 250e3, 5e6, "no",
                 families=frozenset({"healthcare", "climate", "industrial"}),
                 note="Eligibility depends on the program's region."),
    CategorySpec("sovereign", ("growth",), 50e6, 500e6, "yes", revenue_floor="scaling_revenue"),
)}

_TECHNICAL: dict[str, str] = {
    "diagnostics": "assay/biomarker validation and clinical-lab workflow",
    "medtech": "device engineering and design controls",
    "therapeutics": "drug development, CMC and trial design",
    "life_science_tools": "instrument and reagent development",
    "digital_health": "clinical software and data integration",
    "ai_ml": "applied ML and data infrastructure",
    "robotics_hardware": "hardware engineering and manufacturing",
    "energy": "energy hardware and project finance",
    "climate_tech": "climate hardware and carbon markets",
    "cybersecurity": "security engineering",
    "fintech": "financial infrastructure and compliance",
    "aerospace_defense": "defense procurement and hardware",
    "advanced_manufacturing": "industrial process engineering",
}
_COMMERCIAL: dict[str, str] = {
    "healthcare": "hospital and health-system go-to-market",
    "software": "B2B go-to-market",
    "climate": "utility and industrial sales",
    "industrial": "industrial channel sales",
    "consumer": "consumer distribution",
}


def _known(item) -> object | None:
    return item.value if item.known else None


def _sector_ok(spec: CategorySpec, sub: str | None, fam: str | None) -> bool | None:
    if spec.families is None and spec.subsectors is None:
        return True
    if sub is None and fam is None:
        return None
    return (fam in (spec.families or frozenset())) or (sub in (spec.subsectors or frozenset()))


def evaluate_category(spec: CategorySpec, company: CompanyProfile,
                      preferences: InvestorPreferences) -> tuple[bool, str, float]:
    """(eligible, rationale or exclusion reason, relevance) for one investor category."""
    if spec.key in preferences.excluded_types:
        return False, "Excluded by user preference", 0.0
    stage = _known(company.company_stage)
    raise_amount = _known(company.raise_amount)
    sub = _known(company.subsector)
    fam = _known(company.sector) or family_of(sub)

    if stage and stage not in spec.stages:
        focus = ", ".join(stage_label(s) for s in spec.stages)
        return False, f"Typically invests at {focus}, not {stage_label(stage)}", 0.0
    sector_ok = _sector_ok(spec, sub, fam)
    if sector_ok is False:
        return False, "Sector-specific category not relevant to this company", 0.0
    if sector_ok is None:
        return False, "Sector-specific category; company sector not established", 0.0
    if raise_amount:
        if spec.check_max < FOLLOW_SHARE[0] * raise_amount:
            return False, (f"Typical checks ({format_money_range(spec.check_min, spec.check_max)}) "
                           f"too small for a {format_money(raise_amount)} round"), 0.0
        if spec.check_min > LEAD_SHARE[1] * raise_amount:
            return False, (f"Typical minimum check ({format_money(spec.check_min)}) exceeds most "
                           f"of the {format_money(raise_amount)} round"), 0.0
    if spec.revenue_floor:
        revenue_stage = _known(company.revenue_stage)
        if revenue_stage is None or REVENUE_STAGE_INDEX[revenue_stage] < REVENUE_STAGE_INDEX[spec.revenue_floor]:
            return False, f"Requires {REVENUE_STAGES[spec.revenue_floor].lower()}", 0.0

    lead_required = company.needs_lead(preferences.lead_required)
    needs = set(_known(company.strategic_needs) or [])
    relevance = 1.0
    reasons: list[str] = []
    if lead_required and spec.leads == "yes":
        relevance += 3
        reasons.append("can lead the round")
    elif lead_required and spec.leads == "sometimes":
        relevance += 1.5
        reasons.append("sometimes leads")
    overlap = [t for t in spec.strategic if t in needs]
    if overlap:
        relevance += min(3, len(overlap))
        reasons.append("brings " + ", ".join(STRATEGIC_TAGS[t].lower() for t in overlap[:2]))
    if spec.families or spec.subsectors:
        relevance += 2
        reasons.append(f"{subsector_label(sub) if sub else family_label(fam)}-relevant mandate")
    if spec.key in preferences.preferred_types:
        relevance += 1
        reasons.append("matches your preferred types")
    if raise_amount:
        reasons.append(f"typical checks {format_money_range(spec.check_min, spec.check_max)}")
    text = "; ".join(reasons) if reasons else "stage and check size fit"
    return True, text[0].upper() + text[1:], relevance


def _regulatory_expertise(company: CompanyProfile) -> str | None:
    sub = _known(company.subsector)
    if sub not in REGULATED_SUBSECTORS:
        return None
    status = (_known(company.regulatory_status) or "").lower()
    if "510" in status or "de novo" in status or "breakthrough" in status:
        return "FDA device pathway experience (510(k)/De Novo, Breakthrough programs)"
    if "fda" in status or family_of(sub) == "healthcare":
        return "FDA/clinical regulatory experience"
    return "Regulatory approval and licensing experience"


def build_ideal_investor_profile(company: CompanyProfile,
                                 preferences: InvestorPreferences) -> IdealInvestorProfile:
    stage = _known(company.company_stage)
    raise_amount = _known(company.raise_amount)
    sub = _known(company.subsector)
    fam = _known(company.sector) or family_of(sub)
    needs = set(_known(company.strategic_needs) or [])
    location = company.location()

    lead_required = company.needs_lead(preferences.lead_required)
    if preferences.lead_required is not None:
        lead_basis = "Set by user"
    elif company.lead_investor.known:
        lead_basis = f"Lead named in deck: {company.lead_investor.value}"
    elif company.seeking_lead.known and company.seeking_lead.value:
        lead_basis = f"Deck states the round is seeking a lead ({company.seeking_lead.location})"
    else:
        lead_basis = "No lead named in the deck (assumed still needed)"

    recommended: list[tuple[float, CategoryRecommendation]] = []
    excluded: list[CategoryRecommendation] = []
    for spec in CATEGORY_SPECS.values():
        eligible, text, relevance = evaluate_category(spec, company, preferences)
        rec = CategoryRecommendation(investor_type=spec.key, label=investor_type_label(spec.key),
                                     rationale=text)
        if eligible:
            recommended.append((relevance, rec))
        else:
            excluded.append(rec)
    recommended.sort(key=lambda pair: -pair[0])

    lead_range = (LEAD_SHARE[0] * raise_amount, LEAD_SHARE[1] * raise_amount) if raise_amount else (None, None)
    follow_range = (FOLLOW_SHARE[0] * raise_amount, FOLLOW_SHARE[1] * raise_amount) if raise_amount else (None, None)

    technical = _TECHNICAL.get(sub or "")
    commercial = None
    if _known(company.product_stage) in {"pilot", "commercial", "scaling"} or \
            _known(company.revenue_stage) in {"early_revenue", "revenue", "scaling_revenue"}:
        commercial = _COMMERCIAL.get(fam or "")
    regulatory = _regulatory_expertise(company)
    cross_border = None
    if location and location.country and location.country != "US":
        cross_border = f"Comfort investing in {describe_token(location.country)}-based companies"
    elif "international" in needs:
        cross_border = "Cross-border expansion support"
    follow_on = ("Deep reserves for Series B+ (capital-intensive subsector)"
                 if sub in CAPITAL_INTENSIVE_SUBSECTORS else "Standard follow-on reserves")
    strategic_needs = needs & {"distribution", "manufacturing", "customer_access", "clinical_validation",
                               "partnering"}
    if strategic_needs:
        strategic_vs_financial = ("Financial lead plus a strategic co-investor for "
                                  + ", ".join(STRATEGIC_TAGS[t].lower() for t in sorted(strategic_needs)[:2]))
    else:
        strategic_vs_financial = "Financial investor"
    partnership = None
    if any(r.investor_type in {"corporate_vc", "strategic", "medtech_strategic", "pharma",
                               "industrial_strategic", "healthcare_system"} for _rel, r in recommended):
        partnership = f"Corporate/strategic investors active in {family_label(fam).lower()}"
    channel = None
    if needs & {"customer_access", "distribution"}:
        channel = "Introductions to buyers and distribution channels"

    geo_text = ""
    if location is not None:
        parts = [describe_token(location.subdivision)] if location.subdivision else []
        if location.country:
            parts.append(describe_token(location.country))
        geo_text = f"Mandate covering {' / '.join(parts) or describe_location(location)}"

    points: list[str] = []
    if stage and raise_amount:
        prefix = "Lead-capable " if lead_required else ""
        points.append(f"{prefix}{stage_label(stage)} investor able to write "
                      f"{format_money_range(*lead_range)} into the {format_money(raise_amount)} round")
    elif stage:
        points.append(f"{stage_label(stage)} investor{' able to lead' if lead_required else ''} "
                      "(round size not established)")
    else:
        points.append("Stage and round size not established - confirm before targeting")
    if sub:
        points.append(f"{subsector_label(sub)} specialist" + (f" with {technical} depth" if technical else ""))
    elif fam:
        points.append(f"{family_label(fam)} investor")
    if regulatory:
        points.append(f"Comfortable with regulatory risk: {regulatory}")
    if strategic_needs:
        points.append(strategic_vs_financial)
    elif commercial:
        points.append(f"Adds {commercial} expertise")
    if geo_text:
        points.append(geo_text)

    return IdealInvestorProfile(
        target_stages=[stage] if stage else [],
        recommended_categories=[r for _rel, r in recommended],
        excluded_categories=excluded,
        sector_specialization=(f"{subsector_label(sub)} specialists, or {family_label(fam)} investors "
                               f"with {subsector_label(sub).lower()} deals") if sub else
        (f"{family_label(fam)} investors" if fam else "Sector not established"),
        geographic_fit=geo_text or "Company location not established",
        lead_check_range=lead_range,
        follow_check_range=follow_range,
        lead_required=lead_required,
        lead_basis=lead_basis,
        strategic_vs_financial=strategic_vs_financial,
        technical_expertise=technical,
        commercialization_expertise=commercial,
        regulatory_expertise=regulatory,
        cross_border_capability=cross_border,
        follow_on_capacity=follow_on,
        strategic_partnership_potential=partnership,
        customer_channel_access=channel,
        summary_points=points[:5],
    )


def archetype_name(spec: CategorySpec, company: CompanyProfile) -> str:
    base = investor_type_label(spec.key)
    sub = _known(company.subsector)
    if spec.families or spec.subsectors or not sub:
        return base
    return f"{subsector_label(sub)}-focused {base[0].lower()}{base[1:]}"


def build_archetypes(company: CompanyProfile, iip: IdealInvestorProfile,
                     preferences: InvestorPreferences, *, limit: int = 6) -> list[InvestorRecord]:
    """Investor profiles for the top recommended categories - explicitly not named firms."""
    location = company.location()
    geographies: list[str] = []
    if location is not None and location.country:
        geographies = [describe_token(location.country)]
    elif location is not None and location.macro_region:
        geographies = [describe_token(location.macro_region)]
    sub = _known(company.subsector)
    fam = _known(company.sector) or family_of(sub)
    sector_prefs = [sub] if sub else ([fam] if fam else [])

    archetypes: list[InvestorRecord] = []
    for rec in iip.recommended_categories:
        if rec.investor_type in preferences.excluded_types:
            continue
        spec = CATEGORY_SPECS[rec.investor_type]
        archetypes.append(InvestorRecord(
            investor_id=f"archetype:{spec.key}",
            name=archetype_name(spec, company),
            investor_type=spec.key,
            investment_geographies=geographies,
            sector_preferences=sector_prefs,
            stage_preferences=list(spec.stages),
            min_check=spec.check_min,
            max_check=spec.check_max,
            lead_capability=spec.leads,  # type: ignore[arg-type]
            strategic_capabilities=list(spec.strategic),
            data_source=ARCHETYPE_SOURCE,
            is_archetype=True,
            notes=spec.note or None,
        ))
        if len(archetypes) >= limit:
            break
    return archetypes
