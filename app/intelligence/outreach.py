"""Why it fits, investment evidence, outreach angle, and required introduction materials.

All text is assembled from facts in the company record (with their deck locations) and
the investor record. Investor names, portfolio companies and deals only ever come from
the investor dataset - never generated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.matching.investor_profile import ARCHETYPE_SOURCE
from app.matching.scoring import describe_deal, relevant_deals
from app.matching.taxonomy import (
    REGULATED_SUBSECTORS,
    STRATEGIC_TYPES,
    business_model_label,
    describe_sector_tokens,
    family_label,
    family_of,
    investor_type_label,
    stage_label,
    strategic_label,
    subsector_label,
)
from app.models.company import CompanyProfile
from app.models.investor import InvestorRecord
from app.models.match import InvestorMatch
from app.utils.geography import describe_token, geography_match
from app.utils.money import format_money, format_money_range
from app.utils.text import truncate

_ACHIEVED = re.compile(r"granted|received|cleared|approved|designation|ce[\s-]marked|submitted|filed", re.I)
#: "granted in March 2025" -> "(Mar 2025)": same fact, fewer characters in a table cell.
_DATED_VERB = re.compile(r"\s+(?:was\s+)?(?:granted|received|obtained|awarded|achieved)\s+in\s+"
                         r"([A-Z][a-z]+)\s+((?:19|20)\d\d)")
_TECH_SUBSECTORS = frozenset({"robotics_hardware", "ai_ml", "energy", "climate_tech",
                              "advanced_manufacturing", "aerospace_defense", "life_science_tools"})


@dataclass(frozen=True)
class ProofPoint:
    kind: str
    text: str
    location: str | None


def _clause(text: str, limit: int) -> str:
    first = re.split(r";|\s\|\s", text)[0].strip().rstrip(".")
    first = _DATED_VERB.sub(lambda m: f" ({m.group(1)[:3]} {m.group(2)})", first)
    return truncate(first, limit)


def proof_points(company: CompanyProfile) -> list[ProofPoint]:
    """The company's strongest evidenced assets, most persuasive first."""
    points: list[ProofPoint] = []
    reg = company.regulatory_status
    if reg.known and _ACHIEVED.search(reg.value or ""):
        points.append(ProofPoint("regulatory", _clause(reg.value, 70), reg.location))
    if company.revenue.known:
        text = f"{format_money(company.revenue.value)} revenue"
        if company.revenue_growth.known:
            text += f" (+{company.revenue_growth.value:g}% growth)"
        points.append(ProofPoint("revenue", text, company.revenue.location))
    if company.customers.known:
        points.append(ProofPoint("customers", company.customers.value, company.customers.location))
    if company.round_committed.known and company.raise_amount.known:
        points.append(ProofPoint(
            "committed",
            f"{format_money(company.round_committed.value)} of the {format_money(company.raise_amount.value)} already committed",
            company.round_committed.location))
    if company.intellectual_property.known:
        points.append(ProofPoint("ip", _clause(company.intellectual_property.value, 60),
                                 company.intellectual_property.location))
    if company.partnerships.known:
        points.append(ProofPoint("partnership", _clause(company.partnerships.value, 60),
                                 company.partnerships.location))
    if company.market_size.known:
        points.append(ProofPoint("market", f"{format_money(company.market_size.value)} TAM",
                                 company.market_size.location))
    return points


def _stage_text(company: CompanyProfile) -> str:
    return stage_label(company.company_stage.value) if company.company_stage.known else "current"


def _sub_text(company: CompanyProfile) -> str:
    if company.subsector.known:
        return subsector_label(company.subsector.value).lower()
    if company.sector.known:
        return family_label(company.sector.value).lower()
    return "sector"


