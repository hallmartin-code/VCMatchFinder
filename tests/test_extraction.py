"""Company normalization, provenance, and missing-data handling."""

from __future__ import annotations

from helpers import make_doc

from app.extraction.company_extractor import extract_company
from app.ingestion.deck_parser import notes_document, parse_deck
from app.utils.money import format_money, parse_money, parse_money_range, parse_percent


def test_lumora_core_fields_with_provenance(lumora_company):
    c = lumora_company
    assert c.name.value == "Lumora Diagnostics"
    assert c.raise_amount.value == 6_000_000 and c.raise_amount.location == "Page 12"
    assert c.raise_amount.source == "pitch_deck"
    assert c.company_stage.value == "series_a" and c.company_stage.source == "pitch_deck"
    assert c.round_committed.value == 1_500_000
    assert c.amount_raised.value == 3_200_000
    assert c.valuation.value == 18_000_000 and c.valuation_type.value == "pre_money"
    assert c.revenue.value == 420_000 and c.revenue.location == "Page 5"
    assert c.revenue_growth.value == 140
    assert c.market_size.value == 4_200_000_000
    assert c.headquarters.value == "Austin, Texas"
    assert c.location() is not None and "US-TX" in c.location().tokens
    assert c.customers.value == "3 hospital pilot sites"
    assert c.competitors.value == ["Sepsiq Labs", "Tessary Dx"]
    assert c.existing_investors.value == ["Brightwater Seed Collective", "Pellucid Angels"]
    assert c.seeking_lead.value is True
    assert len(c.use_of_funds.value) == 3
    assert "Breakthrough Device designation granted" in c.regulatory_status.value


def test_classifications_are_labeled_as_inference(lumora_company):
    assert lumora_company.subsector.value == "diagnostics"
    assert lumora_company.subsector.source == "inference"
    assert lumora_company.business_model.value == ["device_consumables"]
    assert lumora_company.business_model.source == "inference"
    assert lumora_company.strategic_needs.source == "inference"


def test_forward_looking_target_is_not_read_as_revenue():
    doc = make_doc("Financial Plan\nRevenue target: $12M ARR by 2029\nProjected revenue of $30M in 2030")
    company = extract_company(doc).company
    assert company.revenue.value is None
    assert company.revenue.source == "missing"


def test_missing_values_stay_null_and_are_listed_for_manual_entry(samples):
    result = extract_company(parse_deck(samples["sparse"]))
    company = result.company
    assert company.raise_amount.value is None and company.raise_amount.source == "missing"
    assert company.company_stage.value is None
    assert "raise_amount" in result.manual_entry_fields
    assert "company_stage" in result.manual_entry_fields
    assert any("enter manually" in w for w in result.warnings)


def test_seeking_lead_explains_missing_lead(lumora_company):
    assert "lead_investor" not in lumora_company.missing_fields()


def test_stage_inferred_from_round_size_is_labeled_low_confidence():
    company = extract_company(make_doc("Acme Robotics\nWe are raising $3M to scale production.")).company
    assert company.company_stage.value == "seed"
    assert company.company_stage.source == "inference"
    assert company.company_stage.confidence <= 0.5


def test_user_notes_override_the_deck(samples):
    doc = parse_deck(samples["pdf"])
    result = extract_company(doc, notes_document("We are now raising $8M for the Series A."))
    assert result.company.raise_amount.value == 8_000_000
    assert result.company.raise_amount.source == "user_supplied"


def test_pptx_extraction_matches_pdf(samples, lumora_company):
    pptx = extract_company(parse_deck(samples["pptx"])).company
    for field in ("name", "raise_amount", "company_stage", "revenue", "valuation", "headquarters"):
        assert pptx.field(field).value == lumora_company.field(field).value, field
    assert pptx.raise_amount.location.startswith("Slide")


def test_user_corrections_are_marked_and_reconciled(lumora_company):
    edited = lumora_company.apply_overrides({
        "headquarters": "Boston, MA", "raise_amount": "$8M", "company_stage": "Series B",
        "subsector": "Medical devices", "website": "",
    })
    assert edited.raise_amount.value == 8_000_000 and edited.raise_amount.source == "user_supplied"
    assert edited.company_stage.value == "series_b"
    assert edited.subsector.value == "medtech" and edited.sector.value == "healthcare"
    assert edited.website.value == lumora_company.website.value  # blank means unchanged
    assert lumora_company.raise_amount.value == 6_000_000  # original untouched


def test_money_parsing_rules():
    assert parse_money("$4.2B", strict=True) == 4.2e9
    assert parse_money("USD 750k", strict=True) == 750_000
    assert parse_money("Series 3 in 2026", strict=True) is None
    assert parse_money("2026") is None  # a year is never money
    assert parse_money("4,000,000") == 4_000_000
    assert parse_money_range("$1-3M") == (1e6, 3e6)
    assert parse_money_range("$500K-$1.5M") == (500e3, 1.5e6)
    assert parse_percent("grew 140% YoY") == 140
    assert format_money(1_500_000) == "$1.5M" and format_money(None) == "Not disclosed"
