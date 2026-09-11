"""Customers, pilots, LOIs, partnerships, regulatory status, IP, investors, competitors."""

from __future__ import annotations

import re
from typing import Any

from app.extraction.patterns import FORWARD_LOOKING, lines_matching
from app.ingestion.models import DeckDocument, DeckLine
from app.utils.provenance import Sourced
from app.utils.text import split_list, truncate

_CUSTOMER_NOUNS = (
    r"customers|clients|hospitals?|health\s+systems?|clinics?|pilot\s+sites?|sites|pilots?|"
    r"paying\s+users|users|accounts|schools|districts|facilities|labs?|laboratories|"
    r"enterprise\s+customers|logos|distributors"
)
_CUSTOMER_RE = re.compile(
    rf"\b(\d{{1,3}}(?:,\d{{3}})*|\d+)\s+(?:[A-Za-z-]+\s+){{0,2}}({_CUSTOMER_NOUNS})\b", re.I
)
_CUSTOMER_EXCLUDE = (
    f"{FORWARD_LOOKING}|potential|prospective|target\\s+customers|addressable|"
    r"\btam\b|\bsam\b|\bsom\b|\bmarket\b|\bpenetration\b|\bof\s+(?:the\s+)?(?:top|largest)\b"
)
_LOI_RE = re.compile(r"\b(\d{1,4})\s+(?:signed\s+|non-binding\s+)?(?:lois?|letters?\s+of\s+intent)\b", re.I)
_PARTNER_RE = (
    r"\bpartner(?:ship)?s?\s+(?:with|agreement)|distribution\s+agreement|collaboration\s+with|"
    r"strategic\s+partner|\bmou\b|joint\s+development|co-development|channel\s+partner"
)
_REGULATORY_RE = (
    r"\bfda\b|510\(?k\)?|de\s+novo|\bpma\b|ce[\s-]mark|breakthrough\s+device|\bide\b|\bind\b|"
    r"phase\s+(?:i{1,3}|[1-3])\b|\bclia\b|ukca|\bhipaa\b|soc\s?2|\bcap\s+accredit"
)
_BIO_EXCLUDE = r"\badvisor|\badviser|experience|years?\s+of|previously|formerly|former\b|background|\bled\s+\d+"
_IP_RE = r"\bpatents?\b|patent[\s-]pending|\bpct\b|trade\s+secrets?|exclusive\s+licen[cs]e|\bip\s+(?:portfolio|position|protection)"
_INVESTOR_CUE = re.compile(
    r"\b(?:existing|current|prior|seed)\s+investors?\b|\bbacked\s+by\b|\binvestors?\s+include\b|"
    r"\bour\s+investors\b|\bcap\s+table\s+includes\b", re.I,
)
_LEAD_CUE = re.compile(r"\b(?:led\s+by|lead\s+investor|round\s+lead)\s*(?:is|:|-|–)?\s*", re.I)
_LEAD_EXCLUDE = re.compile(
    r"team\s+led\s+by|led\s+by\s+(?:dr\.?|ceo|cto|our|a\s+team|founder|professor|prof\.)|"
    r"study\s+led|trial\s+led|research\s+led", re.I,
)
_SEEKING_LEAD = re.compile(
    r"seeking\s+(?:a\s+)?lead|lead\s+(?:investor\s+)?(?:wanted|sought|open|tbd|to\s+be\s+determined)|"
    r"looking\s+for\s+(?:a\s+)?lead|open\s+lead\s+position", re.I,
)
_LEAD_PLACEHOLDER = re.compile(r"^\s*(?:tbd|to\s+be\s+determined|open|none|n/?a|seeking|pending)", re.I)
_COMPETITOR_CUE = re.compile(
    r"\b(?:competitors?|competition|competitive\s+landscape|alternatives|other\s+players)\s*"
    r"(?:include|includes|are|:|-|–)\s*", re.I,
)
_GENERIC_COMPETITORS = {
    "incumbents", "status quo", "manual processes", "in-house", "in house", "blood culture",
    "legacy systems", "spreadsheets", "others", "other", "etc", "startups", "diy", "none",
    "traditional methods", "manual", "legacy vendors",
}
_PRODUCT_STAGE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("commercial", r"commercially\s+available|commercial\s+(?:launch(?:ed)?|sales|stage)|"
                   r"launched\s+(?:in|to)|generally\s+available|\bin\s+market\b|shipping\s+to|"
                   r"fda[\s-](?:cleared|approved)|ce[\s-]marked|on\s+the\s+market"),
    ("pilot", r"\bpilots?\b|\bbeta\b|early\s+access|research[\s-]use[\s-]only|\bruo\b|"
              r"limited\s+release|design\s+partners?"),
    ("prototype", r"\bprototype|\bmvp\b|proof[\s-]of[\s-]concept|\bpoc\b|\balpha\b|working\s+model"),
    ("concept", r"pre-?clinical|concept\s+stage|research\s+stage|early\s+development|"
                r"discovery\s+stage"),
)


