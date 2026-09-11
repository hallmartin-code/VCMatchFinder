"""Build the normalized ``CompanyProfile`` from a parsed deck (and optional user notes).

Direct reads from the deck are labeled ``pitch_deck`` with their slide/page. Anything the
engine classifies or derives (sector from keywords, stage from round size, strategic
needs from use of funds) is labeled ``inference``. Values that cannot be found stay
``None``; the manual-entry list tells the user which gaps matter most.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.extraction.financial_extractor import extract_financials
from app.extraction.patterns import lines_matching
from app.extraction.traction_extractor import extract_traction
from app.ingestion.models import DeckDocument
from app.matching.taxonomy import (
    CAPITAL_INTENSIVE_SUBSECTORS,
    REGULATED_SUBSECTORS,
    SUBSECTORS,
    classify_business_model,
    classify_sector_text,
    family_of,
    parse_sector_tokens,
    parse_strategic_tags,
    revenue_stage_for,
)
from app.models.company import FIELD_SPECS, CompanyProfile
from app.utils.geography import US_STATES, resolve_location
from app.utils.money import format_money
from app.utils.provenance import Sourced, pick_best
from app.utils.text import truncate

_GENERIC_TITLES = re.compile(
    r"investor\s+(?:presentation|deck|update)|pitch\s+deck|confidential|company\s+overview|"
    r"agenda|welcome|series\s+[a-f]|seed\s+round|executive\s+summary|introduction|"
    r"^\s*(?:the\s+)?(?:problem|solution|team|market)\s*$|\b20\d\d\b|^page\s+\d",
    re.I,
)
_ENTITY_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&\-]*(?:\s+[A-Z][A-Za-z0-9&\-]*){0,4}),?\s+"
    r"(?:Inc|Corp|Corporation|Ltd|LLC|GmbH|PBC|Limited)\b\.?"
)
_NAME_CUE = re.compile(r"^(?:company(?:\s+name)?|legal\s+name|issuer)\s*[:\-–]\s*(.+)$", re.I)
_WEBSITE_RE = re.compile(
    r"(?<![@\w.])(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+(?:com|io|ai|co|net|org|health|bio|"
    r"tech|us|app|dev|example))\b(?!@)",
    re.I,
)
_HQ_CUE = re.compile(
    r"\b(?:headquarter(?:s|ed)(?:\s+in)?|based\s+in|hq\s*[:\-–]?|located\s+in|offices?\s+in)\s*[:\-–]?\s*",
    re.I,
)
_HQ_EXCLUDE = re.compile(
    r"(?:hospitals?|customers?|patients|pilots?|sites|partners?|clinics?|users)\s+(?:are\s+)?"
    r"(?:based|located)", re.I,
)
_CITY_STATE = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?),\s*([A-Z]{2})\b")
_SECTOR_CUE = re.compile(r"\b(?:sector|industry|vertical|category)\s*[:\-–]\s*(.+)", re.I)
_BUSINESS_MODEL_CUE = (
    r"\b(?:business\s+model|revenue\s+model|how\s+we\s+make\s+money|pricing\s+model|"
    r"monetization|go[\s-]to[\s-]market)\b"
)
_FILENAME_NOISE = re.compile(
    r"\b(?:deck|pitch|investor|presentation|series\s*[a-f]|seed|final|draft|v\d+(?:\.\d+)*|"
    r"copy|confidential|20\d\d|q[1-4]|\d+)\b",
    re.I,
)

#: Strategic-need tags inferred from use-of-funds language.
_UOF_NEEDS: tuple[tuple[str, str], ...] = (
    (r"clinical|validation\s+study|trial|study", "clinical_validation"),
    (r"fda|510\(?k\)?|regulatory|ce\s+mark|approval|submission", "regulatory"),
    (r"sales|commercial|marketing|go[\s-]to[\s-]market|gtm|business\s+development", "commercialization"),
    (r"manufactur|production|scale[\s-]?up|supply", "manufacturing"),
    (r"international|europe|expansion\s+(?:into|to)|global", "international"),
    (r"hir(?:e|ing)|team|talent|recruit", "recruiting"),
    (r"r&d|engineering|product\s+development|platform", "technical"),
    (r"reimbursement|payor|payer|coverage", "reimbursement"),
    (r"distribution|channel|partners", "distribution"),
)

#: Inference of stage from round size - labeled low confidence, shown for confirmation.
_STAGE_BY_RAISE: tuple[tuple[float, str], ...] = (
    (1.0e6, "pre_seed"), (4.0e6, "seed"), (15e6, "series_a"), (40e6, "series_b"),
    (100e6, "series_c"),
)


class ExtractionResult(BaseModel):
    company: CompanyProfile
    warnings: list[str] = Field(default_factory=list)
    manual_entry_fields: list[str] = Field(default_factory=list)
    deck_filename: str = ""
    deck_format: str = ""
    deck_pages: int = 0
    deck_scanned: bool = False


# ---------------------------------------------------------------------- company fields


def _source(doc: DeckDocument) -> str:
    return "user_supplied" if doc.source_format == "notes" else "pitch_deck"


def extract_name(doc: DeckDocument) -> Sourced[Any]:
    candidates: list[Sourced[Any]] = []
    for line in doc.iter_lines(include_notes=False):
        match = _NAME_CUE.match(line.text)
        if match:
            candidates.append(Sourced(value=truncate(match.group(1), 80), source=_source(doc),
                                      location=line.location, confidence=0.92,
                                      evidence=line.text, method="pattern"))
            break

    entities: Counter[str] = Counter()
    first_seen: dict[str, str] = {}
    for line in doc.iter_lines(include_notes=False):
        if re.search(r"investors?|backed\s+by|led\s+by|partner", line.text, re.I):
            continue
        for match in _ENTITY_RE.finditer(line.text):
            name = match.group(1).strip()
            weight = 2 if line.page in {1, doc.page_count} else 1
            entities[name] += weight
            first_seen.setdefault(name, line.location)
    if entities:
        name, _count = entities.most_common(1)[0]
        candidates.append(Sourced(value=name, source=_source(doc), location=first_seen[name],
                                  confidence=0.85, evidence=f"{name} (legal entity mention)",
                                  method="pattern"))

    if doc.pages and doc.source_format != "notes":
        first = doc.pages[0]
        for line in [ln.strip() for ln in first.text.split("\n") if ln.strip()][:4]:
            words = line.split()
            if 1 <= len(words) <= 6 and not _GENERIC_TITLES.search(line) and len(line) <= 60 \
                    and any(ch.isalpha() for ch in line):
                candidates.append(Sourced(value=line.strip(" .|"), source="pitch_deck",
                                          location=doc.location(1), confidence=0.72,
                                          evidence=line, method="title_slide"))
                break

    if doc.source_format != "notes":
        stem = _FILENAME_NOISE.sub(" ", Path(doc.filename).stem.replace("_", " ").replace("-", " "))
        stem = " ".join(stem.split())
        if stem and len(stem) >= 3:
            candidates.append(Sourced(value=stem.title() if stem.islower() else stem,
                                      source="inference", location="File name", confidence=0.35,
                                      evidence=doc.filename, method="filename",
                                      note="Guessed from the file name; confirm"))
    return pick_best(*candidates)


def extract_website(doc: DeckDocument, company_name: str | None) -> Sourced[Any]:
    name_tokens = {t.lower() for t in re.findall(r"[A-Za-z]{4,}", company_name or "")}
    best: tuple[int, str, str, str] | None = None
    for line in doc.iter_lines(include_notes=False):
        if "@" in line.text and not _WEBSITE_RE.search(line.text.split("@")[0]):
            pass
        for match in _WEBSITE_RE.finditer(line.text):
            domain = match.group(1).lower()
            if re.search(rf"@{re.escape(domain)}", line.text, re.I):
                continue
            score = 0
            if any(tok in domain for tok in name_tokens):
                score += 3
            if line.page in {1, doc.page_count}:
                score += 1
            if best is None or score > best[0]:
                best = (score, domain, line.location, line.text)
    if best is None or best[0] < 1:
        return Sourced(value=None)
    return Sourced(value=best[1], source=_source(doc), location=best[2],
                   confidence=0.85 if best[0] >= 3 else 0.6, evidence=best[3], method="pattern")


def extract_headquarters(doc: DeckDocument) -> Sourced[Any]:
    for line in doc.iter_lines():
        match = _HQ_CUE.search(line.text)
        if not match or _HQ_EXCLUDE.search(line.text):
            continue
        tail = re.split(r"[.;|·•]|\s{2,}|\s[-–]\s", line.text[match.end():])[0].strip()
        tail = truncate(tail, 60)
        if not tail:
            continue
        resolved = resolve_location(tail)
        return Sourced(value=tail, source=_source(doc), location=line.location,
                       confidence=0.88 if resolved else 0.55, evidence=line.text,
                       method="pattern", note=None if resolved else "Location not recognized")
    for page_number in (1, doc.page_count):
        page_lines = [ln for ln in doc.iter_lines(include_notes=False) if ln.page == page_number]
        for line in page_lines:
            match = _CITY_STATE.search(line.text)
            if match and match.group(2) in set(US_STATES.values()):
                value = f"{match.group(1)}, {match.group(2)}"
                return Sourced(value=value, source=_source(doc), location=line.location,
                               confidence=0.62, evidence=line.text, method="pattern",
                               note="City/state found on title or contact slide")
    return Sourced(value=None)


def classify_sector(doc: DeckDocument) -> tuple[Sourced[Any], Sourced[Any]]:
    """(subsector, sector family) - explicit statement first, keyword classifier second."""
    for line in doc.iter_lines(include_notes=False):
        match = _SECTOR_CUE.search(line.text)
        if not match:
            continue
        subs = [t[4:] for t in parse_sector_tokens(match.group(1)) if t.startswith("sub:")]
        if subs:
            sub = subs[0]
            return (
                Sourced(value=sub, source=_source(doc), location=line.location, confidence=0.88,
                        evidence=line.text, method="pattern"),
                Sourced(value=family_of(sub), source=_source(doc), location=line.location,
                        confidence=0.88, evidence=line.text, method="taxonomy"),
            )

    totals: Counter[str] = Counter()
    by_page: dict[str, Counter[int]] = {}
    for page in doc.pages:
        text = "\n".join([page.title or "", page.text, *page.tables, page.notes or ""])
        for key, score in classify_sector_text(text).items():
            totals[key] += score
            by_page.setdefault(key, Counter())[page.number] += score
    if not totals:
        return Sourced(value=None), Sourced(value=None)
    ranked = totals.most_common()
    top_key, top_score = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_score < 4:
        return Sourced(value=None, note="Too little sector language to classify"), Sourced(value=None)
    confidence = 0.75 if top_score >= 1.4 * max(runner, 1) else 0.55
    best_page = by_page[top_key].most_common(1)[0][0]
    evidence = ", ".join(f"{SUBSECTORS[k].label} {v:g}" for k, v in ranked[:3])
    sub = Sourced(value=top_key, source="inference", location=doc.location(best_page),
                  confidence=confidence, evidence=f"Keyword scores: {evidence}",
                  method="keyword_classifier",
                  note="Classified from deck language; confirm in review")
    family = Sourced(value=family_of(top_key), source="inference", location=doc.location(best_page),
                     confidence=confidence, evidence=sub.evidence, method="taxonomy")
    return sub, family


def extract_business_model(doc: DeckDocument) -> tuple[Sourced[Any], Sourced[Any]]:
    """(tags, description)."""
    lines = lines_matching(doc, _BUSINESS_MODEL_CUE)
    for line in lines:
        description = re.split(_BUSINESS_MODEL_CUE, line.text, maxsplit=1, flags=re.I)[-1]
        description = description.strip(" :-–") or line.next_text
        page_text = doc.page_text(line.page)
        tags = classify_business_model(f"{description}\n{page_text}")
        desc = Sourced(value=truncate(description, 140), source=_source(doc),
                       location=line.location, confidence=0.85, evidence=line.text,
                       method="pattern") if description else Sourced(value=None)
        if tags:
            return (Sourced(value=sorted(tags), source="inference", location=line.location,
                            confidence=0.78, evidence=truncate(description or line.text, 200),
                            method="keyword_classifier",
                            note="Model classified from the stated business model"), desc)
        if desc.known:
            return Sourced(value=None), desc
    tags = classify_business_model(doc.full_text())
    if tags:
        return (Sourced(value=sorted(tags), source="inference", confidence=0.5,
                        evidence="Business-model language across the deck",
                        method="keyword_classifier",
                        note="No explicit business-model statement found"), Sourced(value=None))
    return Sourced(value=None), Sourced(value=None)


# ------------------------------------------------------------------------ derivations


def derive_stage(company: CompanyProfile) -> Sourced[Any]:
    if company.company_stage.known or not company.raise_amount.known:
        return company.company_stage
    amount = company.raise_amount.value
    stage = next((s for ceiling, s in _STAGE_BY_RAISE if amount <= ceiling), "growth")
    return Sourced(value=stage, source="inference", location=company.raise_amount.location,
                   confidence=0.4, evidence=f"Raise of {format_money(amount)}",
                   method="derived", note="Stage inferred from round size only; confirm")


def derive_revenue_stage(company: CompanyProfile) -> Sourced[Any]:
    if company.revenue_stage.known and company.revenue_stage.source != "inference":
        return company.revenue_stage
    if company.revenue.known:
        return Sourced(value=revenue_stage_for(company.revenue.value), source="inference",
                       location=company.revenue.location, confidence=0.85,
                       evidence=f"Revenue of {format_money(company.revenue.value)}",
                       method="derived")
    return company.revenue_stage


def derive_strategic_needs(company: CompanyProfile) -> Sourced[Any]:
    if company.strategic_needs.known and company.strategic_needs.source == "user_supplied":
        return company.strategic_needs
    needs: dict[str, str] = {}
    for item in company.use_of_funds.value or []:
        for pattern, tag in _UOF_NEEDS:
            if re.search(pattern, item, re.I):
                needs.setdefault(tag, f"use of funds: {truncate(item, 60)}")
    sub = company.subsector.value
    reg = (company.regulatory_status.value or "").lower()
    if sub in REGULATED_SUBSECTORS and not re.search(r"cleared|approved|ce[\s-]marked", reg):
        needs.setdefault("regulatory", "regulated product without clearance in the deck")
    if sub in CAPITAL_INTENSIVE_SUBSECTORS:
        needs.setdefault("follow_on_capital", "capital-intensive subsector")
    if company.product_stage.value in {"pilot", "commercial"}:
        needs.setdefault("commercialization", f"product stage: {company.product_stage.value}")
    if company.customers.known and re.search(r"hospital|health\s+system|clinic", company.customers.value or "", re.I):
        needs.setdefault("customer_access", "institutional healthcare buyers")
    if not needs:
        return Sourced(value=None)
    evidence = "; ".join(f"{k}: {v}" for k, v in needs.items())
    # Keep derivation order: needs named in the use of funds come first.
    return Sourced(value=list(needs), source="inference", confidence=0.6,
                   location=company.use_of_funds.location, evidence=evidence,
                   method="derived", note="Inferred from use of funds, sector and stage")


def compose_traction(company: CompanyProfile, extras: dict[str, Sourced[Any]]) -> Sourced[Any]:
    parts: list[tuple[str, Sourced[Any]]] = []
    if company.customers.known:
        parts.append((company.customers.value, company.customers))
    if company.revenue.known:
        parts.append((f"{format_money(company.revenue.value)} revenue", company.revenue))
    if company.revenue_growth.known:
        parts.append((f"{company.revenue_growth.value:g}% growth", company.revenue_growth))
    lois = extras.get("lois")
    if lois is not None and lois.known:
        parts.append((str(lois.value), lois))
    if not parts:
        return Sourced(value=None)
    locations = sorted({p.location for _t, p in parts if p.location})
    sources = {p.source for _t, p in parts}
    source = "user_supplied" if sources == {"user_supplied"} else "pitch_deck"
    return Sourced(value=" · ".join(t for t, _p in parts), source=source,
                   location="; ".join(locations)[:120] or None,
                   confidence=min(p.confidence for _t, p in parts), method="composite",
                   note="Composed from extracted traction facts")


# ------------------------------------------------------------------------- orchestration


def _extract_raw(doc: DeckDocument) -> dict[str, Sourced[Any]]:
    fields: dict[str, Sourced[Any]] = {}
    name = extract_name(doc)
    fields["name"] = name
    fields["website"] = extract_website(doc, name.value if name.known else None)
    fields["headquarters"] = extract_headquarters(doc)
    fields["subsector"], fields["sector"] = classify_sector(doc)
    fields["business_model"], fields["business_model_description"] = extract_business_model(doc)
    fields.update(extract_financials(doc))
    fields.update(extract_traction(doc))
    return fields


def merge_fields(*field_sets: dict[str, Sourced[Any]]) -> dict[str, Sourced[Any]]:
    """Per field, keep the strongest candidate across sources (user notes beat the deck)."""
    merged: dict[str, Sourced[Any]] = {}
    keys = {k for fs in field_sets for k in fs}
    for key in keys:
        merged[key] = pick_best(*(fs.get(key) for fs in field_sets))
    return merged


def build_company(fields: dict[str, Sourced[Any]]) -> CompanyProfile:
    company = CompanyProfile()
    for key, value in fields.items():
        if key in FIELD_SPECS:
            setattr(company, key, value)
    company.company_stage = derive_stage(company)
    if company.company_stage.known and not company.round_type.known \
            and company.company_stage.source != "inference":
        company.round_type = company.company_stage
    company.revenue_stage = derive_revenue_stage(company)
    company.strategic_needs = derive_strategic_needs(company)
    company.traction = compose_traction(company, fields)
    if company.lead_investor.known:
        company.seeking_lead = Sourced(value=False, source=company.lead_investor.source,
                                       location=company.lead_investor.location, confidence=0.8,
                                       method="derived")
    return company


def manual_entry_fields(company: CompanyProfile) -> list[str]:
    """Missing fields that materially move the ranking - the user should fill these."""
    return [name for name in company.missing_fields(min_impact=2)
            if FIELD_SPECS[name].reviewable]


def extract_company(doc: DeckDocument, notes: DeckDocument | None = None, *,
                    extra_fields: list[dict[str, Sourced[Any]]] | None = None) -> ExtractionResult:
    """Deck (+ optional user notes, + optional verified model fields) -> company record."""
    field_sets = [_extract_raw(doc)]
    if notes is not None:
        field_sets.append(_extract_raw(notes))
    field_sets.extend(extra_fields or [])
    company = build_company(merge_fields(*field_sets))

    warnings = list(doc.warnings)
    manual = manual_entry_fields(company)
    if doc.is_scanned:
        warnings.append(
            "Deck text could not be extracted, so these fields need manual entry: "
            + ", ".join(FIELD_SPECS[f].label for f in manual) + "."
        )
    elif manual:
        warnings.append(
            "Not found in the deck (enter manually for an accurate match): "
            + ", ".join(FIELD_SPECS[f].label for f in manual) + "."
        )
    return ExtractionResult(
        company=company, warnings=warnings, manual_entry_fields=manual,
        deck_filename=doc.filename, deck_format=doc.source_format,
        deck_pages=doc.page_count, deck_scanned=doc.is_scanned,
    )


def parse_needs(value: Any) -> list[str]:
    return sorted(parse_strategic_tags(value))
