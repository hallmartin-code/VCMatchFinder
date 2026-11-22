"""A sample first-outreach email per match, framed for the investor's type.

Like the rest of the explanation text, every fact comes from the company record or the
investor record: the company's evidenced proof points, the match's fit reasons, deals
from the investor dataset, and the contact's first name when the investor list has one.
Anything the engine cannot know (a missing contact, the sender) is left as a bracketed
placeholder. The app never sends it - it is a draft
for a person to review, edit and send.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.intelligence.outreach import proof_points
from app.matching.scoring import describe_deal, relevant_deals
from app.matching.taxonomy import STRATEGIC_TYPES, family_label, stage_label, subsector_label
from app.models.company import CompanyProfile
from app.models.investor import InvestorContact, InvestorRecord
from app.models.match import InvestorMatch
from app.utils.money import format_money

_FUND_TYPES = frozenset({"seed_fund", "micro_vc", "venture_capital", "growth_equity", "private_equity",
                         "sovereign"})


@dataclass(frozen=True)
class SampleEmail:
    subject: str
    body: str

    def text(self) -> str:
        return f"Subject: {self.subject}\n\n{self.body}"


def _group(investor_type: str) -> str:
    if investor_type in STRATEGIC_TYPES:
        return "strategic"
    if investor_type in _FUND_TYPES:
        return "fund"
    if investor_type in {"angel", "syndicate", "angel_group", "family_office", "impact", "government_fund"}:
        return investor_type
    return "other"


def _sector(company: CompanyProfile) -> str:
    if company.subsector.known:
        return subsector_label(company.subsector.value).lower()
    if company.sector.known:
        return family_label(company.sector.value).lower()
    return ""


def _a(words: str) -> str:
    return f"{'an' if words[:1].lower() in set('aeiou') else 'a'} {words}"


def _poss(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _round(company: CompanyProfile) -> str:
    """ "$6M Series A round" - or as much of it as the record supports."""
    stage = stage_label(company.company_stage.value) if company.company_stage.known else ""
    amount = format_money(company.raise_amount.value) if company.raise_amount.known else ""
    return " ".join(w for w in (amount, stage, "round") if w)


def _intro(company: CompanyProfile) -> str:
    name = company.display_name
    where = " ".join((company.headquarters.value or "").split()) if company.headquarters.known else ""
    sector = _sector(company)
    kind = " ".join(w for w in (f"{where}-based" if where else "", sector, "company") if w)
    text = f"{name} is {_a(kind)} raising {_a(_round(company))}"
    if company.round_committed.known:
        text += f", with {format_money(company.round_committed.value)} already committed"
    return text + "."


def _fit_lines(match: InvestorMatch, inv: InvestorRecord | None, company: CompanyProfile, today: date) -> list[str]:
    lines = [p.strip().rstrip(".") for p in match.rationale.split(";") if p.strip()]
    lines = [line[0].upper() + line[1:] for line in lines][:3]
    if inv is not None and not inv.is_archetype:
        deals = relevant_deals(inv, company, today)
        if deals:
            names = " and ".join(describe_deal(d) for d, _w in deals[:2])
            lines.append(f"Close to your investment{'s' if len(deals[:2]) > 1 else ''} in {names}")
    return lines


def _can_lead(match: InvestorMatch) -> bool:
    lead = match.criterion("lead_capability")
    return lead is not None and lead.known and lead.raw_score >= 75


def sample_email(match: InvestorMatch, company: CompanyProfile, *, inv: InvestorRecord | None = None,
                 needs_lead: bool = False, today: date | None = None,
                 contact: InvestorContact | None = None) -> SampleEmail:
    """A first-outreach draft for this match, framed for its investor type."""
    today = today or date.today()
    group = _group(match.investor_type)
    name = company.display_name
    investor = "[Investor name]" if match.kind == "archetype" else match.investor_name
    sector = _sector(company) or "our"
    round_text = _round(company)
    lead_ask = needs_lead and _can_lead(match)

    openers = {
        "angel": f"I'm the founder of {name}, and I'd value you as an investor in our current round.",
        "syndicate": f"I'm the founder of {name}, and I think our round could suit your syndicate.",
        "angel_group": f"I'd like {name} to be considered for an upcoming {investor} screening.",
        "fund": f"I'm reaching out because we think {name} fits {_poss(investor)} focus.",
        "family_office": f"I'm reaching out about a direct investment opportunity in {name}.",
        "strategic": f"I'm reaching out because {name} could be both a strategic partner and an investment "
                     f"for {investor}.",
        "impact": f"I'm reaching out about {name}, which we believe can deliver measurable impact alongside "
                  "returns.",
        "government_fund": f"I'm reaching out about {_poss(name)} round and whether it fits "
                           f"{_poss(investor)} programs.",
        "other": f"I'm reaching out about {_poss(name)} current round.",
    }
    if lead_ask:
        ask = f"We're looking for a lead investor and would welcome a conversation about {investor} leading the round."
    else:
        ask = {
            "angel": "Would you be open to a 20-minute call next week?",
            "syndicate": "Would you consider bringing the round to your syndicate? A 20-minute call would be a "
                         "great start.",
            "angel_group": "Could we apply for a screening slot, or is there a member who should see the deck first?",
            "fund": "Would you have time for a 30-minute call with the partner who covers "
                    f"{sector if sector != 'our' else 'this sector'}?",
            "family_office": "Would you be open to a call to walk through the opportunity and terms?",
            "strategic": "Would your venture or business-development team be open to a call about both a "
                         "commercial partnership and participation in the round?",
            "impact": "Would you be open to a call to walk through the impact case and the round?",
            "government_fund": "Could you point us to the right program and contact for an application?",
            "other": "Would you be open to a short call?",
        }[group]
    if group == "angel_group" and needs_lead and not lead_ask:
        ask += " We are also looking for a lead investor to anchor the round."

    points = [p.text for p in proof_points(company)][:3]
    greeting = (contact.first_name if contact and contact.first_name else "[First name]")
    paragraphs = [f"Hi {greeting},", openers[group], _intro(company)]
    if points:
        paragraphs.append("Highlights: " + "; ".join(points) + ".")
    fit = _fit_lines(match, inv, company, today)
    if fit:
        paragraphs.append(f"Why we think this fits {investor}:\n" + "\n".join(f"- {line}" for line in fit))
    paragraphs.append(ask)
    materials = [m.lower() for m in match.required_materials[:4]]
    if materials:
        listed = ", ".join(materials[:-1]) + (" and " if len(materials) > 1 else "") + materials[-1]
        paragraphs.append(f"I'm happy to share our {listed}.")
    paragraphs.append(f"Best,\n[Your name]\n[Title], {name}")

    subjects = {
        "angel": f"Intro: {name}, raising {_a(round_text)}",
        "syndicate": f"{name}: {_a(round_text)} for your syndicate",
        "angel_group": f"Screening request: {name}",
        "strategic": f"{name} and {investor}: partnership and investment",
        "family_office": f"Direct investment: {name}",
    }
    subject = subjects.get(group, f"{name}: {round_text}")
    if sector != "our" and group in {"fund", "impact", "government_fund", "other"}:
        subject += f" ({sector})"
    return SampleEmail(subject=subject[0].upper() + subject[1:], body="\n\n".join(paragraphs))
