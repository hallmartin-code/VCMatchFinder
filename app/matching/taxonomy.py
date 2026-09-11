"""Controlled vocabularies: stages, sectors, investor types, business models, capabilities.

Decks and investor datasets describe the same things in hundreds of ways. Everything is
mapped onto the canonical keys below before any comparison, so scoring compares like
with like and every match can be explained in the same terms.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# ---------------------------------------------------------------------------- stages

STAGES: tuple[str, ...] = ("pre_seed", "seed", "series_a", "series_b", "series_c", "growth")
STAGE_INDEX: dict[str, int] = {stage: i for i, stage in enumerate(STAGES)}
STAGE_LABELS: dict[str, str] = {
    "pre_seed": "Pre-Seed", "seed": "Seed", "series_a": "Series A",
    "series_b": "Series B", "series_c": "Series C+", "growth": "Growth",
}

_STAGE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"pre[\s-]?seed|friends\s*(?:and|&)\s*family|angel\s+round", "pre_seed"),
    (r"\bseed\b", "seed"),
    (r"series\s*a\b|\ba\s+round\b", "series_a"),
    (r"series\s*b\b|\bb\s+round\b", "series_b"),
    (r"series\s*[c-h]\b|late[\s-]stage", "series_c"),
    (r"\bgrowth\b|pre[\s-]?ipo|expansion\s+(?:round|stage)|buyout", "growth"),
)
_STAGE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"all\s+stages|stage[\s-]agnostic|any\s+stage", STAGES),
    (r"early[\s-]stage", ("pre_seed", "seed", "series_a")),
    (r"late[\s-]stage", ("series_c", "growth")),
    (r"venture[\s-]stage", ("seed", "series_a", "series_b", "series_c")),
)


def parse_stage(text: str | None) -> str | None:
    """The first canonical stage named in the text."""
    if not text:
        return None
    lowered = text.lower()
    earliest: tuple[int, str] | None = None
    for pattern, stage in _STAGE_PATTERNS:
        match = re.search(pattern, lowered)
        if match and (earliest is None or match.start() < earliest[0]):
            # "pre-seed" also contains "seed"; the pre-seed pattern starts earlier.
            earliest = (match.start(), stage)
    return earliest[1] if earliest else None


def parse_stages(value: str | Iterable[str] | None) -> list[str]:
    """All stages named, expanding ranges ("Seed–Series A") and groups ("early stage")."""
    if value is None:
        return []
    items = [value] if isinstance(value, str) else list(value)
    found: set[str] = set()
    for item in items:
        text = str(item).lower()
        if text.strip() in STAGE_INDEX:
            found.add(text.strip())
            continue
        for pattern, group in _STAGE_GROUPS:
            if re.search(pattern, text):
                found.update(group)
        # Ranges: "seed to series b", "Seed-Series A", "Series A–C"
        range_match = re.search(r"(.+?)\s*(?:–|—|-|\bto\b|\bthrough\b)\s*(.+)", text)
        if range_match:
            start = parse_stage(range_match.group(1))
            end_text = range_match.group(2)
            end = parse_stage(end_text)
            if end is None and re.fullmatch(r"\s*[c-h]\b.*", end_text) and start:
                end = "series_c"
            if start and end:
                lo, hi = sorted((STAGE_INDEX[start], STAGE_INDEX[end]))
                found.update(STAGES[lo : hi + 1])
                continue
        for pattern, stage in _STAGE_PATTERNS:
            if re.search(pattern, text):
                found.add(stage)
    return sorted(found, key=STAGE_INDEX.__getitem__)


def stage_label(stage: str | None) -> str:
    return STAGE_LABELS.get(stage or "", "Unknown")


def stage_distance(company_stage: str, investor_stages: Iterable[str]) -> int | None:
    """Rounds between the company's stage and the nearest stage the investor backs."""
    indices = [STAGE_INDEX[s] for s in investor_stages if s in STAGE_INDEX]
    if company_stage not in STAGE_INDEX or not indices:
        return None
    here = STAGE_INDEX[company_stage]
    return min(abs(here - i) for i in indices)


# --------------------------------------------------------------------------- sectors

SECTOR_FAMILIES: dict[str, str] = {
    "healthcare": "Healthcare & Life Sciences",
    "software": "Software & Internet",
    "climate": "Climate & Energy",
    "industrial": "Industrial & Hardware",
    "consumer": "Consumer",
}