def why_it_fits(inv: InvestorRecord, company: CompanyProfile, match: InvestorMatch, today: date) -> str:
    phrases: list[str] = []
    ordered = sorted((c for c in match.breakdown if c.known and c.raw_score >= 60),
                     key=lambda c: -c.weighted_score)
    for c in ordered:
        phrase: str | None = None
        if c.criterion == "stage" and c.raw_score >= 100:
            phrase = f"{_stage_text(company)} investor"
        elif c.criterion == "sector":
            if c.raw_score >= 100:
                phrase = f"core {_sub_text(company)} mandate"
            elif c.raw_score >= 75:
                phrase = f"invests across {family_label(family_of(company.subsector.value) or company.sector.value).lower()}"
            else:
                phrase = f"adjacent focus ({describe_sector_tokens(inv.sector_tokens()).lower()})"
        elif c.criterion == "check_size":
            span = format_money_range(*inv.check_range())
            if c.raw_score >= 100:
                # "Lead allocation" would mislead for an investor that only follows.
                phrase = (f"{span} checks fit a lead allocation" if inv.lead_capability in {"yes", "sometimes"}
                          else f"{span} checks fit this round")
            elif c.raw_score >= 75:
                phrase = f"{span} checks fit a follow-on allocation"
            else:
                phrase = f"{span} checks would take most of the round"
        elif c.criterion == "geography":
            location = company.location()
            if location is not None:
                _score, token = geography_match(location.tokens, inv.geo_tokens())
                if token:
                    phrase = f"mandate covers {describe_token(token)}"
        elif c.criterion == "business_model" and c.raw_score >= 100:
            overlap = set(company.business_model.value or []) & inv.business_model_tags()
            phrase = f"backs {business_model_label(sorted(overlap)).lower()} models"
        elif c.criterion == "traction" and c.raw_score >= 100:
            phrase = "traction meets its entry bar"
        elif c.criterion == "historical_evidence":
            deals = relevant_deals(inv, company, today)
            if deals:
                names = ", ".join(d.company for d, _w in deals[:2])
                phrase = f"{len(deals)} relevant deal{'s' if len(deals) != 1 else ''} ({names})"
        elif c.criterion == "strategic_value" and c.raw_score >= 70:
            overlap = set(company.strategic_needs.value or []) & inv.strategic_tags()
            phrase = f"offers {strategic_label(sorted(overlap)[:2]).lower()}"
        elif c.criterion == "lead_capability" and c.raw_score >= 85:
            phrase = "leads rounds" if inv.lead_capability == "yes" else "comfortable following"
        if phrase:
            phrases.append(phrase)
        if len(phrases) == 3:
            break
    if not phrases:
        return "No criterion scored as a clear fit; ranked on partial data."
    text = "; ".join(phrases)
    return truncate(text[0].upper() + text[1:] + ".", 220)


def investment_evidence(inv: InvestorRecord, company: CompanyProfile, today: date) -> list[str]:
    if inv.is_archetype:
        return [f"No named-investor evidence — category profile from the {ARCHETYPE_SOURCE}."]
    evidence: list[str] = []
    deals = relevant_deals(inv, company, today)
    for deal, _weight in deals[:3]:
        sector = f" — {deal.sector}" if deal.sector else ""
        evidence.append(f"Deal: {describe_deal(deal)}{sector}")
    if not deals and inv.historical_investments:
        evidence.append("Recorded deals outside this sector: "
                        + ", ".join(describe_deal(d) for d in inv.historical_investments[:2]))
    if inv.portfolio_companies:
        evidence.append("Portfolio: " + ", ".join(inv.portfolio_companies[:4]))
    mandate: list[str] = []
    if inv.sector_preferences:
        mandate.append(", ".join(inv.sector_preferences))
    if inv.stage_preferences:
        mandate.append(", ".join(stage_label(s) for s in inv.stage_preferences))
    low, high = inv.check_range()
    if low is not None or high is not None:
        mandate.append(f"checks {format_money_range(low, high)}")
    if mandate:
        evidence.append("Stated focus: " + "; ".join(mandate))
    if inv.fund_status != "unknown":
        note = f" ({inv.fund_status_note})" if inv.fund_status_note else ""
        evidence.append(f"Fund status: {inv.fund_status.replace('_', ' ')}{note}")
    if inv.source_urls or inv.last_verified:
        verified = f"; last verified {inv.last_verified:%Y-%m-%d}" if inv.last_verified else ""
        evidence.append(f"Sources: {len(inv.source_urls)} URL(s){verified}")
    else:
        evidence.append("No source URLs or verification date on record")
    evidence.append(f"Dataset: {inv.data_source}{' (synthetic)' if inv.is_synthetic else ''}")
    return evidence


