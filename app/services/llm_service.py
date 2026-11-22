"""Optional external model: Anthropic Claude for field extraction and wording polish.

Off by default. It runs only when all three hold: ``TEN_LLM_ENABLED=true``, an API key is
configured. Everything the
model returns is checked locally before it is used:

* Extracted fields must cite a slide whose extracted text contains the quoted evidence and
  the value's figures; unverifiable values are discarded. Scanned decks have no local text,
  so values read from slide images are kept but labeled unverified and low-confidence.
* Polished wording may not introduce any number or proper noun absent from its inputs;
  otherwise the deterministic draft is kept.

Scores are never produced here - scoring is deterministic. The engine works fully without
this module.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.errors import LLMUnavailableError
from app.extraction.patterns import money_mentions
from app.ingestion.models import DeckDocument
from app.intelligence.outreach import proof_points
from app.matching.taxonomy import parse_stage, stage_label
from app.models.company import CompanyProfile
from app.models.match import InvestorMatch
from app.reporting.template import load_template
from app.utils.logging import get_logger
from app.utils.money import parse_money, parse_percent
from app.utils.provenance import Sourced
from app.utils.text import truncate

LOGGER = get_logger(__name__)

PROVIDER = "Anthropic"
#: Server-side refusal fallback (array form, supported by the installed SDK).
FALLBACK_BETA = "server-side-fallback-2026-06-01"
FALLBACK_MODELS = [{"model": "claude-opus-4-8"}]
MAX_TOKENS = 16000
MAX_DECK_CHARS = 150_000
MAX_VISION_PAGES = 20
GROUNDING_THRESHOLD = 0.6

#: Fields the model may extract -> value kind.
EXTRACTION_FIELDS: dict[str, str] = {
    "name": "text", "website": "text", "headquarters": "text",
    "business_model_description": "text", "round_type": "text", "raise_amount": "money",
    "amount_raised": "money", "round_committed": "money", "valuation": "money",
    "valuation_type": "text", "revenue": "money", "revenue_growth": "percent", "customers": "text",
    "market_size": "money", "use_of_funds": "list", "regulatory_status": "text",
    "intellectual_property": "text", "existing_investors": "list", "lead_investor": "text",
    "competitors": "list", "partnerships": "text",
}

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": list(EXTRACTION_FIELDS)},
                    "value": {"type": "string"},
                    "slide": {"type": "integer"},
                    "quote": {"type": "string"},
                },
                "required": ["field", "value", "slide", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["fields"],
    "additionalProperties": False,
}

EXTRACTION_SYSTEM = """You extract facts from a startup pitch deck for an investor-matching engine used by a venture firm.

Report only what the deck itself states. For each field you find, return the value as written in the deck, the slide/page number it appears on, and a short verbatim quote (under 30 words) copied from that slide. Leave out any field the deck does not state; the engine treats missing information as unknown, which is always better than an estimate. Do not use outside knowledge.

Plans, targets and projections are not facts about the present: "revenue" means revenue already achieved, and a revenue target or forecast is not revenue. Similarly, an approval that is planned or sought is not a regulatory status that has been achieved; report it only as a plan.

Field meanings:
- name: company name. website: company website. headquarters: city/region/country of the company.
- business_model_description: how the company makes money, in the deck's words.
- round_type: the round being raised now (e.g. "Seed", "Series A").
- raise_amount: amount being raised in the current round. amount_raised: total capital raised before this round.
- round_committed: amount already committed to the current round.
- valuation: valuation figure; valuation_type: "pre-money", "post-money", or "cap".
- revenue: achieved revenue (annual, ARR, or stated period). revenue_growth: achieved growth rate as a percentage.
- customers: count and type of customers, pilots or sites. market_size: total addressable market.
- use_of_funds: planned uses of this round, separated by semicolons.
- regulatory_status: regulatory designations, clearances, submissions or plans.
- intellectual_property: patents, licenses or other IP.
- existing_investors, competitors: names separated by semicolons.
- lead_investor: named lead of the current round, only if one is named.
- partnerships: named partnerships or collaborations."""

#: Cell limits come from the report template, so Claude writes to the report's format.
_MATCH_COLUMNS = load_template().section("top_investor_matches")
WHY_LIMIT = _MATCH_COLUMNS.column("why").max_chars or 130
CONCERN_LIMIT = _MATCH_COLUMNS.column("concern").max_chars or 80
ANGLE_LIMIT = _MATCH_COLUMNS.column("angle").max_chars or 165
#: Targets sit below the one-page ladder's trimmed limits, so Claude's text is never cut off.
WHY_TARGET = _MATCH_COLUMNS.column("why").target_chars or 95
CONCERN_TARGET = _MATCH_COLUMNS.column("concern").target_chars or 55
ANGLE_TARGET = _MATCH_COLUMNS.column("angle").target_chars or 125

POLISH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "rationale": {"type": "string"},
                    "concern": {"type": "string"},
                    "outreach_angle": {"type": "string"},
                },
                "required": ["id", "rationale", "concern", "outreach_angle"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

POLISH_SYSTEM = f"""You edit investor-matching notes for a venture firm's partners. Each note fills one narrow cell of a one-page investor matching report. Text longer than the target is cut off mid-sentence when the page is tight, so aim for the target length and treat the maximum as a hard ceiling.

