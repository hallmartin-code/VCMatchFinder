"""The Pipedrive spreadsheet: one row per contact, each with its own sample email."""

from __future__ import annotations

import csv
import io

import pytest
from helpers import TODAY

from app.intelligence.sample_email import sample_email
from app.models.company import InvestorPreferences
from app.models.investor import InvestorContact
from app.pipeline import run_matching
from app.reporting.crm_export import COLUMNS, contact_rows, crm_csv


@pytest.fixture
def outcome(lumora_company, dataset, config):
    return run_matching(lumora_company, InvestorPreferences(), dataset, config, today=TODAY)


def rows_for(outcome, match, inv):
    def draft(contact):
        return sample_email(match, outcome.company, inv=inv, needs_lead=True, today=TODAY, contact=contact)
    return contact_rows(match, outcome.company, inv, draft)


def all_rows(outcome):
    return [row for m in outcome.run.top(8) if m.kind == "investor"
            for row in rows_for(outcome, m, outcome.records[m.investor_id])]


def parse(data: bytes) -> list[dict[str, str]]:
    assert data.startswith(b"\xef\xbb\xbf")  # BOM: Excel opens UTF-8 correctly
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def with_contacts(outcome):
    match = outcome.run.top(8)[0]
    inv = outcome.records[match.investor_id].model_copy(update={"contacts": [
        InvestorContact(first_name="Ann", last_name="Lee"),
        InvestorContact(first_name="Dana", last_name="Reyes", email="dana@aldermoor.example"),
        InvestorContact(first_name="Sam", last_name="Ito", email="sam@aldermoor.example"),
    ]})
    return match, inv


def test_one_row_per_contact_each_with_its_own_email(outcome):
    match, inv = with_contacts(outcome)
    parsed = parse(crm_csv(rows_for(outcome, match, inv)))
    assert tuple(parsed[0]) == COLUMNS
    # Primary contact (first with an email) first, then everyone else in list order.
    assert [(r["Person name"], r["Email"], r["Primary contact"]) for r in parsed] == [
        ("Dana Reyes", "dana@aldermoor.example", "Yes"), ("Ann Lee", "", ""), ("Sam Ito", "sam@aldermoor.example", "")]
    assert [r["Email body"].split(",")[0] for r in parsed] == ["Hi Dana", "Hi Ann", "Hi Sam"]
    assert {r["Organization name"] for r in parsed} == {"Aldermoor Health Ventures"}
    assert parsed[0]["First name"] == "Dana" and parsed[0]["Last name"] == "Reyes"
    assert parsed[0]["Deal title"] == "Lumora Diagnostics – Aldermoor Health Ventures"


def test_investor_without_contacts_still_gets_one_row(outcome):
    rows = all_rows(outcome)  # the synthetic dataset lists no people
    parsed = parse(crm_csv(rows))
    assert len(parsed) == len([m for m in outcome.run.top(8) if m.kind == "investor"]) > 0
    first = parsed[0]
    assert first["Person name"] == first["Email"] == first["Primary contact"] == ""
    assert first["Email body"].startswith("Hi [First name],") and "\n\n" in first["Email body"]
    assert first["Synthetic test record"] == "Yes"  # the bundled dataset is fictitious


def test_status_reflects_contacted_and_intro_list(outcome):
    rows = all_rows(outcome)
    a, b = rows[0]["investor_id"], rows[1]["investor_id"]
    parsed = parse(crm_csv(rows, contacted=[a], intro_list=[a, b]))
    assert [r["Status"] for r in parsed[:3]] == ["Already contacted", "On intro list", "Not contacted"]
    assert "investor_id" not in parsed[0]