def _source(doc: DeckDocument) -> str:
    return "user_supplied" if doc.source_format == "notes" else "pitch_deck"


def _sourced(doc: DeckDocument, value: Any, line: DeckLine, confidence: float,
             method: str = "pattern", note: str | None = None) -> Sourced[Any]:
    return Sourced(value=value, source=_source(doc), location=line.location,
                   confidence=confidence, evidence=line.text, method=method, note=note)


def extract_customers(doc: DeckDocument) -> dict[str, Sourced[Any]]:
    out: dict[str, Sourced[Any]] = {}
    candidates: list[tuple[int, str, DeckLine]] = []
    for line in doc.iter_lines():
        if re.search(_CUSTOMER_EXCLUDE, line.text, re.I):
            continue
        for match in _CUSTOMER_RE.finditer(line.text):
            count = int(match.group(1).replace(",", ""))
            if 1900 <= count <= 2100 or count == 0:
                continue
            candidates.append((count, match.group(0), line))
    if candidates:
        count, phrase, line = candidates[0]
        out["customers"] = _sourced(doc, truncate(phrase, 80), line, 0.82)
        out["customer_count"] = _sourced(doc, count, line, 0.78)

    lois = [(int(m.group(1)), m.group(0), ln) for ln in doc.iter_lines()
            for m in [_LOI_RE.search(ln.text)] if m and not re.search(FORWARD_LOOKING, ln.text, re.I)]
    if lois:
        out["lois"] = _sourced(doc, lois[0][1], lois[0][2], 0.85)

    partners = lines_matching(doc, _PARTNER_RE, exclude=f"{FORWARD_LOOKING}|seeking|looking\\s+for")
    if partners:
        out["partnerships"] = _sourced(doc, truncate(partners[0].text, 140), partners[0], 0.75)
    return out


def extract_regulatory(doc: DeckDocument) -> Sourced[Any]:
    lines = lines_matching(doc, _REGULATORY_RE, exclude=_BIO_EXCLUDE)
    if not lines:
        return Sourced(value=None)
    # Prefer statements of achieved status, then plans.
    achieved = [ln for ln in lines if re.search(
        r"granted|received|cleared|approved|designation|submitted|filed|accepted|completed", ln.text, re.I)]
    chosen = (achieved + [ln for ln in lines if ln not in achieved])[:2]
    value = "; ".join(truncate(ln.text, 110) for ln in chosen)
    return Sourced(value=value, source=_source(doc), location=chosen[0].location,
                   confidence=0.82, evidence=" | ".join(ln.text for ln in chosen), method="pattern")