@dataclass(frozen=True)
class Subsector:
    key: str
    label: str
    family: str
    #: (phrase, weight) used to classify deck text.
    keywords: tuple[tuple[str, float], ...]
    #: Phrases investors use for this subsector in mandates.
    aliases: tuple[str, ...]


SUBSECTORS: dict[str, Subsector] = {s.key: s for s in (
    Subsector("diagnostics", "Diagnostics", "healthcare",
              (("diagnostic", 3), ("assay", 3), ("biomarker", 2), ("point-of-care", 2),
               ("point of care", 2), ("ivd", 3), ("in vitro", 2), ("lab test", 2), ("clia", 2),
               ("detection", 1), ("screening", 1), ("test result", 1), ("sepsis", 1)),
              ("diagnostics", "diagnostic", "dx", "ivd", "molecular testing", "lab testing",
               "molecular diagnostics", "point of care diagnostics")),
    Subsector("medtech", "Medical devices", "healthcare",
              (("medical device", 3), ("implant", 2), ("surgical", 2), ("510(k)", 2),
               ("catheter", 2), ("de novo", 1), ("wearable", 1), ("device", 1)),
              ("medtech", "med tech", "medical devices", "medical device", "devices",
               "medical technology")),
    Subsector("digital_health", "Digital health", "healthcare",
              (("digital health", 3), ("telehealth", 3), ("telemedicine", 3),
               ("remote patient monitoring", 3), ("remote monitoring", 2),
               ("patient engagement", 2), ("ehr", 2), ("care coordination", 2), ("health app", 2)),
              ("digital health", "health it", "healthtech", "health tech", "telehealth",
               "healthcare it")),
    Subsector("therapeutics", "Therapeutics / biotech", "healthcare",
              (("therapeutic", 3), ("drug candidate", 3), ("small molecule", 3), ("biologic", 2),
               ("antibody", 2), ("phase 1", 2), ("phase 2", 2), ("preclinical", 2),
               ("oncology", 2), ("gene therapy", 3), ("mechanism of action", 2)),
              ("therapeutics", "biotech", "biotechnology", "drug development", "biopharma",
               "pharmaceuticals", "drug discovery")),
    Subsector("life_science_tools", "Life science tools", "healthcare",
              (("life science tools", 3), ("research tools", 2), ("sequencing", 2),
               ("bioprocess", 2), ("lab automation", 3), ("reagent", 2)),
              ("life science tools", "lab automation", "research tools", "laboratory automation")),
    Subsector("healthcare_services", "Healthcare services", "healthcare",
              (("care delivery", 3), ("home health", 3), ("provider network", 2), ("clinic", 1)),
              ("healthcare services", "care delivery", "health services")),
    Subsector("enterprise_software", "Enterprise software", "software",
              (("saas", 2), ("b2b software", 3), ("enterprise software", 3), ("workflow", 1),
               ("crm", 2), ("erp", 2), ("api", 1)),
              ("enterprise software", "saas", "b2b software", "enterprise", "b2b saas",
               "vertical saas")),
    Subsector("fintech", "Fintech", "software",
              (("fintech", 3), ("payments", 2), ("lending", 2), ("banking", 2), ("insurtech", 3),
               ("wealth management", 2)),
              ("fintech", "financial services", "payments", "insurtech", "financial technology")),
    Subsector("cybersecurity", "Cybersecurity", "software",
              (("cybersecurity", 3), ("threat detection", 3), ("zero trust", 3), ("encryption", 2)),
              ("cybersecurity", "security", "infosec", "cyber")),
    Subsector("ai_ml", "AI / machine learning", "software",
              (("machine learning", 1.5), ("artificial intelligence", 1.5), ("llm", 2),
               ("computer vision", 1.5), ("deep learning", 1.5)),
              ("ai", "ai/ml", "artificial intelligence", "machine learning", "applied ai")),
    Subsector("edtech", "Edtech", "software",
              (("edtech", 3), ("learning platform", 3), ("k-12", 3), ("students", 1)),
              ("edtech", "education", "education technology")),
    Subsector("proptech", "Proptech", "software",
              (("proptech", 3), ("real estate", 2), ("property management", 3)),
              ("proptech", "real estate", "construction tech")),
    Subsector("energy", "Energy", "climate",
              (("solar", 3), ("battery", 2), ("grid", 2), ("renewable", 3), ("hydrogen", 3),
               ("ev charging", 3), ("energy storage", 3)),
              ("energy", "clean energy", "renewables", "energy transition", "energy storage")),
    Subsector("climate_tech", "Climate tech", "climate",
              (("climate", 3), ("carbon", 2), ("decarboni", 3), ("emissions", 2),
               ("sustainability", 1)),
              ("climate", "climate tech", "cleantech", "sustainability", "decarbonization")),
    Subsector("agtech_food", "Agtech & food", "climate",
              (("agtech", 3), ("agriculture", 3), ("crop", 2), ("farm", 2), ("food tech", 3),
               ("alternative protein", 3)),
              ("agtech", "agriculture", "food tech", "foodtech", "agrifood")),
    Subsector("advanced_manufacturing", "Advanced manufacturing", "industrial",
              (("manufacturing", 2), ("3d printing", 3), ("additive manufacturing", 3),
               ("materials", 1), ("supply chain", 1)),
              ("manufacturing", "advanced manufacturing", "industrial tech", "materials")),
    Subsector("robotics_hardware", "Robotics & hardware", "industrial",
              (("robotics", 3), ("robot", 2), ("hardware", 2), ("iot", 2), ("drone", 3),
               ("sensor", 1)),
              ("robotics", "hardware", "iot", "deep tech", "deeptech")),
    Subsector("mobility_transport", "Mobility & logistics", "industrial",
              (("mobility", 3), ("logistics", 2), ("autonomous vehicle", 3), ("fleet", 2),
               ("transportation", 2)),
              ("mobility", "logistics", "transportation", "supply chain tech")),
    Subsector("aerospace_defense", "Aerospace & defense", "industrial",
              (("aerospace", 3), ("defense", 3), ("satellite", 3), ("space", 1)),
              ("aerospace", "defense", "space", "spacetech")),
    Subsector("consumer_products", "Consumer products", "consumer",
              (("cpg", 3), ("beauty", 2), ("apparel", 2), ("direct-to-consumer", 3), ("d2c", 2)),
              ("cpg", "consumer products", "consumer brands", "beauty")),
    Subsector("ecommerce_retail", "E-commerce & retail", "consumer",
              (("e-commerce", 3), ("ecommerce", 3), ("retail", 2), ("shopify", 2)),
              ("ecommerce", "e-commerce", "retail", "marketplaces")),
)}

