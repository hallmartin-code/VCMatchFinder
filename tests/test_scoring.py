"""Deterministic fit scoring."""

from __future__ import annotations

import pytest
from helpers import TODAY, make_investor

from app.config import CRITERIA, MatchingConfig, MatchWeights
from app.errors import ConfigError
from app.matching.scoring import fit_score, score_investor
from app.models.company import InvestorPreferences
from app.models.investor import HistoricalInvestment


def breakdown(inv, company, config=None):
    config = config or MatchingConfig()
    return {c.criterion: c for c in score_investor(inv, company, InvestorPreferences(), config,
                                                   today=TODAY, needs_lead=True)}


def test_weights_normalize_to_100_and_validate():
    assert sum(MatchWeights().normalized().values()) == pytest.approx(100)
    equal = MatchWeights(**{k: 1 for k in CRITERIA}).normalized()
    assert all(v == pytest.approx(100 / 9) for v in equal.values())
    with pytest.raises(ConfigError):
        MatchWeights(**{k: 0 for k in CRITERIA})
    assert MatchWeights().as_record()["sector_weight"] == 20


def test_breakdown_retains_evidence_and_sums_to_fit(lumora_company):
    items = score_investor(make_investor(), lumora_company, InvestorPreferences(), MatchingConfig(),
                           today=TODAY, needs_lead=True)
    assert [c.criterion for c in items] == list(CRITERIA)
    for c in items:
        assert c.weighted_score == pytest.approx(c.raw_score * c.weight / 100, abs=1e-3)
        assert c.evidence and c.evidence_source.startswith("Investor: test")
    assert fit_score(items) == pytest.approx(sum(c.weighted_score for c in items), abs=0.05)


def test_scoring_is_deterministic(lumora_company):
    inv = make_investor()
    first = score_investor(inv, lumora_company, InvestorPreferences(), MatchingConfig(), today=TODAY, needs_lead=True)
    second = score_investor(inv, lumora_company, InvestorPreferences(), MatchingConfig(), today=TODAY, needs_lead=True)
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]


@pytest.mark.parametrize(("low", "high", "expected"), [
    (2e6, 5e6, 100),      # overlaps the 25-60% lead band of a $6M round
    (500e3, 1e6, 75),     # meaningful follow-on (>= 10%)
    (100e3, 300e3, 40),   # small participant (>= 3%)
    (25e3, 100e3, 15),    # too small to matter
    (4e6, 6e6, 60),       # would take most of the round
])
def test_check_size_bands(lumora_company, low, high, expected):
    assert breakdown(make_investor(min_check=low, max_check=high), lumora_company)["check_size"].raw_score == expected


@pytest.mark.parametrize(("sectors", "expected"), [
    (["diagnostics"], 100), (["healthcare"], 75), (["medical devices"], 65), (["generalist"], 50),
])
def test_sector_levels(lumora_company, sectors, expected):
    assert breakdown(make_investor(sector_preferences=sectors), lumora_company)["sector"].raw_score == expected


@pytest.mark.parametrize(("geos", "expected"), [(["Texas"], 100), (["North America"], 85), (["Global"], 70)])
def test_geography_levels(lumora_company, geos, expected):
    assert breakdown(make_investor(investment_geographies=geos), lumora_company)["geography"].raw_score == expected


def test_stage_adjacent_scores_partially(lumora_company):
    assert breakdown(make_investor(stage_preferences=["seed"]), lumora_company)["stage"].raw_score == 60


def test_missing_data_scores_unknown_not_favorable(lumora_company):
    b = breakdown(make_investor(min_check=None, max_check=None, business_model_preferences=[]), lumora_company)
    check = b["check_size"]
    assert check.raw_score == 40 and not check.known and check.confidence == 0
    assert check.missing_information == ["Investor check size"]
    assert b["business_model"].raw_score == 40
    # Unknown is always below any positive evidence.
    assert check.raw_score < breakdown(make_investor(min_check=100e3, max_check=300e3), lumora_company)["check_size"].raw_score + 1


def test_unknown_score_is_configurable(lumora_company):
    config = MatchingConfig.model_validate({"scoring": {"unknown_raw_score": 20}})
    assert breakdown(make_investor(min_check=None, max_check=None), lumora_company, config)["check_size"].raw_score == 20


def test_historical_evidence(lumora_company):
    assert breakdown(make_investor(), lumora_company)["historical_evidence"].raw_score == 0
    deals = [HistoricalInvestment(company=f"Dx{i}", sector="diagnostics", stage="Series A", year=2025) for i in range(2)]
    scored = breakdown(make_investor(historical_investments=deals), lumora_company)["historical_evidence"]
    assert scored.raw_score == pytest.approx(75, abs=0.1)  # 100 x (1 - 0.5^2)
    assert "Dx0" in scored.evidence


def test_lead_capability_depends_on_round_needs(lumora_company):
    assert breakdown(make_investor(lead_capability="yes"), lumora_company)["lead_capability"].raw_score == 100
    assert breakdown(make_investor(lead_capability="no"), lumora_company)["lead_capability"].raw_score == 10


def test_strategic_value_overlap(lumora_company):
    inv = make_investor(strategic_capabilities=["FDA regulatory strategy", "clinical validation sites"])
    assert breakdown(inv, lumora_company)["strategic_value"].raw_score == 100
