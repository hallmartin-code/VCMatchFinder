"""Hard filters fire only on known data, and every reason is audited."""

from __future__ import annotations

from helpers import make_investor

from app.matching.filters import apply_hard_filters, check_investor
from app.models.company import InvestorPreferences
from app.models.investor import InvestorRecord

PREFS = InvestorPreferences()


def codes(investor, company, prefs=PREFS):
    return {r.code for r in check_investor(investor, company, prefs)}


def test_compatible_investor_passes(lumora_company):
    assert codes(make_investor(), lumora_company) == set()


def test_stage_mismatch(lumora_company):
    assert "stage_mismatch" in codes(make_investor(stage_preferences=["series_c", "growth"]), lumora_company)
    # One round away is not a hard mismatch.
    assert "stage_mismatch" not in codes(make_investor(stage_preferences=["seed"]), lumora_company)


def test_sector_mandate_mismatch_and_exclusion(lumora_company):
    assert "mandate_mismatch" in codes(make_investor(sector_preferences=["fintech"]), lumora_company)
    assert "mandate_mismatch" not in codes(make_investor(sector_preferences=["healthcare"]), lumora_company)
    assert "sector_exclusion" in codes(make_investor(excluded_sectors=["diagnostics"]), lumora_company)


def test_geography_restriction(lumora_company):
    assert "geography_restriction" in codes(make_investor(investment_geographies=["Europe"]), lumora_company)
    assert codes(make_investor(investment_geographies=["Global"]), lumora_company) == set()
    assert "excluded_geography" in codes(make_investor(excluded_geographies=["Texas"]), lumora_company)


def test_check_size_incompatible(lumora_company):
    assert "check_size_incompatible" in codes(make_investor(min_check=10e6, max_check=25e6), lumora_company)


def test_fund_not_investing(lumora_company):
    inv = make_investor(fund_status="between_funds", fund_status_note="Fund III raising")
    reasons = check_investor(inv, lumora_company, PREFS)
    assert reasons[0].code == "fund_not_investing" and "Fund III raising" in reasons[0].detail


def test_portfolio_conflict_with_named_competitor(lumora_company):
    reasons = check_investor(make_investor(portfolio_companies=["Sepsiq Labs"]), lumora_company, PREFS)
    assert reasons and reasons[0].code == "portfolio_conflict"
    assert "Sepsiq Labs" in reasons[0].detail and "Page 8" in reasons[0].detail


def test_user_exclusions(lumora_company):
    prefs = InvestorPreferences(excluded_types=["corporate_vc"], excluded_investors=["Test Investor LLC"])
    found = codes(make_investor(investor_type="corporate_vc"), lumora_company, prefs)
    assert {"excluded_type", "excluded_by_user"} <= found


def test_missing_investor_data_never_filters(lumora_company):
    bare = InvestorRecord(investor_id="bare", name="Bare Minimum Partners")
    assert codes(bare, lumora_company) == set()


def test_rejections_keep_all_reasons(lumora_company, dataset):
    viable, rejected = apply_hard_filters(dataset.investors, lumora_company, PREFS)
    by_name = {r.investor_name: {x.code for x in r.reasons} for r in rejected}
    assert by_name["Ironvale Growth Partners"] == {"stage_mismatch", "check_size_incompatible"}
    assert by_name["Northgate Software Capital"] == {"mandate_mismatch"}
    assert by_name["Quarry Lane Capital"] == {"fund_not_investing"}
    assert by_name["Marrow & Pine Ventures"] == {"portfolio_conflict"}
    assert by_name["Solenne European Health Fund"] == {"geography_restriction"}
    assert len(viable) + len(rejected) == len(dataset.investors)


def test_west_virginia_is_not_virginia():
    from app.utils.geography import parse_geo_tokens, resolve_location

    assert resolve_location("West Virginia, United States").subdivision == "US-WV"
    assert "US-VA" not in parse_geo_tokens("Morgantown, West Virginia")
    assert {"US-VA", "US-WV"} <= parse_geo_tokens("Virginia and West Virginia")