For each item, rewrite three drafts:
- "rationale" (the Why It Fits column): why this investor fits the company. Aim for about {WHY_TARGET} characters; never more than {WHY_LIMIT}.
- "concern" (the Concern column): the single most likely reason this investor would decline or delay, chosen from the concern drafts. Aim for about {CONCERN_TARGET} characters; never more than {CONCERN_LIMIT}.
- "outreach_angle" (the Outreach Angle column): the most specific way to position the company to this investor, usually one proof point tied to one thing the investor has done or offers. Aim for about {ANGLE_TARGET} characters; never more than {ANGLE_LIMIT}.

Use only facts that appear in the item's drafts and facts list: every name, number and date you write must already be in the input, because anything else would be a fabricated claim about a real investor or company. Write plainly and specifically, without promotional language. Return every id you were given."""


def _tidy(text: str) -> str:
    """Restore the report's em-dash style when the model writes a spaced hyphen."""
    return re.sub(r"\s+-\s+", " — ", " ".join(text.split()))

_TOKEN = re.compile(r"[a-z0-9]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CAPITALIZED = re.compile(r"\b[A-Z][A-Za-z0-9&'’\-]{2,}")
_POSSESSIVE = re.compile(r"['’]s?$")
#: Capitalized words that can open a rewritten sentence without adding a claim.
_COMMON_WORDS = frozenset("""lead pitch position open frame offer anchor source emphasize highlight
their they the this that with and for its use show start stress present connect tie note build
invests invest investor investors core strong fits fit backs brings offers mandate focus checks
round series seed deal deals relevant recorded adjacent comfortable leads following profile
confirm verify support approach position positioned""".split())


@dataclass
class LLMExtraction:
    fields: dict[str, Sourced[Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    used: bool = False


def overlap_ratio(quote: str, text: str) -> float:
    quote_tokens = set(_TOKEN.findall(quote.lower()))
    if not quote_tokens:
        return 0.0
    return len(quote_tokens & set(_TOKEN.findall(text.lower()))) / len(quote_tokens)


def _numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text.replace(",", "")))


def value_supported(raw: str, quote: str, kind: str) -> bool:
    """The value's figures must appear in the quote (money compared numerically)."""
    if kind == "money":
        amount = parse_money(raw)
        if amount is None:
            return False
        return any(abs(m - amount) <= 0.01 * amount for _p, m in money_mentions(quote)) or \
            bool(_numbers(raw) & _numbers(quote))
    numbers = _numbers(raw)
    return not numbers or bool(numbers & _numbers(quote))


def novel_tokens(output: str, source: str) -> list[str]:
    """Numbers and proper nouns in ``output`` that do not occur in ``source``."""
    source_numbers = _numbers(source)
    source_words = set(_TOKEN.findall(source.lower()))
    novel = [n for n in _NUMBER.findall(output.replace(",", "")) if n not in source_numbers]
    for word in _CAPITALIZED.findall(output):
        # "Acme's" is the known name "Acme" in possessive form, not a new name.
        parts = _TOKEN.findall(_POSSESSIVE.sub("", word).lower())
        if parts and all(part in source_words or part in _COMMON_WORDS for part in parts):
            continue
        novel.append(word)
    return novel


def _convert(field_name: str, kind: str, raw: str) -> Any | None:
    if kind == "money":
        return parse_money(raw)
    if kind == "percent":
        value = parse_percent(raw)
        if value is None:
            try:
                value = float(raw.strip().rstrip("%"))
            except ValueError:
                return None
        return value
    if kind == "list":
        items = [s.strip(" .") for s in re.split(r";|,", raw) if s.strip(" .")]
        return items[:8] or None
    if field_name == "valuation_type":
        lowered = raw.lower()
        if "pre" in lowered:
            return "pre_money"
        if "post" in lowered:
            return "post_money"
        if "cap" in lowered:
            return "cap"
        return "unspecified"
    return truncate(raw, 160) or None


class LLMService:
    """Thin, fail-safe wrapper around the Anthropic Messages API."""

    def __init__(self, settings: Settings, *, client: Any = None) -> None:
        self.settings = settings
        self.model = settings.llm_model
        self._client_obj = client
        #: Matches whose rewrite failed the fact check in the last polish call, with the terms.
        self.last_rejections: list[str] = []

    # ------------------------------------------------------------------- gating

    @property
    def configured(self) -> bool:
        return self._client_obj is not None or self.settings.llm_configured

    def disclosure(self) -> str:
        return (f"When enabled, deck text (and, for scanned decks, slide images) and the draft match "
                f"explanations are sent to {PROVIDER}'s API (model {self.model}) for field extraction "
                "and wording.")

    def _require(self) -> None:
        if not self.configured:
            raise LLMUnavailableError(
                "External model is not configured (set TEN_LLM_ENABLED=true and ANTHROPIC_API_KEY)."
            )

    def _client(self) -> Any:
        if self._client_obj is None:
            import anthropic

            key = self.settings.anthropic_api_key.get_secret_value() if self.settings.anthropic_api_key else None
            self._client_obj = anthropic.Anthropic(api_key=key, timeout=self.settings.llm_timeout_seconds,
                                                   max_retries=2)
        return self._client_obj

    # --------------------------------------------------------------------- call

    def _call(self, *, system: str, content: list[dict[str, Any]], schema: dict[str, Any],
              effort: str) -> dict[str, Any]:
        import anthropic

        try:
            response = self._client().beta.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                betas=[FALLBACK_BETA],
                fallbacks=FALLBACK_MODELS,
            )
        except anthropic.AuthenticationError as exc:
            raise LLMUnavailableError("The Anthropic API key was rejected; check ANTHROPIC_API_KEY.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMUnavailableError("The API key lacks permission for this model.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMUnavailableError("The external model is rate-limited; try again shortly.") from exc
        except anthropic.BadRequestError as exc:
            raise LLMUnavailableError(f"The external model rejected the request: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailableError(f"The external model returned an error ({exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailableError("Could not reach the external model (network error or timeout).") from exc

        if response.stop_reason == "refusal":
            raise LLMUnavailableError("The external model declined the request.")
        if response.stop_reason == "max_tokens":
            raise LLMUnavailableError("The external model's response was truncated.")
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), None)
        if not text:
            raise LLMUnavailableError("The external model returned no content.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMUnavailableError("The external model returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise LLMUnavailableError("The external model returned an unexpected structure.")
        return data

    # --------------------------------------------------------------- extraction

    def _deck_text(self, doc: DeckDocument) -> tuple[str, list[str]]:
        warnings: list[str] = []
        parts: list[str] = []
        used = 0
        for page in doc.pages:
            block = f"=== {doc.unit} {page.number} ===\n{doc.page_text(page.number).strip()}"
            if used + len(block) > MAX_DECK_CHARS:
                warnings.append(f"Deck text exceeded {MAX_DECK_CHARS:,} characters; only "
                                f"{doc.unit.lower()}s 1-{page.number - 1} were sent to the external model.")
                break
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts), warnings

    def extract_fields(self, doc: DeckDocument, *,
                       images: list[tuple[int, bytes]] | None = None) -> LLMExtraction:
        self._require()
        text, warnings = self._deck_text(doc)
        content: list[dict[str, Any]] = []
        for number, png in (images or [])[:MAX_VISION_PAGES]:
            content.append({"type": "text", "text": f"{doc.unit} {number}:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.standard_b64encode(png).decode("ascii")}})
        if text.strip():
            content.append({"type": "text", "text": f"<deck>\n{text}\n</deck>"})
        content.append({"type": "text", "text": "Extract the fields defined in your instructions from this deck."})
        data = self._call(system=EXTRACTION_SYSTEM, content=content, schema=EXTRACTION_SCHEMA, effort="high")
        fields, ground_warnings = self._ground(doc, data, vision=bool(images))
        LOGGER.info("External extraction returned %d verified field(s)", len(fields))
        return LLMExtraction(fields=fields, warnings=warnings + ground_warnings, used=True)

    def _ground(self, doc: DeckDocument, data: dict[str, Any], *, vision: bool) -> tuple[dict[str, Sourced[Any]], list[str]]:
        fields: dict[str, Sourced[Any]] = {}
        rejected = 0
        unverified = 0
        for item in data.get("fields", []) or []:
            if not isinstance(item, dict):
                continue
            name = item.get("field")
            kind = EXTRACTION_FIELDS.get(str(name))
            raw = str(item.get("value") or "").strip()
            quote = str(item.get("quote") or "").strip()
            try:
                slide = int(item.get("slide") or 0)
            except (TypeError, ValueError):
                slide = 0
            if kind is None or not raw:
                continue
            if not 1 <= slide <= doc.page_count:
                rejected += 1
                continue
            value = _convert(name, kind, raw)
            if value is None:
                continue
            page_text = doc.page_text(slide)
            location = doc.location(slide)
            if page_text.strip():
                if overlap_ratio(quote, page_text) < GROUNDING_THRESHOLD or not value_supported(raw, quote, kind):
                    rejected += 1
                    continue
                sourced: Sourced[Any] = Sourced(value=value, source="pitch_deck", location=location,
                                                confidence=0.8, evidence=quote, method="llm")
            elif vision:
                unverified += 1
                sourced = Sourced(value=value, source="pitch_deck", location=location, confidence=0.5,
                                  evidence=quote, method="llm_vision",
                                  note="Read from the slide image by the external model; not verifiable "
                                       "locally - confirm")
            else:
                rejected += 1
                continue
            if name not in fields or sourced.confidence > fields[name].confidence:
                fields[name] = sourced
                if name == "round_type":
                    stage = parse_stage(raw)
                    if stage:
                        fields["company_stage"] = sourced.model_copy(update={"value": stage})
                        fields["round_type"] = sourced.model_copy(update={"value": stage_label(stage)})
        warnings: list[str] = []
        if rejected:
            warnings.append(f"{rejected} model-extracted value(s) could not be verified against the cited "
                            "slide text and were discarded.")
        if unverified:
            warnings.append(f"{unverified} value(s) were read from slide images and cannot be verified "
                            "locally; they are marked low-confidence for review.")
        return fields, warnings

    # ------------------------------------------------------------------- polish

    def polish_matches(self, matches: list[InvestorMatch], company: CompanyProfile) -> int:
        """Rewrite rationale/angle wording; reject any output that adds a name or number."""
        self._require()
        self.last_rejections = []
        if not matches:
            return 0
        company_facts = [p.text for p in proof_points(company)]
        payload: list[dict[str, Any]] = []
        sources: dict[str, str] = {}
        for match in matches:
            facts = [c.evidence for c in match.breakdown if c.known] + match.investment_evidence + company_facts
            sources[match.investor_id] = " ".join(
                [match.rationale, match.outreach_angle, match.investor_name, company.display_name,
                 *match.likely_concerns, *facts])
            payload.append({"id": match.investor_id, "investor": match.investor_name,
                            "rationale_draft": match.rationale, "concern_drafts": match.likely_concerns,
                            "outreach_angle_draft": match.outreach_angle, "facts": facts})
        data = self._call(
            system=POLISH_SYSTEM,
            content=[{"type": "text", "text": json.dumps({"company": company.display_name, "items": payload},
                                                         indent=1)}],
            schema=POLISH_SCHEMA, effort="low",
        )
        by_id = {m.investor_id: m for m in matches}
        polished = 0
        for item in data.get("items", []) or []:
            match = by_id.get(str(item.get("id")))
            if match is None:
                continue
            rationale = str(item.get("rationale") or "").strip()
            angle = str(item.get("outreach_angle") or "").strip()
            if not rationale or not angle:
                continue
            source = sources[match.investor_id]
            novel = novel_tokens(rationale, source) + novel_tokens(angle, source)
            if novel:
                self.last_rejections.append(f"{match.investor_name} ({', '.join(dict.fromkeys(novel))})")
                continue
            match.rationale = truncate(_tidy(rationale), WHY_LIMIT)
            match.outreach_angle = truncate(_tidy(angle), ANGLE_LIMIT)
            concern = str(item.get("concern") or "").strip()
            if concern and not novel_tokens(concern, source):
                match.likely_concerns = [truncate(_tidy(concern), CONCERN_LIMIT), *match.likely_concerns[1:]]
            match.text_source = "llm_polished"
            polished += 1
        return polished
