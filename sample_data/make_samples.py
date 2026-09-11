"""Generate the synthetic sample decks used by the tests and the demo.

    python sample_data/make_samples.py

Everything here is fictitious: Lumora Diagnostics, its people, competitors, investors and
figures are invented for testing. Nothing describes a real company.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches, Pt
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent

#: (title, lines, speaker notes)
SLIDES: list[tuple[str, list[str], str | None]] = [
    ("Lumora Diagnostics", [
        "Rapid host-response sepsis testing at the point of care",
        "Investor Presentation | Series A | September 2026",
        "Confidential",
    ], None),
    ("The Problem", [
        "Sepsis affects 1.7 million US adults each year (CDC).",
        "Blood culture takes 24-72 hours; clinicians treat blind in the meantime.",
        "Every hour of delayed treatment raises mortality risk.",
    ], None),
    ("Our Solution", [
        "LumoraDx: a 20-minute host-response assay on a benchtop reader.",
        "Measures a proprietary 6-biomarker panel from a single blood draw.",
        "AI-assisted risk score integrated into the hospital EHR.",
    ], None),
    ("Product & Regulatory", [
        "Research-use-only (RUO) units in use at 3 hospital pilot sites.",
        "FDA Breakthrough Device designation granted in March 2025.",
        "510(k) submission planned for Q2 2027 after the validation study.",
    ], None),
    ("Traction", [
        "$420K revenue in FY2025 from RUO reader placements and cartridges",
        "Revenue grew 140% year-over-year",
        "2 signed LOIs with regional health systems",
        "Collaboration with two academic medical centers on the validation protocol",
    ], "Pilot sites went live in January 2026; the Ohio site is a 900-bed academic center."),
    ("Business Model", [
        "Business model: razor-and-blade; reader placed, recurring cartridges at $85 per test",
        "Hospitals buy cartridges on annual supply contracts.",
        "Target gross margin of 70% at scale.",
    ], None),
    ("Market Opportunity", [
        "TAM: $4.2B US sepsis diagnostics market (Grand View Research, 2025)",
        "SAM: $1.1B across 5,500 US acute-care hospitals",
        "SOM: $120M with 8% penetration of target hospitals by 2031",
    ], None),
    ("Competitive Landscape", [
        "Competitors: Sepsiq Labs, Tessary Dx, and blood culture incumbents",
        "LumoraDx is the only host-response assay under 30 minutes outside the core lab.",
    ], None),
    ("Intellectual Property", [
        "4 issued US patents covering the biomarker panel and cartridge design",
        "2 PCT applications pending",
        "Exclusive license to the scoring algorithm from a university partner",
    ], None),
    ("Team", [
        "Dana Whitfield, CEO: previously VP Commercial at a diagnostics company",
        "Dr. Samir Okoye, CSO: PhD immunology, 12 years in host-response research",
        "Lena Ortiz, VP Regulatory: led 6 FDA 510(k) clearances",
    ], None),
    ("Financial Plan", [
        "Revenue target: $12M ARR by 2029",
        "Path to profitability in 2030",
        "Gross margin target of 70% by 2028",
    ], None),
    ("The Ask", [
        "We are raising a $6M Series A",
        "$1.5M committed from existing investors",
        "Pre-money valuation: $18M",
        "Seeking a lead investor for the round",
        "Existing investors: Brightwater Seed Collective, Pellucid Angels",
        "Capital raised to date: $3.2M (seed round plus NIH SBIR grant)",
        "Use of funds:",
        "45% - Clinical validation study (n=600 patients)",
        "30% - FDA 510(k) submission and regulatory",
        "25% - Commercial team and hospital onboarding",
        "Headquartered in Austin, Texas | www.lumoradx.example | Lumora Diagnostics, Inc.",
    ], "Two existing investors have committed $1.5M; we want a specialist lead who can take $3M or more."),
]

SPARSE_SLIDES: list[tuple[str, list[str]]] = [
    ("Nimbus Robotics", ["Autonomous picking robots for mid-size warehouses"]),
    ("Why now", ["Labor shortages are pushing warehouses toward automation."]),
]


def build_pdf(path: Path) -> Path:
    page = landscape(letter)
    pdf = canvas.Canvas(str(path), pagesize=page)
    for title, lines, _notes in SLIDES:
        pdf.setFont("Helvetica-Bold", 26)
        pdf.drawString(54, page[1] - 72, title)
        pdf.setFont("Helvetica", 14)
        y = page[1] - 130
        for line in lines:
            pdf.drawString(64, y, line)
            y -= 28
        pdf.showPage()
    pdf.save()
    return path


def build_pptx(path: Path) -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    for index, (title, lines, notes) in enumerate(SLIDES):
        if index == 0:
            slide = prs.slides.add_slide(prs.slide_layouts[0])
            slide.shapes.title.text = title
            slide.placeholders[1].text = "\n".join(lines)
        else:
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = title
            body = slide.placeholders[1].text_frame
            body.text = lines[0]
            for line in lines[1:]:
                paragraph = body.add_paragraph()
                paragraph.text = line
                paragraph.font.size = Pt(16)
        if title == "Traction":
            chart_data = CategoryChartData()
            chart_data.categories = ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"]
            chart_data.add_series("Revenue", (60, 90, 120, 150))
            frame = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(8.5), Inches(4.5),
                                           Inches(4.3), Inches(2.7), chart_data)
            frame.chart.has_title = True
            frame.chart.chart_title.text_frame.text = "Quarterly revenue ($K)"
        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    terms = prs.slides.add_slide(prs.slide_layouts[5])
    terms.shapes.title.text = "Round Terms"
    rows = [("Term", "Detail"), ("Round", "Series A"), ("Raise", "$6M"),
            ("Pre-money valuation", "$18M"), ("Committed", "$1.5M")]
    table = terms.shapes.add_table(len(rows), 2, Inches(1), Inches(1.8), Inches(8), Inches(2.5)).table
    for r, (left, right) in enumerate(rows):
        table.cell(r, 0).text = left
        table.cell(r, 1).text = right
    prs.save(str(path))
    return path


def build_scanned_pdf(path: Path) -> Path:
    """Image-only pages: shapes and no text layer, like a scanned deck."""
    page = landscape(letter)
    pdf = canvas.Canvas(str(path), pagesize=page)
    for i in range(5):
        pdf.setFillColorRGB(0.12, 0.22, 0.39)
        pdf.rect(40, page[1] - 110, page[0] - 80, 60, fill=1, stroke=0)
        pdf.setFillColorRGB(0.85, 0.87, 0.9)
        for j in range(4):
            pdf.rect(60, page[1] - 190 - j * 70, page[0] - 160 - i * 20, 40, fill=1, stroke=0)
        pdf.showPage()
    pdf.save()
    return path


def build_sparse_pdf(path: Path) -> Path:
    page = landscape(letter)
    pdf = canvas.Canvas(str(path), pagesize=page)
    for title, lines in SPARSE_SLIDES:
        pdf.setFont("Helvetica-Bold", 26)
        pdf.drawString(54, page[1] - 72, title)
        pdf.setFont("Helvetica", 14)
        y = page[1] - 130
        for line in lines:
            pdf.drawString(64, y, line)
            y -= 28
        pdf.showPage()
    pdf.save()
    return path


def main(out_dir: Path = HERE) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for builder, name in ((build_pdf, "lumora_diagnostics_series_a.pdf"),
                          (build_pptx, "lumora_diagnostics_series_a.pptx"),
                          (build_scanned_pdf, "scanned_deck.pdf"),
                          (build_sparse_pdf, "sparse_deck.pdf")):
        path = builder(out_dir / name)
        print(f"wrote {path.relative_to(out_dir.parent) if out_dir.parent in path.parents else path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE))
