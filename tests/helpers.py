"""Small factories shared by the tests."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.ingestion.models import DeckDocument, DeckPage
from app.models.investor import InvestorRecord

TODAY = date(2026, 9, 11)


def make_doc(*pages: str, fmt: str = "pdf", filename: str = "test_deck.pdf") -> DeckDocument:
    return DeckDocument(
        filename=filename, source_format=fmt,  # type: ignore[arg-type]
        pages=[DeckPage(number=i, title=text.split("\n")[0], text=text) for i, text in enumerate(pages, start=1)],
    )


def make_investor(**overrides: Any) -> InvestorRecord:
    base: dict[str, Any] = {
        "investor_id": "test-investor", "name": "Test Investor", "investor_type": "venture_capital",
        "investment_geographies": ["United States"], "sector_preferences": ["diagnostics"],
        "stage_preferences": ["series_a"], "min_check": 2e6, "max_check": 5e6, "lead_capability": "yes",
        "fund_status": "active", "data_source": "test",
    }
    base.update(overrides)
    return InvestorRecord(**base)