def extract_ip(doc: DeckDocument) -> Sourced[Any]:
    lines = lines_matching(doc, _IP_RE, exclude=_BIO_EXCLUDE)
    if not lines:
        return Sourced(value=None)
    chosen = lines[:2]
    value = "; ".join(truncate(ln.text, 110) for ln in chosen)
    return Sourced(value=value, source=_source(doc), location=chosen[0].location,
                   confidence=0.82, evidence=" | ".join(ln.text for ln in chosen), method="pattern")


def _org_names(text: str) -> list[str]:
    names: list[str] = []
    for item in split_list(re.sub(r"\([^)]*\)", "", text)):
        item = item.strip(" .:;-–")
        if not item or len(item) > 60 or not item[0].isupper():
            continue
        if item.lower() in _GENERIC_COMPETITORS or re.search(r"[\$€£]\s?\d|\d+\s*%", item):
            continue
        names.append(item)
    return names


def extract_investors(doc: DeckDocument) -> dict[str, Sourced[Any]]:
    out: dict[str, Sourced[Any]] = {}
    for line in doc.iter_lines():
        match = _INVESTOR_CUE.search(line.text)
        if not match:
            continue
        tail = line.text[match.end():].strip(" :-–")
        names = _org_names(tail) or (_org_names(line.next_text) if not tail else [])
        if names:
            out["existing_investors"] = _sourced(doc, names[:8], line, 0.8)
            break

    for line in doc.iter_lines():
        if _SEEKING_LEAD.search(line.text):
            out["seeking_lead"] = _sourced(doc, True, line, 0.88)
            break
    for line in doc.iter_lines():
        match = _LEAD_CUE.search(line.text)
        if not match or _LEAD_EXCLUDE.search(line.text):
            continue
        tail = line.text[match.end():].strip(" :-–")
        if not tail or _LEAD_PLACEHOLDER.match(tail):
            out.setdefault("seeking_lead", _sourced(doc, True, line, 0.85))
            continue
        names = _org_names(tail)
        if names:
            out["lead_investor"] = _sourced(doc, names[0], line, 0.82)
            out["seeking_lead"] = _sourced(doc, False, line, 0.8)
            break
    return out


def extract_competitors(doc: DeckDocument) -> Sourced[Any]:
    for line in doc.iter_lines():
        match = _COMPETITOR_CUE.search(line.text)
        if not match:
            continue
        names = _org_names(line.text[match.end():] or line.next_text)
        if names:
            return _sourced(doc, names[:8], line, 0.78)
    # Competition table: first cell of each row on a competition-titled page.
    for page in doc.pages:
        if page.title and re.search(r"compet", page.title, re.I) and page.tables:
            names = [row.split(" | ")[0].strip() for row in page.tables[1:]]
            names = [n for n in names if n and n[0].isupper() and n.lower() not in _GENERIC_COMPETITORS
                     and len(n) <= 50]
            if names:
                return Sourced(value=names[:8], source=_source(doc),
                               location=doc.location(page.number, "table"), confidence=0.7,
                               evidence=", ".join(names[:8]), method="table")
    return Sourced(value=None)


def classify_product_stage(doc: DeckDocument) -> Sourced[Any]:
    """Most mature product stage explicitly evidenced (forward-looking lines excluded)."""
    for stage, pattern in _PRODUCT_STAGE_PATTERNS:
        lines = lines_matching(doc, pattern, exclude=f"{FORWARD_LOOKING}|{_BIO_EXCLUDE}|competit")
        if lines:
            line = lines[0]
            return Sourced(value=stage, source="inference", location=line.location,
                           confidence=0.65, evidence=line.text, method="keyword_classifier",
                           note="Product stage classified from deck language")
    return Sourced(value=None)


def extract_traction(doc: DeckDocument) -> dict[str, Sourced[Any]]:
    out = extract_customers(doc)
    out["regulatory_status"] = extract_regulatory(doc)
    out["intellectual_property"] = extract_ip(doc)
    out.update(extract_investors(doc))
    out["competitors"] = extract_competitors(doc)
    out["product_stage"] = classify_product_stage(doc)
    return out