#: Family-level mandate phrases ("healthcare" backs every healthcare subsector).
FAMILY_ALIASES: dict[str, tuple[str, ...]] = {
    "healthcare": ("healthcare", "health care", "life sciences", "life science", "health",
                   "medical", "hospital"),
    "software": ("software", "internet", "information technology"),
    "climate": ("climate & energy", "climate and energy"),
    "industrial": ("industrial", "industrials"),
    "consumer": ("consumer", "consumer internet"),
}
GENERALIST_ALIASES: tuple[str, ...] = (
    "generalist", "sector agnostic", "sector-agnostic", "all sectors", "any sector",
    "technology", "tech", "multi-sector",
)

#: Subsectors where regulatory approval gates revenue.
REGULATED_SUBSECTORS = frozenset({"diagnostics", "medtech", "therapeutics", "fintech",
                                  "aerospace_defense"})
#: Subsectors where reaching the next milestone typically needs deep follow-on capital.
CAPITAL_INTENSIVE_SUBSECTORS = frozenset({
    "therapeutics", "medtech", "diagnostics", "energy", "climate_tech", "robotics_hardware",
    "aerospace_defense", "advanced_manufacturing", "mobility_transport",
})
#: Subsectors whose buyers typically run long procurement cycles.
LONG_SALES_CYCLE_SUBSECTORS = frozenset({
    "diagnostics", "medtech", "digital_health", "healthcare_services", "enterprise_software",
    "cybersecurity", "aerospace_defense", "energy",
})


def subsector_label(key: str | None) -> str:
    return SUBSECTORS[key].label if key in SUBSECTORS else "Unknown"


def family_of(subsector: str | None) -> str | None:
    return SUBSECTORS[subsector].family if subsector in SUBSECTORS else None


def family_label(key: str | None) -> str:
    return SECTOR_FAMILIES.get(key or "", "Unknown")


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9&/()+.\- ]", " ", text.lower()).split())


