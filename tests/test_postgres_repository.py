"""Postgres investor repository: row mapping and graceful failure (no database needed)."""

from __future__ import annotations

from datetime import date

import pytest

from app.config import Settings
from app.data import postgres_repository as pg
from app.errors import InvestorDataError
from app.services.investor_data_service import load_default_dataset, load_investor_dataset

ROW = {
    "name": "CTAN", "investor_type": "Angel Group", "website": None,
    "headquarters": "Austin, TX", "geography": "Austin-based [6]; invests in Texas (e.g. a Wisconsin deal) [6]",
    "sectors": ["Medtech", "SaaS / Software"], "industries_raw": None,
    "stages": ["Seed", "Series A"], "min_check": 342000, "max_check": 482000, "typical_check": 25000,
    "can_lead": True, "statuses": ["Active"], "last_deal_date": date(2026, 6, 3),
    "researched_on": date(2026, 11, 21),
    "research_sources": "https://example.org/a; https://example.org/b",
    "research_summary": "Sector: medtech [1]", "contacts": "A Person, B Person", "contact_count": 2,
    "contact_list": [{"first_name": "A", "last_name": "Person", "email": "a@ctan.example"},
                     {"first_name": "B", "last_name": "Person", "email": None}],
}


def test_row_maps_to_investor_record(monkeypatch):
    repo = pg.PostgresInvestorRepository("postgresql://unused")
    monkeypatch.setattr(repo, "_query", lambda: [ROW])
    pg._CACHE.clear()
    [inv] = repo.list_investors()
    assert inv.name == "CTAN" and inv.investor_type == "angel_group"
    assert inv.stage_preferences == ["seed", "series_a"]
    assert "sub:medtech" in inv.sector_tokens()
    assert inv.check_range() == (342000, 482000)
    assert inv.lead_capability == "yes" and inv.fund_status == "active"
    assert inv.geo_tokens() == {"US-TX"}  # the parenthetical example is not a mandate
    assert inv.has_verification and inv.last_known_investment == date(2026, 6, 3)
    assert "[1]" not in (inv.notes or "")
    assert [c.name for c in inv.contacts] == ["A Person", "B Person"]
    assert inv.primary_contact().email == "a@ctan.example"


def test_unknowns_stay_unknown_and_paused_means_not_investing(monkeypatch):
    row = {**ROW, "can_lead": None, "statuses": ["Active", "Paused"], "sectors": [], "stages": [],
           "min_check": None, "max_check": None, "typical_check": None, "research_sources": None,
           "researched_on": None, "geography": None}
    repo = pg.PostgresInvestorRepository("postgresql://unused")
    monkeypatch.setattr(repo, "_query", lambda: [row])
    pg._CACHE.clear()
    [inv] = repo.list_investors()
    assert inv.lead_capability == "unknown" and inv.fund_status == "between_funds"
    assert inv.sector_preferences == [] and inv.stage_preferences == []
    assert inv.check_range() == (None, None) and not inv.has_verification


def test_database_failure_becomes_a_warning(monkeypatch):
    repo = pg.PostgresInvestorRepository("postgresql://unused")

    def boom():
        raise InvestorDataError("Could not read investors from the database: refused")

    monkeypatch.setattr(repo, "_query", boom)
    pg._CACHE.clear()
    dataset = load_investor_dataset((), (), [repo])
    assert dataset.investors == []
    assert any("Could not read investors" in w for w in dataset.warnings)


def test_database_only_used_when_configured(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEN_DATABASE_URL", raising=False)
    settings = Settings(_env_file=None, investor_data_paths="")
    assert not settings.database_configured
    assert load_default_dataset(settings).sources == []

    calls = []
    monkeypatch.setattr(pg.PostgresInvestorRepository, "_query", lambda self: calls.append(1) or [ROW])
    pg._CACHE.clear()
    settings = Settings(_env_file=None, investor_data_paths="", database_url="postgresql://unused")
    dataset = load_default_dataset(settings)
    assert [i.name for i in dataset.investors] == ["CTAN"] and calls == [1]
    assert load_default_dataset(settings, include_defaults=False).investors == []


def test_empty_url_rejected():
    with pytest.raises(InvestorDataError):
        pg.PostgresInvestorRepository("  ")
