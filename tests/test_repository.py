"""Investor repositories: JSON/CSV loading, aliases, duplicates, incomplete data."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.data.investor_repository import (
    CsvInvestorRepository,
    JsonInvestorRepository,
    RemoteInvestorRepository,
    parse_fund_status,
)
from app.errors import InvestorDataError, UnsupportedFormatError
from app.services.investor_data_service import load_investor_dataset, repository_for_upload

CSV = """Investor,Type,Check Size,Stage Focus,Sector Focus,Leads Rounds,Lead History,Fund Status,Portfolio
Northlight Dx Fund,Micro VC,$1.5M-$3M,"Seed, Series A","diagnostics, sepsis",Yes,Led Vessl Dx Series A 2025; led Coriolis Seed 2024,"Fund II 2025, actively deploying","Vessl Dx, Coriolis"
Harbour Test Ventures,Venture Fund,$2M-$4M,Series A,"medtech, diagnostics",Sometimes,,"Between funds - no dry powder until 2027",
,Venture Fund,$1M,Seed,healthcare,No,,,
"""


def test_json_dataset_loads_and_flags_synthetic(dataset):
    assert dataset.any_synthetic and dataset.all_synthetic
    assert len(dataset.investors) == 16
    assert any("SYNTHETIC" in w for w in dataset.warnings)


def test_duplicate_records_are_merged(dataset):
    matches = [i for i in dataset.investors if "aldermoor" in i.name.lower()]
    assert len(matches) == 1
    merged = matches[0]
    assert merged.name == "Aldermoor Health Ventures"
    assert "Halcyon Assays" in merged.portfolio_companies  # unioned from the duplicate
    assert any("Merged 2 duplicate" in w for w in dataset.warnings)


def test_incomplete_record_is_kept_without_invented_values(dataset):
    upland = next(i for i in dataset.investors if i.name == "Upland Ridge Partners")
    assert upland.investor_type == "unknown"
    assert upland.stage_preferences == [] and upland.check_range() == (None, None)
    assert upland.fund_status == "unknown"


def test_csv_with_alias_headers_and_deal_history():
    repo = CsvInvestorRepository(data=CSV.encode(), name="targets.csv")
    investors = {i.name: i for i in repo.list_investors()}
    northlight = investors["Northlight Dx Fund"]
    assert northlight.investor_type == "micro_vc"
    assert northlight.check_range() == (1.5e6, 3e6)
    assert northlight.stage_preferences == ["seed", "series_a"]
    assert northlight.lead_capability == "yes"
    assert northlight.fund_status == "active"
    deal = northlight.historical_investments[0]
    assert (deal.company, deal.stage_key(), deal.year, deal.led) == ("Vessl Dx", "series_a", 2025, True)
    assert investors["Harbour Test Ventures"].fund_status == "between_funds"
    assert any("no investor name" in w for w in repo.warnings)  # blank-name row skipped


def test_csv_without_name_column_is_rejected():
    with pytest.raises(InvestorDataError, match="name column"):
        CsvInvestorRepository(data=b"Type,Stage\nVC,Seed\n", name="bad.csv")


def test_invalid_json_is_rejected():
    with pytest.raises(InvestorDataError, match="not valid JSON"):
        JsonInvestorRepository(data=b"{not json", name="bad.json")


def test_missing_dataset_falls_back_to_archetype_warning(tmp_path):
    ds = load_investor_dataset([tmp_path / "missing.json"])
    assert ds.investors == []
    assert any("not found" in w for w in ds.warnings)
    assert any("archetypes" in w for w in ds.warnings)


def test_upload_type_validation():
    with pytest.raises(UnsupportedFormatError):
        repository_for_upload("investors.txt", b"name\nX\n")


def test_fund_status_parsing():
    assert parse_fund_status("Fund IV closed 2025, actively deploying")[0] == "active"
    assert parse_fund_status("Fund III raising, no dry powder until 2027")[0] == "between_funds"
    assert parse_fund_status("No longer investing")[0] == "inactive"
    assert parse_fund_status(None) == ("unknown", None)


def test_remote_repository_base_normalizes_rows():
    class FakeCrm(RemoteInvestorRepository):
        def fetch_rows(self) -> Iterator[dict[str, Any]]:
            yield {"Firm": "Remote Capital", "Stages": "Series A", "Sectors": "fintech"}

    investors = FakeCrm("fake-crm").list_investors()
    assert investors[0].name == "Remote Capital"
    assert investors[0].data_source == "fake-crm"
