"""The optional external model: gating, grounding, failure fallback. Uses a fake client."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

try:  # anthropic 1.x is built on httpx2; 0.x on httpx
    import httpx2 as http_lib
except ImportError:  # pragma: no cover - depends on the installed SDK major version
    import httpx as http_lib

from app.config import Settings
from app.errors import LLMUnavailableError
from app.ingestion.deck_parser import parse_deck
from app.pipeline import analyze_deck
from app.services.llm_service import FALLBACK_BETA, LLMService, novel_tokens


class FakeMessages:
    def __init__(self, payload: Any = None, *, error: Exception | None = None, stop_reason: str = "end_turn"):
        self.payload, self.error, self.stop_reason, self.calls = payload, error, stop_reason, []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)])


def service(messages: FakeMessages) -> LLMService:
    settings = Settings(_env_file=None, llm_enabled=False)
    return LLMService(settings, client=SimpleNamespace(beta=SimpleNamespace(messages=messages)))


def test_disabled_by_default():
    llm = LLMService(Settings(_env_file=None, llm_enabled=False))
    assert not llm.configured
    with pytest.raises(LLMUnavailableError):
        llm.extract_fields(parse_deck_stub())
    assert service(FakeMessages({"fields": []})).configured


def parse_deck_stub():
    from helpers import make_doc
    return make_doc("Title", "We are raising a $6M Series A")


def test_extraction_keeps_grounded_values_and_discards_fabrications(samples):
    doc = parse_deck(samples["pdf"])
    messages = FakeMessages({"fields": [
        {"field": "raise_amount", "value": "$6M", "slide": 12, "quote": "We are raising a $6M Series A"},
        {"field": "revenue", "value": "$9M", "slide": 5, "quote": "Annual revenue reached $9M last year"},
        {"field": "lead_investor", "value": "Acme Ventures", "slide": 40, "quote": "Led by Acme Ventures"},
        {"field": "round_type", "value": "Series A", "slide": 12, "quote": "We are raising a $6M Series A"},
    ]})
    result = service(messages).extract_fields(doc)
    assert result.fields["raise_amount"].value == 6e6 and result.fields["raise_amount"].method == "llm"
    assert result.fields["company_stage"].value == "series_a"
    assert "revenue" not in result.fields and "lead_investor" not in result.fields
    assert any("discarded" in w for w in result.warnings)
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"]


def test_api_failure_falls_back_to_deterministic_extraction(samples, settings):
    error = anthropic.APIConnectionError(request=http_lib.Request("POST", "https://api.anthropic.com/v1/messages"))
    session = analyze_deck(settings=settings, deck_path=samples["pdf"],
                           llm=service(FakeMessages(error=error)))
    assert not session.llm_used
    assert any("External extraction skipped" in n for n in session.llm_notes)
    assert session.company.raise_amount.value == 6e6  # deterministic result intact


@pytest.mark.parametrize(("payload", "stop"), [("{not json", "end_turn"), ({"fields": []}, "refusal")])
def test_bad_or_refused_responses_raise(payload, stop):
    with pytest.raises(LLMUnavailableError):
        service(FakeMessages(payload, stop_reason=stop)).extract_fields(parse_deck_stub())


def test_polish_rejects_new_names_and_numbers(lumora_company, dataset, config):
    from helpers import TODAY

    from app.models.company import InvestorPreferences
    from app.pipeline import run_matching

    outcome = run_matching(lumora_company, InvestorPreferences(), dataset, config, today=TODAY)
    matches = outcome.run.matches[:2]
    good, bad = matches
    original_bad = bad.outreach_angle
    messages = FakeMessages({"items": [
        {"id": good.investor_id, "rationale": "Core diagnostics mandate at Series A.",
         "concern": "Customer concentration risk",
         "outreach_angle": "Lead with the FDA Breakthrough Device designation."},
        {"id": bad.investor_id, "rationale": "Backed Zentrix Bio in 2019.",
         "outreach_angle": "Mention their $40M fund."},
    ]})
    count = service(messages).polish_matches(matches, lumora_company)
    assert count == 1
    assert good.text_source == "llm_polished"
    assert good.likely_concerns[0] == "Customer concentration risk"
    assert bad.outreach_angle == original_bad and bad.text_source == "deterministic"


def test_novel_token_check():
    assert novel_tokens("Backed Zentrix in 2019", "Backed Cortexa in 2025") == ["2019", "Zentrix"]
    assert novel_tokens("Lead with Cortexa", "Cortexa Diagnostics") == []
    # Possessives of known names are not new names (a real rejection seen in live testing).
    assert novel_tokens("Lead with Acme's designation", "Acme Diagnostics designation") == []
    assert novel_tokens("Lead with Acme’s designation", "Acme Diagnostics designation") == []
    assert novel_tokens("Lead with Zentrix's designation", "Acme Diagnostics designation") == ["Zentrix's"]
