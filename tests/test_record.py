"""The TEN Capital JSON record."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest
from helpers import TODAY

from app.models.company import CompanyProfile, InvestorPreferences
from app.models.record import CompanyRecord
from app.pipeline import build_record, run_matching
from app.reporting.json_exporter import record_to_json, validate_record_json
from app.utils.logging import ConfidentialityFilter

STAMP = datetime(2026, 9, 11, 15, 0, tzinfo=UTC)


@pytest.fixture
def record(lumora_company, dataset, config, settings):
    outcome = run_matching(lumora_company, InvestorPreferences(), dataset, config, today=TODAY)
    return build_record(outcome, config=config, settings=settings, timestamp=STAMP)


def test_record_has_the_specified_structure(record):
    data = json.loads(record_to_json(record))
    for key in ("analysis_id", "timestamp", "user", "company", "investor_preferences", "match_criteria",
                "ideal_investor_profile", "top_matches", "data_gaps"):
        assert key in data
    assert "consent" not in data
    for key in ("name", "sector", "subsector", "stage", "geography", "business_model", "revenue", "traction",
                "round_type", "raise_amount", "amount_raised", "valuation", "strategic_needs"):
        assert key in data["company"]
    assert data["match_criteria"]["stage_weight"] == 15 and data["match_criteria"]["sector_weight"] == 20
    first = data["top_matches"][0]
    for key in ("rank", "investor", "investor_type", "fit_score", "confidence", "evidence_quality", "rationale",
                "likely_concerns", "outreach_angle", "required_materials", "outreach_priority", "score_breakdown"):
        assert key in first
    assert len(first["score_breakdown"]) == 9
    assert data["company_provenance"]["raise_amount"]["location"] == "Page 12"
    assert {r["investor_name"] for r in data["rejected_investors"]} >= {"Quarry Lane Capital"}
    assert len(data["top_matches"]) <= 8


def test_record_round_trips_through_validation(record):
    again = validate_record_json(record_to_json(record))
    assert again == record


def test_company_record_stays_in_sync_with_profile():
    CompanyRecord(**CompanyProfile().summary())  # extra="forbid" would raise on drift


def test_confidentiality_filter_truncates_long_messages():
    record = logging.LogRecord("app.x", logging.INFO, __file__, 1, "deck text " * 100, None, None)
    ConfidentialityFilter().filter(record)
    assert len(record.getMessage()) < 400 and "confidentiality guard" in record.getMessage()
