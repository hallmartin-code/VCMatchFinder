"""Round, valuation, revenue, growth, market size and use of funds."""

from __future__ import annotations

import re
from typing import Any

from app.extraction.patterns import (
    FORWARD_LOOKING,
    RAISED_TO_DATE,
    Cue,
    Hit,
    best,
    lines_matching,
    money_parser,
    nearest_money,
    scan,
    to_sourced,
)
from app.ingestion.models import DeckDocument
from app.matching.taxonomy import parse_stage, stage_label
from app.utils.money import format_money, parse_percent
from app.utils.provenance import Sourced

_NEXT_ROUND = r"next\s+round|future\s+round|follow[\s-]on\s+round|series\s+[b-f]\s+in\s+20\d\d"

RAISE_CUES = (
    Cue(r"\b(?:currently\s+)?(?:raising|seeking|we\s+are\s+raising|capital\s+sought|"
        r"investment\s+sought|funding\s+sought|round\s+size|the\s+ask|our\s+ask)\b",
        0.92, "window", exclude=f"{RAISED_TO_DATE}|{_NEXT_ROUND}"),
    Cue(r"\bask\s*[:\-–]", 0.85, "window", exclude=RAISED_TO_DATE),
    Cue(r"\b(?:round|offering|raise)\s*(?:size|amount)?\s*[:\-–]", 0.85, "window",
        exclude=f"{RAISED_TO_DATE}|{_NEXT_ROUND}"),
    Cue(r"[\$€£]\s?[\d.,]+\s*(?:k|m|mm|million)?\s+(?:pre-?seed|seed|series\s+[a-f]|bridge)\b",
        0.88, "line", exclude=f"{RAISED_TO_DATE}|{_NEXT_ROUND}|closed|raised"),
)
COMMITTED_CUES = (
    Cue(r"\b(?:committed|soft[\s-]?circled|hard[\s-]?circled|subscribed|in\s+hand|secured)\b",
        0.86, "window",
        exclude=r"committed\s+to\s+(?!this\s+round)|commitment\s+to|committed\s+team|"
                r"secured\s+(?:patents?|ip|data|cloud|funding\s+from\s+nih)|\bpatents?\b"),
)
RAISED_TO_DATE_CUES = (
    Cue(r"\b(?:raised\s+to\s+date|capital\s+raised|previously\s+raised|total\s+(?:funding|raised|"
        r"capital\s+raised)|raised\s+since\s+inception|funding\s+to\s+date|we(?:'ve|\s+have)\s+raised)\b",
        0.9, "window"),
)
VALUATION_CUES: tuple[tuple[Cue, str], ...] = (
    (Cue(r"\bpre-?\s?money\b", 0.94, "window"), "pre_money"),
    (Cue(r"\bpost-?\s?money\b", 0.94, "window"), "post_money"),
    (Cue(r"\b(?:valuation|conversion|note|safe)\s+cap\b", 0.9, "window"), "cap"),
    (Cue(r"\bvaluation\b", 0.72, "line", exclude=r"market|tam|comparable|exit|acquisition"),
     "unspecified"),
)
REVENUE_CUES = (
    Cue(r"\b(?:annual\s+revenue|revenue\s+run[\s-]?rate|ttm\s+revenue|trailing\s+twelve|"
        r"fy\s?20\d\d\s+revenue|revenue\s+(?:in|for)\s+(?:fy\s?)?20\d\d)\b",
        0.9, "line", exclude=FORWARD_LOOKING),
    Cue(r"\brevenue\b", 0.78, "line",
        exclude=f"{FORWARD_LOOKING}|revenue\\s+(?:model|share|stream|potential|opportunity|"
                r"per|cycle)|market|\btam\b|\bsam\b|pre-?revenue|breakeven|pricing|"
                r"per\s+(?:test|unit|user|seat|month|patient)"),
    Cue(r"\barr\b|annual\s+recurring\s+revenue", 0.92, "line",
        exclude=f"{FORWARD_LOOKING}|arrival|arrange|array"),
)
MRR_CUE = (Cue(r"\bmrr\b|monthly\s+recurring\s+revenue", 0.9, "line", exclude=FORWARD_LOOKING),)
GROWTH_CUES = (
    Cue(r"(?:revenue|sales|arr|bookings)\s+(?:grew|growth|increased|up)\b", 0.88, "line",
        exclude=r"market|cagr|industry|\btam\b|segment"),
    Cue(r"\d+(?:\.\d+)?\s*%\s*(?:yoy|year[\s-]over[\s-]year|y/y|annual\s+growth)", 0.85, "line",
        exclude=r"market|cagr|industry|\btam\b|segment"),
)
TAM_CUES = (
    Cue(r"\btam\b|total\s+addressable\s+market", 0.92, "window"),
    Cue(r"\b(?:market\s+(?:size|opportunity)|addressable\s+market)\b", 0.78, "window",
        exclude=r"\bsam\b|\bsom\b|serviceable"),
)
STAGE_CUE = Cue(
    r"\b(?:pre-?seed|seed\s+(?:round|stage|financing|extension)|series\s+[a-f]\b|"
    r"bridge\s+round|growth\s+round)",
    0.85, "line",
    exclude=r"\btbd\b|to\s+be\s+determined|next\s+round|future\s+round|previous|prior|"
            r"last\s+round|closed\s+(?:our|a|the)|raised\s+(?:a|our|the)|led\s+(?:the|our)|"
            r"to\s+date|since\s+inception",
)
_ASK_WORDS = re.compile(r"raising|seeking|the\s+ask|our\s+ask|round\s+size|we\s+are", re.I)
_PERCENT_COMMITTED = re.compile(
    r"(\d{1,3})\s*%\s+(?:of\s+(?:the\s+)?round\s+)?(?:is\s+)?(?:committed|subscribed|closed|secured)",
    re.I,
)
_USE_OF_FUNDS = r"\buse\s+of\s+(?:funds|proceeds)\b|proceeds\s+will\s+be\s+used|allocation\s+of\s+(?:funds|capital)"
_UOF_STOP = re.compile(
    r"pre-?money|post-?money|valuation|committed|raising|existing\s+investors|lead\s+investor|"
    r"runway|contact|terms?\s*:", re.I,
)


