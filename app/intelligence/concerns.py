"""Likely concerns: the most probable reasons an investor would decline or delay.

Rule-based and severity-ranked. Every concern is derived from the company record, the
investor record, or the score breakdown - nothing is speculative about the investor
beyond what the data shows.
"""

from __future__ import annotations

import re
from datetime import date

from app.config import MatchingConfig
from app.matching.confidence import months_between
from app.matching.filters import is_existing_investor
from app.matching.scoring import FOLLOW_FLOOR, LEAD_BAND
from app.matching.taxonomy import (
    CAPITAL_INTENSIVE_SUBSECTORS,
    LONG_SALES_CYCLE_SUBSECTORS,
    PRODUCT_STAGES,
    REGULATED_SUBSECTORS,
    STAGE_INDEX,
    family_of,
)
from app.models.company import CompanyProfile
from app.models.investor import InvestorRecord
from app.models.match import InvestorMatch
from app.utils.money import format_money, format_money_range

SMALL_CHECK_TYPES = frozenset({"angel", "angel_group", "syndicate", "seed_fund", "micro_vc"})
_CLEARED = re.compile(r"cleared|approved|ce[\s-]marked", re.I)


def likely_concerns(inv: InvestorRecord, company: CompanyProfile, match: InvestorMatch, *,
                    needs_lead: bool, config: MatchingConfig, today: date, limit: int = 3) -> list[str]:
    found: list[tuple[int, str]] = []

    def add(severity: int, text: str) -> None:
        found.append((severity, text))

    round_size = company.raise_amount.value if company.raise_amount.known else None
    sub = company.subsector.value if company.subsector.known else None
    revenue_stage = company.revenue_stage.value if company.revenue_stage.known else None

    if inv.is_archetype:
        # Always first for a profile: the reader must not mistake it for a named firm.
        add(99, "Profile only — named firms in this category must be sourced and verified")
    if needs_lead and inv.lead_capability == "no":
        add(95, "Invests only behind a committed lead")
    if not inv.is_archetype and is_existing_investor(inv, company):
        add(90, "Already an investor — expect pro-rata support, not a new lead")

    check = match.criterion("check_size")
    low, high = inv.check_range()
    if round_size and check is not None and check.known:
        if high is not None and high < FOLLOW_FLOOR * round_size:
            add(85, f"Check size ({format_money_range(low, high)}) too small to anchor a "
                    f"{format_money(round_size)} round")
        elif low is not None and low > LEAD_BAND[1] * round_size:
            add(84, f"Would need to take most of the {format_money(round_size)} round")

    stage = match.criterion("stage")
    if stage is not None and stage.known and stage.raw_score == 60 and company.company_stage.known:
        earliest = min(STAGE_INDEX[s] for s in inv.stage_preferences)
        add(80, "Usually enters a round later" if earliest > STAGE_INDEX[company.company_stage.value]
            else "Usually invests a round earlier")

    traction = match.criterion("traction")
    if traction is not None and traction.known and traction.raw_score < 60:
        add(78, "Pre-revenue; typically expects revenue at entry" if revenue_stage == "pre_revenue"
            else "Revenue below its typical entry point")

    regulatory = company.regulatory_status.value or ""
    if sub in REGULATED_SUBSECTORS and not _CLEARED.search(regulatory) and "regulatory" not in inv.strategic_tags():
        healthcare = family_of(sub) == "healthcare" or re.search(r"fda|510", regulatory, re.I)
        add(75, "Early regulatory stage — no FDA clearance yet" if healthcare
            else "Regulatory approval still ahead")

    last = inv.last_known_investment
    if last and months_between(last, today) > config.scoring.stale_activity_months:
        add(72, f"No recorded investment since {last:%b %Y}")

    geography = match.criterion("geography")
    # Below 70 means no match at all (a global mandate scores 70 and does cover the company).
    if geography is not None and geography.known and geography.raw_score < 70:
        add(70, "Outside its core geography")

    if match.evidence_quality in {"unknown", "inferred"} and not inv.is_archetype:
        add(68, "Mandate unverified — confirm before outreach")

    if sub in CAPITAL_INTENSIVE_SUBSECTORS and (inv.investor_type in SMALL_CHECK_TYPES
                                                or (inv.fund_size is not None and inv.fund_size < 50e6)):
        add(60, "Capital intensity may exceed its follow-on capacity")

    if company.valuation.known and round_size and company.valuation_type.value == "pre_money":
        share = round_size / company.valuation.value
        if share > 0.35:
            add(57, f"Dilution: raise is {share:.0%} of pre-money valuation")
    elif needs_lead and inv.lead_capability in {"yes", "sometimes"} and not company.valuation.known:
        add(58, "Valuation expectations not stated in the deck")

    if needs_lead and inv.lead_capability == "sometimes":
        add(55, "No lead yet — may wait for one to set terms")

    sector = match.criterion("sector")
    if sector is not None and sector.known and 0 < sector.raw_score <= 65:
        add(54, "Adjacent rather than core sector fit")

    count = company.customer_count.value if company.customer_count.known else None
    if count is not None and count <= 3:
        add(50, f"Customer concentration ({company.customers.value})")

    product_stage = company.product_stage.value if company.product_stage.known else None
    if product_stage in {"concept", "prototype", "pilot"} and revenue_stage in {None, "pre_revenue", "early_revenue"}:
        add(48, f"Limited commercial validation ({PRODUCT_STAGES[product_stage].lower()})")

    if sub in LONG_SALES_CYCLE_SUBSECTORS:
        add(45, "Long hospital sales cycle" if family_of(sub) == "healthcare" else "Long enterprise sales cycle")

    competitors = company.competitors.value or []
    if len(competitors) >= 2:
        add(40, f"Competitive field ({', '.join(competitors[:2])})")

    seen: set[str] = set()
    ordered: list[str] = []
    for _severity, text in sorted(found, key=lambda pair: -pair[0]):
        if text not in seen:
            seen.add(text)
            ordered.append(text)
    return ordered[:limit]