def _contains_phrase(haystack: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", haystack) is not None


def parse_sector_tokens(value: str | Iterable[str] | None) -> set[str]:
    """Map mandate phrases to tokens: ``sub:<key>``, ``fam:<key>`` or ``generalist``."""
    if value is None:
        return set()
    items = [value] if isinstance(value, str) else list(value)
    tokens: set[str] = set()
    subsector_aliases = sorted(
        ((alias, key) for key, sub in SUBSECTORS.items() for alias in (key, *sub.aliases)),
        key=lambda pair: -len(pair[0]),
    )
    for raw in items:
        for part in re.split(r"[,;|\n]", str(raw)):
            item = _norm(part.replace("_", " "))
            if not item:
                continue
            if item.replace(" ", "_") in SUBSECTORS:
                tokens.add(f"sub:{item.replace(' ', '_')}")
                continue
            if item in SECTOR_FAMILIES:
                tokens.add(f"fam:{item}")
                continue
            matched = False
            remaining = item
            for alias, key in subsector_aliases:
                alias_n = _norm(alias.replace("_", " "))
                if _contains_phrase(remaining, alias_n):
                    tokens.add(f"sub:{key}")
                    remaining = remaining.replace(alias_n, " ")
                    matched = True
            for family, aliases in FAMILY_ALIASES.items():
                if any(_contains_phrase(remaining, a) for a in aliases):
                    tokens.add(f"fam:{family}")
                    matched = True
            if not matched and any(_contains_phrase(item, a) for a in GENERALIST_ALIASES):
                tokens.add("generalist")
    return tokens


def describe_sector_tokens(tokens: Iterable[str]) -> str:
    labels = []
    for token in sorted(tokens):
        if token == "generalist":
            labels.append("Generalist")
        elif token.startswith("sub:"):
            labels.append(subsector_label(token[4:]))
        elif token.startswith("fam:"):
            labels.append(family_label(token[4:]))
    return ", ".join(labels) if labels else "Not stated"


def classify_sector_text(text: str) -> dict[str, float]:
    """Keyword score per subsector for a block of text."""
    lowered = text.lower()
    scores: dict[str, float] = {}
    for key, sub in SUBSECTORS.items():
        total = 0.0
        for phrase, weight in sub.keywords:
            hits = len(re.findall(rf"(?<![a-z]){re.escape(phrase)}", lowered))
            total += min(hits, 6) * weight
        if total:
            scores[key] = total
    # "AI" as a standalone upper-case word is a weak signal; counted separately so that
    # "AI-powered diagnostics" still reads as diagnostics.
    ai_hits = len(re.findall(r"\bAI\b", text))
    if ai_hits:
        scores["ai_ml"] = scores.get("ai_ml", 0.0) + min(ai_hits, 6) * 0.75
    return scores


# ---------------------------------------------------------------------- investor types

INVESTOR_TYPES: dict[str, str] = {
    "angel": "Angel investor",
    "angel_group": "Angel group",
    "syndicate": "Syndicate",
    "seed_fund": "Seed fund",
    "micro_vc": "Micro-VC",
    "venture_capital": "Venture capital fund",
    "growth_equity": "Growth equity fund",
    "family_office": "Family office",
    "corporate_vc": "Corporate venture capital",
    "strategic": "Strategic investor",
    "healthcare_system": "Healthcare system",
    "pharma": "Pharmaceutical company",
    "medtech_strategic": "Medical-device strategic",
    "industrial_strategic": "Industrial strategic",
    "private_equity": "Private equity",
    "impact": "Impact investor",
    "government_fund": "Government-backed fund",
    "sovereign": "Sovereign wealth-related investor",
    "unknown": "Unknown type",
}
STRATEGIC_TYPES = frozenset({"corporate_vc", "strategic", "healthcare_system", "pharma",
                             "medtech_strategic", "industrial_strategic"})

#: Ordered most-specific first; the first alias found wins.
_TYPE_ALIASES: tuple[tuple[str, str], ...] = (
    ("angel group", "angel_group"), ("angel network", "angel_group"),
    ("angel collective", "angel_group"), ("angels", "angel_group"),
    ("angel", "angel"), ("syndicate", "syndicate"), ("spv", "syndicate"),
    ("micro vc", "micro_vc"), ("micro-vc", "micro_vc"), ("microvc", "micro_vc"),
    ("seed fund", "seed_fund"), ("pre-seed fund", "seed_fund"),
    ("growth equity", "growth_equity"), ("growth fund", "growth_equity"),
    ("family office", "family_office"),
    ("corporate venture", "corporate_vc"), ("cvc", "corporate_vc"), ("corporate", "corporate_vc"),
    ("health system", "healthcare_system"), ("healthcare system", "healthcare_system"),
    ("hospital", "healthcare_system"),
    ("pharmaceutical", "pharma"), ("pharma", "pharma"),
    ("medical device strategic", "medtech_strategic"), ("medical-device", "medtech_strategic"),
    ("medtech strategic", "medtech_strategic"),
    ("industrial strategic", "industrial_strategic"), ("industrial", "industrial_strategic"),
    ("strategic", "strategic"),
    ("private equity", "private_equity"), ("buyout", "private_equity"),
    ("impact", "impact"),
    ("government", "government_fund"), ("state fund", "government_fund"),
    ("public fund", "government_fund"), ("development bank", "government_fund"),
    ("sovereign", "sovereign"),
    ("venture capital", "venture_capital"), ("venture fund", "venture_capital"),
    ("venture", "venture_capital"), ("vc", "venture_capital"),
    ("seed", "seed_fund"),
)


def parse_investor_type(text: str | None) -> str:
    if not text:
        return "unknown"
    lowered = text.strip().lower()
    if lowered in INVESTOR_TYPES:
        return lowered
    underscored = lowered.replace(" ", "_").replace("-", "_")
    if underscored in INVESTOR_TYPES:
        return underscored
    for alias, key in _TYPE_ALIASES:
        if _contains_phrase(lowered, alias):
            return key
    return "unknown"


def investor_type_label(key: str | None) -> str:
    return INVESTOR_TYPES.get(key or "unknown", "Unknown type")


def parse_lead_capability(text: str | bool | None) -> str:
    if text is None:
        return "unknown"
    if isinstance(text, bool):
        return "yes" if text else "no"
    lowered = str(text).strip().lower()
    if not lowered:
        return "unknown"
    if re.search(r"sometimes|occasionally|co[\s-]?lead|selectively|case by case", lowered):
        return "sometimes"
    if re.search(r"^(?:no|n|false)\b|follows?\b|does not lead|never", lowered):
        return "no"
    if re.search(r"^(?:yes|y|true)\b|leads?\b|lead investor", lowered):
        return "yes"
    return "unknown"


# --------------------------------------------------------------------- business models

BUSINESS_MODELS: dict[str, str] = {
    "saas": "SaaS / subscription software",
    "subscription": "Subscription",
    "marketplace": "Marketplace",
    "hardware": "Hardware sales",
    "device_consumables": "Device + recurring consumables",
    "licensing": "Licensing / royalties",
    "services": "Services",
    "transactional": "Transaction fees",
    "d2c": "Direct-to-consumer",
    "therapeutic_asset": "Drug development / partnering",
    "reimbursed_testing": "Reimbursed testing",
}
_BUSINESS_MODEL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "saas": ("saas", "software as a service", "per seat", "per-seat", "annual license",
             "platform subscription"),
    "subscription": ("subscription", "recurring revenue", "monthly fee", "annual fee"),
    "marketplace": ("marketplace", "take rate", "two-sided"),
    "hardware": ("hardware sales", "unit sales", "device sales", "capital equipment"),
    "device_consumables": ("razor-and-blade", "razor and blade", "consumable", "cartridge",
                           "reagent rental", "per-test", "per test"),
    "licensing": ("licensing", "royalt", "license fee"),
    "services": ("services revenue", "consulting", "managed service", "professional services"),
    "transactional": ("transaction fee", "per transaction", "interchange"),
    "d2c": ("direct-to-consumer", "d2c", "dtc"),
    "therapeutic_asset": ("out-license", "partnering", "milestone payments", "upfront payment"),
    "reimbursed_testing": ("reimbursement", "cpt code", "medicare coverage", "payor coverage"),
}


