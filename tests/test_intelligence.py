"""Rationale, concerns, outreach angles, materials, next action and data gaps."""

from __future__ import annotations

import pytest
from helpers import TODAY

from app.extraction.company_extractor import extract_company
from app.ingestion.deck_parser import parse_deck
from app.pipeline import run_matching
from app.models.company import InvestorPreferences


@pytest.fixture
def outcome(lumora_company, dataset, config):
    return run_matching(lumora_company, InvestorPreferences(), dataset, config, today=TODAY)


def by_name(outcome, name):
    return next(m for m in outcome.run.matches if m.investor_name == name)


def test_top_match_explanations_are_specific(outcome):
    top = by_name(outcome, "Aldermoor Health Ventures")
    assert "diagnostics" in top.rationale.lower()
    assert "Breakthrough Device designation" in top.outreach_angle  # deck fact
    assert "Cortexa Diagnostics" in top.outreach_angle  # dataset fact
    assert "great investment opportunity" not in top.outreach_angle.lower()
    assert 1 <= len(top.likely_concerns) <= 3
    assert {"Regulatory roadmap", "Cap table", "Financial model"} <= set(top.required_materials)
    assert any(e.startswith("Deal: Cortexa Diagnostics") for e in top.investment_evidence)


def test_concerns_reflect_investor_constraints(outcome):
    assert by_name(outcome, "Everline Health System Ventures").likely_concerns[0] == \
        "Invests only behind a committed lead"
    assert any("Already an investor" in c for c in by_name(outcome, "Brightwater Seed Collective").likely_concerns)


def test_next_action_names_tier1(outcome):
    assert "Aldermoor Health Ventures" in outcome.next_action
    assert "separate approval" not in outcome.next_action


def test_data_gaps_flag_synthetic_data_and_inference(outcome):
    assert any("SYNTHETIC" in g for g in outcome.gaps)
    assert any("Inferred" in g for g in outcome.gaps)
    assert any("hard filters" in g for g in outcome.gaps)


def test_missing_round_data_drives_next_action(samples, dataset, config):
    company = extract_company(parse_deck(samples["sparse"])).company
    result = run_matching(company, InvestorPreferences(), dataset, config, today=TODAY)
    assert result.next_action.startswith("Confirm the")
    assert "amount being raised" in result.next_action.lower()


def test_archetype_angles_never_name_dataset_firms(outcome, dataset):
    names = {i.name for i in dataset.investors}
    for match in outcome.run.archetype_matches:
        assert not any(name in match.outreach_angle for name in names)
        assert match.likely_concerns[0].startswith("Profile only")
