"""Sample outreach emails: framed per investor type, built only from recorded facts."""

from __future__ import annotations

import pytest
from helpers import TODAY

from app.intelligence.sample_email import sample_email
from app.models.company import InvestorPreferences
from app.pipeline import run_matching


@pytest.fixture
def outcome(lumora_company, dataset, config):
    return run_matching(lumora_company, InvestorPreferences(), dataset, config, today=TODAY)


def email_for(outcome, name, **update):
    match = next(m for m in outcome.run.matches if m.investor_name == name)
    match = match.model_copy(update=update) if update else match
    return sample_email(match, outcome.company, inv=outcome.records[match.investor_id],
                        needs_lead=outcome.company.needs_lead(None), today=TODAY)


def test_vc_email_shows_fit_and_offers_the_lead(outcome):
    email = email_for(outcome, "Aldermoor Health Ventures")
    assert email.subject.startswith("Lumora Diagnostics: $6M Series A round")
    assert "Aldermoor Health Ventures leading the round" in email.body
    assert "Cortexa Diagnostics" in email.body  # a deal from the investor dataset
    assert "FDA Breakthrough Device designation" in email.body  # a deck proof point
    assert "Aldermoor Health Ventures' focus" in email.body and "s's" not in email.body
    assert "Austin, Texas-based" in email.body
    assert email.body.startswith("Hi [First name],") and "[Your name]" in email.body


def test_deals_come_only_from_the_investor_record(outcome):
    match = next(m for m in outcome.run.matches if m.investor_name == "Aldermoor Health Ventures")
    inv = outcome.records[match.investor_id]
    email = sample_email(match, outcome.company, inv=inv.model_copy(update={"historical_investments": []}),
                         today=TODAY)
    assert "Cortexa" not in email.body and "Close to your investment" not in email.body


@pytest.mark.parametrize(("investor_type", "subject", "phrase"), [
    ("angel_group", "Screening request: Lumora Diagnostics", "screening slot"),
    ("angel", "Intro: Lumora Diagnostics, raising a $6M Series A round", "20-minute call"),
    ("corporate_vc", "Lumora Diagnostics and Aldermoor Health Ventures: partnership and investment",
     "commercial partnership"),
    ("family_office", "Direct investment: Lumora Diagnostics", "direct investment opportunity"),
])
def test_framing_follows_the_investor_type(outcome, investor_type, subject, phrase):
    email = sample_email(
        next(m for m in outcome.run.matches if m.investor_name == "Aldermoor Health Ventures")
        .model_copy(update={"investor_type": investor_type}),
        outcome.company, today=TODAY)
    assert email.subject == subject
    assert phrase in email.body


def test_no_lead_ask_when_the_round_has_a_lead(outcome):
    email = email_for(outcome, "Aldermoor Health Ventures")
    match = next(m for m in outcome.run.matches if m.investor_name == "Aldermoor Health Ventures")
    no_lead = sample_email(match, outcome.company, needs_lead=False, today=TODAY)
    assert "leading the round" in email.body and "leading the round" not in no_lead.body


def test_profiles_use_a_placeholder_name(outcome):
    if not outcome.run.archetype_matches:
        pytest.skip("no archetype matches for this deck")
    profile = outcome.run.archetype_matches[0]
    email = sample_email(profile, outcome.company, today=TODAY)
    assert "[Investor name]" in email.body and profile.investor_name not in email.body