def classify_business_model(text: str) -> list[str]:
    lowered = text.lower()
    return [key for key, words in _BUSINESS_MODEL_KEYWORDS.items()
            if any(w in lowered for w in words)]


def parse_business_models(value: str | Iterable[str] | None) -> set[str]:
    if value is None:
        return set()
    items = [value] if isinstance(value, str) else list(value)
    tags: set[str] = set()
    for item in items:
        text = str(item).lower().strip()
        key = text.replace(" ", "_").replace("-", "_")
        if key in BUSINESS_MODELS:
            tags.add(key)
            continue
        tags.update(classify_business_model(text))
        for key, label in BUSINESS_MODELS.items():
            if label.lower() in text:
                tags.add(key)
    return tags


def business_model_label(keys: Iterable[str]) -> str:
    labels = [BUSINESS_MODELS[k] for k in keys if k in BUSINESS_MODELS]
    return ", ".join(labels) if labels else "Unknown"


# ------------------------------------------------------------- strategic capabilities

STRATEGIC_TAGS: dict[str, str] = {
    "regulatory": "Regulatory expertise",
    "clinical_validation": "Clinical validation",
    "distribution": "Distribution & channels",
    "customer_access": "Customer access",
    "manufacturing": "Manufacturing scale-up",
    "commercialization": "Go-to-market",
    "international": "International expansion",
    "follow_on_capital": "Follow-on capital",
    "technical": "Technical / R&D",
    "recruiting": "Recruiting",
    "reimbursement": "Reimbursement strategy",
    "partnering": "Partnering / M&A",
}
_STRATEGIC_ALIASES: dict[str, tuple[str, ...]] = {
    "regulatory": ("regulatory", "fda", "510(k)", "ce mark", "approval pathway"),
    "clinical_validation": ("clinical", "validation study", "study sites", "clinical evidence",
                            "clinical trial"),
    "distribution": ("distribution", "channel", "distributor", "resell"),
    "customer_access": ("customer access", "hospital network", "health system network",
                        "customer introductions", "introductions to", "pilot sites",
                        "health systems", "buyer network"),
    "manufacturing": ("manufactur", "scale-up", "scale up", "production", "cmc"),
    "commercialization": ("commercial", "go-to-market", "go to market", "gtm", "sales team",
                          "sales and marketing", "marketing", "sales"),
    "international": ("international", "cross-border", "global expansion", "europe",
                      "expansion abroad", "ex-us"),
    "follow_on_capital": ("follow-on", "follow on", "reserves", "pro rata"),
    "technical": ("r&d", "engineering", "technical", "product development"),
    "recruiting": ("recruit", "hiring", "talent", "key hires", "team build"),
    "reimbursement": ("reimbursement", "payor", "payer", "cpt", "coverage"),
    "partnering": ("partnering", "m&a", "acquisition", "strategic partnership", "bd"),
}


