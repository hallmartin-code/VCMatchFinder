"""Target investors' contacts and their sample emails as a spreadsheet for Pipedrive.

One row per contact: every person the investor list holds for each target investor, with
the sample email addressed to that person, plus the organization's priority and fit. An
investor with no listed contact still gets one row, with blank contact columns and a
``[First name]`` placeholder in the email. The columns map onto the person, organization
and deal fields of Pipedrive's spreadsheet import (and other CRMs' CSV imports).
The file is UTF-8 with a byte-order mark so Excel opens it with the dashes and accents
intact; multi-line email bodies are quoted, which every CRM import accepts.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Iterable
from typing import Any

from app.intelligence.sample_email import SampleEmail
from app.models.company import CompanyProfile
from app.models.investor import InvestorContact, InvestorRecord
from app.models.match import TIER_LABELS, InvestorMatch

COLUMNS: tuple[str, ...] = (
    "Person name", "First name", "Last name", "Email", "Primary contact",
    "Organization name", "Website", "Investor type", "Priority", "Fit score", "Confidence",
    "Email subject", "Email body", "Why it fits", "Top concern",
    "Status", "Deal title", "Synthetic test record",
)


def contact_rows(match: InvestorMatch, company: CompanyProfile, inv: InvestorRecord | None,
                 email_for: Callable[[InvestorContact | None], SampleEmail]) -> list[dict[str, Any]]:
    """Rows for one target investor: its primary contact first, then everyone else listed.

    ``email_for`` drafts the email for a contact (``None`` when the list has no one).
    ``Status`` is filled in at export.
    """
    primary = inv.primary_contact() if inv else None
    people: list[InvestorContact | None] = [primary] if primary else [None]
    people += [c for c in (inv.contacts if inv else []) if c is not primary]
    organization = {
        "investor_id": match.investor_id,
        "Organization name": match.investor_name,
        "Website": (inv.website if inv else None) or "",
        "Investor type": match.investor_type_label,
        "Priority": TIER_LABELS[match.outreach_priority],
        "Fit score": round(match.fit_score),
        "Confidence": match.confidence,
        "Why it fits": match.rationale,
        "Top concern": match.likely_concerns[0] if match.likely_concerns else "",
        "Status": "",
        "Deal title": f"{company.display_name} – {match.investor_name}",
        "Synthetic test record": "Yes" if match.is_synthetic else "",
    }
    rows = []
    for person in people:
        email = email_for(person)
        rows.append({
            **organization,
            "Person name": person.name if person else "",
            "First name": person.first_name if person else "",
            "Last name": person.last_name if person else "",
            "Email": (person.email or "") if person else "",
            "Primary contact": "Yes" if person is not None and person is primary else "",
            "Email subject": email.subject,
            "Email body": email.body,
        })
    return rows


def crm_csv(rows: Iterable[dict[str, Any]], *, contacted: Iterable[str] = (),
            intro_list: Iterable[str] = ()) -> bytes:
    """The rows as CSV bytes, with each investor's current status."""
    contacted, intro_list = set(contacted), set(intro_list)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        investor_id = row.get("investor_id")
        status = ("Already contacted" if investor_id in contacted
                  else "On intro list" if investor_id in intro_list else "Not contacted")
        writer.writerow({**row, "Status": status})
    return buffer.getvalue().encode("utf-8-sig")
