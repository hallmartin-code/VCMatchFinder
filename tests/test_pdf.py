"""One-page PDF generation and overflow handling."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pymupdf
import pytest

from app.reporting.pdf_generator import (
    BODY_FLOOR,
    LADDER,
    TABLE_FLOOR,
    ReportContent,
    SnapshotItem,
    render_report,
)
from app.reporting.report_layout import MatchRow

LABELS = ["Company", "Sector", "Stage", "Geography", "Business Model", "Traction", "Round", "Valuation",
          "Capital Raised", "Strategic Need"]


def content(n_matches: int = 8, words: int = 60) -> ReportContent:
    text = " ".join(["substantial"] * words)
    row = MatchRow(name="Investor With A Fairly Long Name", subtitle="Venture capital fund", tier="tier_1",
                   tier_short="T1 · Contact now", archetype=False, fit=91, adjusted=84, confidence="High",
                   evidence="Verified", why=text, concern=text, angle=text)
    return ReportContent(
        company_name="Overflow Co", analysis_id="abcdef1234567890", prepared=date(2026, 9, 11),
        snapshot=[SnapshotItem(label, text, "pitch_deck") for label in LABELS],
        iip_points=[text] * 6, matches=[replace(row, name=f"{row.name} {i}") for i in range(n_matches)],
        next_action=text, data_quality=[text] * 6, synthetic_data=True,
    )


def text_of(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc[0].get_text()


def test_typical_report_is_one_page_with_all_sections():
    result = render_report(content(n_matches=6, words=12))
    assert result.page_count == 1
    text = text_of(result.pdf_bytes)
    for heading in ("TEN CAPITAL — INVESTOR MATCHING REPORT", "COMPANY SNAPSHOT", "IDEAL INVESTOR PROFILE",
                    "TOP INVESTOR MATCHES", "RECOMMENDED NEXT ACTION", "DATA QUALITY",
                    "Confidential – for recipients only.", "Compiled on September 11, 2026 by TEN Capital Network"):
        assert heading in text, heading
    assert "SYNTHETIC" in text


def test_overflowing_content_is_reduced_to_one_page():
    result = render_report(content(n_matches=8, words=80))
    assert result.page_count == 1
    assert result.rung > 0 and result.reductions


@pytest.mark.parametrize("n", [0, 1, 25])
def test_extreme_inputs_still_render_one_page(n):
    assert render_report(content(n_matches=n, words=400)).page_count == 1


def test_type_never_drops_below_readability_floor():
    assert all(rung.body_size >= BODY_FLOOR and rung.table_size >= TABLE_FLOOR for rung in LADDER)
    assert min(rung.max_matches for rung in LADDER) >= 5


def test_open_sans_is_embedded():
    pdf = render_report(content(n_matches=2, words=5)).pdf_bytes
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        fonts = {f[3] for f in doc[0].get_fonts()}
    assert any("OpenSans" in f for f in fonts), fonts