def parse_strategic_tags(value: str | Iterable[str] | None) -> set[str]:
    if value is None:
        return set()
    items = [value] if isinstance(value, str) else list(value)
    tags: set[str] = set()
    for item in items:
        text = str(item).lower().strip()
        if text in STRATEGIC_TAGS:
            tags.add(text)
            continue
        for tag, aliases in _STRATEGIC_ALIASES.items():
            if any(alias in text for alias in aliases):
                tags.add(tag)
    return tags


def strategic_label(tags: Iterable[str]) -> str:
    labels = [STRATEGIC_TAGS[t] for t in tags if t in STRATEGIC_TAGS]
    return ", ".join(labels) if labels else "None identified"


# ------------------------------------------------------------ product/revenue stages

PRODUCT_STAGES: dict[str, str] = {
    "concept": "Concept / research",
    "prototype": "Prototype / MVP",
    "pilot": "Pilot / beta",
    "commercial": "Commercially available",
    "scaling": "Scaling",
}
REVENUE_STAGES: dict[str, str] = {
    "pre_revenue": "Pre-revenue",
    "early_revenue": "Early revenue (<$1M)",
    "revenue": "Revenue ($1M–$10M)",
    "scaling_revenue": "Scaling revenue ($10M+)",
}
REVENUE_STAGE_INDEX = {key: i for i, key in enumerate(REVENUE_STAGES)}


def revenue_stage_for(revenue: float | None) -> str | None:
    if revenue is None:
        return None
    if revenue <= 0:
        return "pre_revenue"
    if revenue < 1e6:
        return "early_revenue"
    if revenue < 1e7:
        return "revenue"
    return "scaling_revenue"