_ARCHETYPE_HOOKS: dict[str, str] = {
    "healthcare_system": "clinical validation and adoption pathway inside their hospitals",
    "medtech_strategic": "distribution, manufacturing and regulatory fit",
    "pharma": "partnering and clinical development fit",
    "corporate_vc": "commercial partnership upside",
    "strategic": "commercial partnership upside",
    "industrial_strategic": "channel and manufacturing fit",
    "venture_capital": "lead-sized ownership at this stage",
    "micro_vc": "early ownership at a sensible entry price",
    "seed_fund": "early ownership at a sensible entry price",
    "family_office": "long-horizon capital alongside a specialist lead",
    "angel_group": "member interest behind a named lead",
    "impact": "measurable impact alongside returns",
    "government_fund": "program eligibility and matching capital",
}


def outreach_angle(inv: InvestorRecord, company: CompanyProfile, match: InvestorMatch, *,
                   needs_lead: bool, today: date) -> str:
    """The strongest investor-specific positioning, in one or two short sentences."""
    points = proof_points(company)
    p1 = points[0].text if points else None
    p2 = points[1].text if len(points) > 1 else None
    name = company.display_name
    stage = _stage_text(company)
    sub = _sub_text(company)
    round_size = company.raise_amount.value if company.raise_amount.known else None

    if inv.is_archetype:
        hook = _ARCHETYPE_HOOKS.get(inv.investor_type, "fit with their stage and check size")
        lead = p1 or "the core value proposition"
        return truncate(f"Source {investor_type_label(inv.investor_type).lower()}s active in {sub}; "
                        f"lead with {lead} and the {hook}.", 220)

    history = match.criterion("historical_evidence")
    strategic = match.criterion("strategic_value")
    sector = match.criterion("sector")
    deals = relevant_deals(inv, company, today)
    lead_seat = bool(needs_lead and inv.lead_capability in {"yes", "sometimes"} and round_size)

    if history is not None and history.known and history.raw_score >= 50 and deals:
        names = " and ".join(d.company for d, _w in deals[:2])
        text = f"Lead with {p1}" if p1 else f"Lead with the {sub} thesis"
        text += f"; it mirrors their {names} deal{'s' if len(deals[:2]) > 1 else ''}"
        if lead_seat:
            text += ". Offer the lead seat"
    elif strategic is not None and strategic.known and strategic.raw_score >= 70:
        overlap = sorted(set(company.strategic_needs.value or []) & inv.strategic_tags())
        need = strategic_label(overlap[:1]).lower()
        text = f"Strategic fit: they offer the {need} {name} needs"
        if p1:
            text += f"; cite {p1}"
    elif lead_seat and inv.lead_capability == "yes":
        text = f"Pitch the lead seat in the {format_money(round_size)} {stage}"
        if company.round_committed.known:
            text += f" ({format_money(company.round_committed.value)} committed)"
        if p1:
            text += f"; anchor on {p1}"
    elif sector is not None and sector.known and sector.raw_score >= 75:
        text = f"Position as a core {sub} deal" + (f": {p1}" if p1 else "") + (f" and {p2}" if p2 else "")
    else:
        text = (f"Open with {p1}" if p1 else "Open with the core value proposition") + \
            f"; confirm their {stage} {sub} mandate first"
    return truncate(text + ".", 220)


def required_materials(inv: InvestorRecord, company: CompanyProfile, match: InvestorMatch, *,
                       needs_lead: bool) -> list[str]:
    materials = ["Executive summary", "Pitch deck"]
    sub = company.subsector.value if company.subsector.known else None
    if (needs_lead and inv.lead_capability in {"yes", "sometimes"}) or match.outreach_priority == "tier_1":
        materials += ["Financial model", "Cap table", "Investment terms"]
    if sub in REGULATED_SUBSECTORS:
        materials.append("Regulatory roadmap")
    if sub in {"diagnostics", "medtech", "therapeutics", "digital_health"}:
        materials.append("Clinical results")
    if company.intellectual_property.known:
        materials.append("IP summary")
    if company.customers.known or company.partnerships.known:
        materials.append("Customer references")
    if inv.investor_type in STRATEGIC_TYPES or sub in _TECH_SUBSECTORS:
        materials.append("Technical validation")
    if company.raise_amount.known:
        materials.append("Use-of-funds schedule")
    if not company.market_size.known:
        materials.append("Market analysis")
    if match.outreach_priority == "tier_1":
        materials.append("Data room")
    return list(dict.fromkeys(materials))[:8]
