"""Ranking, outreach tiers, archetype fallback, and no fabricated investors."""

from __future__ import annotations

from helpers import TODAY, make_investor

from app.matching.investor_profile import build_ideal_investor_profile
from app.matching.ranking import rank_investors
from app.models.company import InvestorPreferences
from app.models.match import TIER_ORDER


def rank(investors, company, config, prefs=None):
    prefs = prefs or InvestorPreferences()
    iip = build_ideal_investor_profile(company, prefs)
    return rank_investors(investors, company, prefs, config, iip, today=TODAY).run


def test_ranking_on_synthetic_dataset(lumora_company, dataset, config):
    run = rank(dataset.investors, lumora_company, config)
    top = run.matches[0]
    assert top.investor_name == "Aldermoor Health Ventures"
    assert top.outreach_priority == "tier_1" and top.evidence_quality == "verified"
    tiers = [TIER_ORDER[m.outreach_priority] for m in run.matches]
    assert tiers == sorted(tiers)  # ordered by outreach priority
    by_name = {m.investor_name: m for m in run.matches}
    assert by_name["Everline Health System Ventures"].outreach_priority == "tier_4"  # follows only; lead needed
    assert by_name["Brightwater Seed Collective"].outreach_priority == "tier_4"  # existing investor
    assert by_name["Upland Ridge Partners"].outreach_priority == "do_not_target"
    assert len(run.rejected) == 5


def test_no_investor_names_are_invented(lumora_company, dataset, config):
    run = rank(dataset.investors, lumora_company, config)
    names = {i.name for i in dataset.investors}
    assert {m.investor_name for m in run.matches} <= names
    assert all(m.kind == "archetype" and m.investor_id.startswith("archetype:") for m in run.archetype_matches)
    assert not ({m.investor_name for m in run.archetype_matches} & names)


def test_archetype_fallback_without_investor_data(lumora_company, config):
    run = rank([], lumora_company, config)
    assert run.matches == [] and run.used_archetype_fallback
    assert run.archetype_matches
    assert all(m.outreach_priority in {"tier_2", "tier_3"} for m in run.archetype_matches)
    assert all(m.evidence_quality == "inferred" for m in run.archetype_matches)
    rows = run.top(8)
    assert rows and all(r.kind == "archetype" for r in rows)


def test_preference_bonus_is_explicit_and_capped(lumora_company, config):
    prefs = InvestorPreferences(preferred_types=["venture_capital"], preferred_geographies=["United States"])
    match = rank([make_investor()], lumora_company, config, prefs).matches[0]
    assert match.preference_bonus == 4.0 and len(match.preference_matches) == 2
    assert match.ranking_score == min(100, match.adjusted_score + 4.0)


def test_user_exclusion_removes_and_audits(lumora_company, dataset, config):
    prefs = InvestorPreferences(excluded_investors=["Aldermoor Health Ventures"])
    run = rank(dataset.investors, lumora_company, config, prefs)
    assert "Aldermoor Health Ventures" not in {m.investor_name for m in run.matches}
    assert any(r.investor_name == "Aldermoor Health Ventures" for r in run.rejected)


def test_tier1_requires_current_round_fit(lumora_company, config):
    later = make_investor(stage_preferences=["series_b"])  # adjacent but enters later
    match = rank([later], lumora_company, config).matches[0]
    assert match.outreach_priority == "tier_3"
    assert "next round" in match.priority_reason