def _stage_parser(text: str, anchor: int) -> str | None:
    return parse_stage(text)


def extract_round(doc: DeckDocument) -> dict[str, Sourced[Any]]:
    out: dict[str, Sourced[Any]] = {}

    raise_hit = best(scan(doc, RAISE_CUES, money_parser))
    out["raise_amount"] = to_sourced(raise_hit, doc)

    stage_hits = scan(doc, (STAGE_CUE,), _stage_parser)
    # A stage named on the same line as the ask is the round being raised.
    boosted = [Hit(h.value, h.location, h.evidence, 0.95 if _ASK_WORDS.search(h.evidence)
                   else h.confidence, h.page) for h in stage_hits]
    stage_hit = best(boosted)
    out["company_stage"] = to_sourced(stage_hit, doc)
    out["round_type"] = to_sourced(stage_hit, doc, value=stage_label(stage_hit.value)) \
        if stage_hit else Sourced(value=None)

    committed_hit = best(scan(doc, COMMITTED_CUES, money_parser))
    if committed_hit and raise_hit and committed_hit.value == raise_hit.value \
            and committed_hit.evidence == raise_hit.evidence:
        committed_hit = None  # the "nearest" amount was the round size itself
    committed = to_sourced(committed_hit, doc)
    if not committed.known and raise_hit:
        for line in doc.iter_lines():
            match = _PERCENT_COMMITTED.search(line.text)
            if match:
                share = float(match.group(1)) / 100.0
                committed = Sourced(
                    value=round(raise_hit.value * share, 2),
                    source="pitch_deck" if doc.source_format != "notes" else "user_supplied",
                    location=line.location, confidence=0.8, evidence=line.text,
                    method="derived",
                    note=f"{match.group(1)}% of the {format_money(raise_hit.value)} round",
                )
                break
    out["round_committed"] = committed

    out["amount_raised"] = to_sourced(best(scan(doc, RAISED_TO_DATE_CUES, money_parser)), doc)

    valuation_hits: list[tuple[Hit, str]] = []
    for cue, kind in VALUATION_CUES:
        for hit in scan(doc, (cue,), money_parser):
            valuation_hits.append((hit, kind))
    if valuation_hits:
        hit, kind = max(valuation_hits, key=lambda pair: (pair[0].confidence, -pair[0].page))
        out["valuation"] = to_sourced(hit, doc)
        out["valuation_type"] = to_sourced(hit, doc, value=kind)
    return out


def extract_revenue(doc: DeckDocument) -> dict[str, Sourced[Any]]:
    out: dict[str, Sourced[Any]] = {}
    hit = best(scan(doc, REVENUE_CUES, money_parser))
    if hit is None:
        mrr = best(scan(doc, MRR_CUE, money_parser))
        if mrr is not None:
            out["revenue"] = to_sourced(mrr, doc, value=mrr.value * 12, method="derived",
                                        note=f"Annualized from MRR of {format_money(mrr.value)}")
    else:
        out["revenue"] = to_sourced(hit, doc)

    pre_revenue = lines_matching(doc, r"\bpre-?revenue\b", exclude=r"\bpost\b|exited|no\s+longer")
    if pre_revenue and "revenue" not in out:
        line = pre_revenue[0]
        out["revenue_stage"] = Sourced(
            value="pre_revenue", source="user_supplied" if doc.source_format == "notes"
            else "pitch_deck", location=line.location, confidence=0.88, evidence=line.text,
            method="pattern",
        )

    def growth_parser(text: str, anchor: int) -> float | None:
        return parse_percent(text)

    out["revenue_growth"] = to_sourced(best(scan(doc, GROWTH_CUES, growth_parser)), doc)
    out["market_size"] = to_sourced(best(scan(doc, TAM_CUES, money_parser)), doc)
    return out


def extract_use_of_funds(doc: DeckDocument) -> Sourced[Any]:
    for page in doc.pages:
        lines = [ln.strip() for ln in "\n".join([page.text, *page.tables]).split("\n") if ln.strip()]
        for i, line in enumerate(lines):
            match = re.search(_USE_OF_FUNDS, line, re.I)
            if not match:
                continue
            items: list[str] = []
            inline = line[match.end():].strip(" :-–")
            if inline:
                items.extend(p.strip() for p in re.split(r";|\s\|\s", inline) if p.strip())
            for follow in lines[i + 1 : i + 10]:
                if _UOF_STOP.search(follow):
                    break
                if re.search(r"\d+\s*%|[\$€£]\s?\d", follow) or follow.startswith(("•", "-", "–")):
                    items.append(follow.lstrip("•-– ").strip())
                if len(items) >= 6:
                    break
            items = [" ".join(it.split())[:120] for it in items if len(it) > 3]
            if items:
                source = "user_supplied" if doc.source_format == "notes" else "pitch_deck"
                return Sourced(value=items, source=source,
                               location=doc.location(page.number), confidence=0.85,
                               evidence="; ".join(items), method="pattern")
    return Sourced(value=None)


def extract_financials(doc: DeckDocument) -> dict[str, Sourced[Any]]:
    out = extract_round(doc)
    out.update(extract_revenue(doc))
    out["use_of_funds"] = extract_use_of_funds(doc)
    return out


__all__ = ["extract_financials", "nearest_money"]
