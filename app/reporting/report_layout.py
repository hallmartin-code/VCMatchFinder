"""Reusable PDF components for TEN Capital one-page reports.

Every component takes a ``ReportStyles`` built from a ``LayoutConfig``, so the one-page
fitting ladder can re-render the same content at tighter spacing or smaller type without
the components knowing anything about fitting. Type sizes, the footer and the match-table
columns come from the report template (``app/reporting/template.py``).

Markup note: bold/italic are applied with explicit ``<font name=...>`` tags rather than
``<b>``/``<i>``, which only resolve for registered font families.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import Flowable, Paragraph, Spacer, Table, TableStyle
from reportlab.platypus.flowables import HRFlowable

from app.reporting.template import load_template
from app.reporting.theme import (
    BLUE,
    CALLOUT_FILL,
    CONFIDENCE_COLORS,
    GREY,
    INK,
    LIGHT_GREY,
    NAVY,
    RULE,
    TIER_COLORS,
    WARN,
    WHITE,
    ZEBRA,
    font,
)

_TEMPLATE = load_template()
SECTION_SIZE = _TEMPLATE.typography.section_heading_pt
TITLE_SIZE = _TEMPLATE.typography.title_pt
FOOTER_SIZE = _TEMPLATE.footer.font_pt
#: Footer logo at the house-standard size (template footer.logo: 0.67" x 0.25").
LOGO_W = float(_TEMPLATE.footer.logo.get("width_in", 0.67)) * 72
LOGO_H = float(_TEMPLATE.footer.logo.get("height_in", 0.25)) * 72


@dataclass(frozen=True)
class LayoutConfig:
    body_size: float = 9.0
    table_size: float = 8.0
    spacing: float = 1.0


@dataclass(frozen=True)
class ReportStyles:
    cfg: LayoutConfig
    title: ParagraphStyle
    meta: ParagraphStyle
    warning: ParagraphStyle
    section: ParagraphStyle
    body: ParagraphStyle
    bullet: ParagraphStyle
    label: ParagraphStyle
    value: ParagraphStyle
    cell: ParagraphStyle
    cell_small: ParagraphStyle
    cell_small_center: ParagraphStyle
    header_cell: ParagraphStyle
    fit: ParagraphStyle
    callout: ParagraphStyle
    caption: ParagraphStyle


def make_styles(cfg: LayoutConfig) -> ReportStyles:
    regular, semibold, bold = font("OpenSans"), font("OpenSans-SemiBold"), font("OpenSans-Bold")
    b, t = cfg.body_size, cfg.table_size

    def style(name: str, **kwargs: Any) -> ParagraphStyle:
        return ParagraphStyle(name, **kwargs)

    return ReportStyles(
        cfg=cfg,
        title=style("title", fontName=bold, fontSize=TITLE_SIZE, leading=TITLE_SIZE * 1.2, textColor=NAVY),
        meta=style("meta", fontName=regular, fontSize=b - 0.5, leading=(b - 0.5) * 1.35, textColor=GREY),
        warning=style("warning", fontName=semibold, fontSize=b - 1, leading=(b - 1) * 1.35, textColor=WARN),
        section=style("section", fontName=bold, fontSize=SECTION_SIZE, leading=SECTION_SIZE * 1.2,
                      textColor=NAVY),
        body=style("body", fontName=regular, fontSize=b, leading=b * 1.3, textColor=INK),
        bullet=style("bullet", fontName=regular, fontSize=b, leading=b * 1.3, textColor=INK,
                     leftIndent=10, bulletIndent=1, bulletFontName=regular, bulletFontSize=b),
        label=style("label", fontName=semibold, fontSize=b - 1, leading=(b - 1) * 1.3, textColor=GREY),
        value=style("value", fontName=regular, fontSize=b - 0.5, leading=(b - 0.5) * 1.28, textColor=INK),
        cell=style("cell", fontName=regular, fontSize=t, leading=t * 1.22, textColor=INK),
        cell_small=style("cell_small", fontName=regular, fontSize=t - 1.2, leading=(t - 1.2) * 1.25,
                         textColor=GREY),
        cell_small_center=style("cell_small_center", fontName=regular, fontSize=t - 1.2,
                                leading=(t - 1.2) * 1.25, textColor=GREY, alignment=TA_CENTER),
        header_cell=style("header_cell", fontName=bold, fontSize=t, leading=t * 1.2, textColor=WHITE),
        fit=style("fit", fontName=bold, fontSize=t + 2.5, leading=(t + 2.5) * 1.1, textColor=NAVY,
                  alignment=TA_CENTER),
        callout=style("callout", fontName=semibold, fontSize=b, leading=b * 1.35, textColor=NAVY),
        caption=style("caption", fontName=regular, fontSize=t - 1.2, leading=(t - 1.2) * 1.3,
                      textColor=LIGHT_GREY),
    )


def esc(text: str | None) -> str:
    return escape(text or "")


def title_block(title: str, meta: str, warning: str | None, styles: ReportStyles) -> list[Flowable]:
    items: list[Flowable] = [Paragraph(esc(title), styles.title), Paragraph(esc(meta), styles.meta)]
    if warning:
        items.append(Paragraph(esc(warning), styles.warning))
    return items


def section_header(text: str, styles: ReportStyles) -> list[Flowable]:
    s = styles.cfg.spacing
    return [
        Spacer(1, 7 * s),
        Paragraph(esc(text.upper()), styles.section),
        HRFlowable(width="100%", thickness=0.6, color=NAVY, spaceBefore=1.5, spaceAfter=3.5 * s),
    ]


def key_value_grid(pairs: list[tuple[str, str]], styles: ReportStyles, width: float,
                   *, label_width: float = 74.0) -> Table:
    """Two label/value pairs per row. Values are pre-built Paragraph markup."""
    value_width = width / 2 - label_width
    rows = []
    for i in range(0, len(pairs), 2):
        left = pairs[i]
        right = pairs[i + 1] if i + 1 < len(pairs) else ("", "")
        rows.append([
            Paragraph(esc(left[0]), styles.label), Paragraph(left[1], styles.value),
            Paragraph(esc(right[0]), styles.label), Paragraph(right[1], styles.value),
        ])
    pad = 1.7 * styles.cfg.spacing
    table = Table(rows, colWidths=[label_width, value_width, label_width, value_width])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), pad),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE),
    ]))
    return table


def bullet_list(items: list[str], styles: ReportStyles) -> list[Flowable]:
    return [Paragraph(esc(item), styles.bullet, bulletText="•") for item in items]


@dataclass(frozen=True)
class MatchRow:
    name: str
    subtitle: str
    tier: str
    tier_short: str
    archetype: bool
    #: Scores are numbers in a report and placeholder strings in the rendered template.
    fit: float | str
    adjusted: float | str
    confidence: str
    evidence: str
    why: str
    concern: str
    angle: str


def template_columns() -> list[tuple[str, str, float]]:
    """(key, header, width fraction) for each match-table column, in template order."""
    return [(c.key, c.header, c.width_fraction) for c in _TEMPLATE.section("top_investor_matches").columns]


def _score(value: float | str) -> str:
    return value if isinstance(value, str) else f"{value:.0f}"


def match_table(rows: list[MatchRow], styles: ReportStyles, width: float,
                columns: Sequence[tuple[str, str, float]] | None = None) -> Table:
    cols = list(columns or template_columns())
    bold, italic = font("OpenSans-Bold"), font("OpenSans-Italic")
    widths = [width * fraction for _key, _header, fraction in cols]
    data: list[list[Any]] = [[Paragraph(esc(header), styles.header_cell) for _key, header, _f in cols]]
    for row in rows:
        name_font = italic if row.archetype else bold
        tier_color = TIER_COLORS.get(row.tier, "#555555")
        conf_color = CONFIDENCE_COLORS.get(row.confidence, "#555555")
        cells: dict[str, Any] = {
            "investor": [Paragraph(f'<font name="{name_font}">{esc(row.name)}</font>', styles.cell),
                         Paragraph(f'<font color="{tier_color}">{esc(row.tier_short)}</font> · {esc(row.subtitle)}',
                                   styles.cell_small)],
            "fit": [Paragraph(esc(_score(row.fit)), styles.fit),
                    Paragraph(f"adj {esc(_score(row.adjusted))}", styles.cell_small_center)],
            "confidence": [Paragraph(f'<font name="{bold}" color="{conf_color}">{esc(row.confidence)}</font>',
                                     styles.cell),
                           Paragraph(esc(row.evidence), styles.cell_small)],
            "why": Paragraph(esc(row.why), styles.cell),
            "concern": Paragraph(esc(row.concern), styles.cell),
            "angle": Paragraph(esc(row.angle), styles.cell),
        }
        data.append([cells[key] for key, _header, _f in cols])
    pad = 2.6 * styles.cfg.spacing
    commands: list[tuple[Any, ...]] = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), pad),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, RULE),
    ]
    for index in range(2, len(data), 2):
        commands.append(("BACKGROUND", (0, index), (-1, index), ZEBRA))
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle(commands))
    return table


def callout(text: str, styles: ReportStyles, width: float) -> Table:
    pad = 4.0 * styles.cfg.spacing
    table = Table([[Paragraph(esc(text), styles.callout)]], colWidths=[width])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CALLOUT_FILL),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, BLUE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), pad),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
    ]))
    return table


def draw_footer(canvas: Any, doc: Any, *, title: str, compiled: str, confidentiality: str,
                logo: Path | None) -> None:
    """TEN Capital footer: title · page · compiled-on · logo, centered, Open Sans (template size)."""
    canvas.saveState()
    regular = font("OpenSans")
    page_width = doc.pagesize[0]
    gap = "      "
    text = f"{title}{gap}{canvas.getPageNumber()}{gap}{compiled}{gap}"
    text_width = pdfmetrics.stringWidth(text, regular, FOOTER_SIZE)
    logo_width = LOGO_W if logo else 0.0
    x = (page_width - text_width - logo_width) / 2
    y = 25.0
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(doc.leftMargin, 40, page_width - doc.rightMargin, 40)
    canvas.setFont(regular, FOOTER_SIZE)
    canvas.setFillColor(GREY)
    canvas.drawString(x, y, text)
    if logo:
        canvas.drawImage(str(logo), x + text_width, y - 6, width=LOGO_W, height=LOGO_H, mask="auto",
                         preserveAspectRatio=True)
    canvas.drawCentredString(page_width / 2, 12, confidentiality)
    canvas.restoreState()
