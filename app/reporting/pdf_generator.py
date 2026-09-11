"""The one-page TEN Capital Investor Matching Report.

Structure - section order, headings, snapshot labels, table columns, footer text, length
limits and type sizes - comes from the report template (``app/reporting/template.py``,
backed by ``templates/investor_matching_report.template.json``). This module binds record
data into that structure and keeps the page to one.

One page is a hard requirement. The generator measures the story against the frame and
walks a degradation ladder until it fits: fewer bullets and rows first, then tighter
spacing, then shorter narratives, and only then smaller type - never below the template's
floors. Each rung is verified by counting the pages of the rendered PDF, and whatever was
given up is recorded in the JSON record.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from functools import partial
from pathlib import Path

import pymupdf
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageTemplate, Paragraph, Spacer

from app.errors import ReportOverflowError
from app.models.match import EVIDENCE_LABELS, TIER_SHORT
from app.models.record import TenCapitalRecord
from app.reporting.report_layout import (
    LayoutConfig,
    MatchRow,
    ReportStyles,
    bullet_list,
    callout,
    draw_footer,
    esc,
    key_value_grid,
    make_styles,
    match_table,
    section_header,
    template_columns,
    title_block,
)
from app.reporting.template import load_template
from app.reporting.theme import font, logo_path, register_fonts
from app.utils.money import format_money
from app.utils.text import truncate

TEMPLATE = load_template()
_MARGINS = TEMPLATE.page.margins_pt
PAGE_W, PAGE_H = TEMPLATE.page.width_pt, TEMPLATE.page.height_pt
MARGIN_X = _MARGINS.left
MARGIN_TOP = _MARGINS.top
MARGIN_BOTTOM = _MARGINS.bottom
FRAME_W = PAGE_W - _MARGINS.left - _MARGINS.right
FRAME_H = PAGE_H - _MARGINS.top - _MARGINS.bottom

_HEADER = TEMPLATE.section("header")
_SNAPSHOT = TEMPLATE.section("company_snapshot")
_IIP = TEMPLATE.section("ideal_investor_profile")
_MATCHES = TEMPLATE.section("top_investor_matches")
_ACTION = TEMPLATE.section("recommended_next_action")
_QUALITY = TEMPLATE.section("data_quality")

REPORT_TITLE = TEMPLATE.document_title
FOOTER_TITLE = TEMPLATE.footer.title_text
CONFIDENTIALITY = TEMPLATE.footer.confidentiality
TABLE_CAPTION = _MATCHES.caption or ""
BODY_FLOOR = TEMPLATE.typography.body_floor_pt
TABLE_FLOOR = TEMPLATE.typography.table_floor_pt
_VALUATION_TYPES = {"pre_money": "pre-money", "post_money": "post-money", "cap": "valuation cap",
                    "unspecified": ""}


@dataclass(frozen=True)
class FitConfig:
    body_size: float = 9.0
    table_size: float = 8.0
    spacing: float = 1.0
    max_matches: int = 8
    why_chars: int = 125
    concern_chars: int = 80
    angle_chars: int = 150
    iip_points: int = 5
    iip_chars: int = 118
    gaps: int = 4
    gap_chars: int = 150
    value_chars: int = 80
    action_chars: int = 320

    def layout(self) -> LayoutConfig:
        return LayoutConfig(body_size=self.body_size, table_size=self.table_size, spacing=self.spacing)

    def describe_drops(self, base: FitConfig, content: ReportContent) -> list[str]:
        drops: list[str] = []
        if (self.why_chars, self.concern_chars, self.angle_chars) < (base.why_chars, base.concern_chars,
                                                                     base.angle_chars):
            drops.append("match narratives shortened")
        if self.max_matches < min(base.max_matches, len(content.matches)):
            drops.append(f"matches limited to {self.max_matches} of {len(content.matches)}")
        if self.iip_points < min(base.iip_points, len(content.iip_points)):
            drops.append(f"ideal-profile points limited to {self.iip_points}")
        if self.gaps < min(base.gaps, len(content.data_quality)):
            drops.append(f"data-quality notes limited to {self.gaps}")
        if self.value_chars < base.value_chars:
            drops.append("snapshot values shortened")
        if self.spacing < base.spacing:
            drops.append(f"spacing reduced {round((1 - self.spacing) * 100)}%")
        if self.body_size < base.body_size:
            drops.append(f"body type {base.body_size:g}pt → {self.body_size:g}pt")
        if self.table_size < base.table_size:
            drops.append(f"table type {base.table_size:g}pt → {self.table_size:g}pt")
        return drops


#: The first rung is the template exactly as specified.
BASE = FitConfig(
    body_size=TEMPLATE.typography.body_pt,
    table_size=TEMPLATE.typography.table_pt,
    max_matches=_MATCHES.max_items or 8,
    why_chars=_MATCHES.column("why").max_chars or 130,
    concern_chars=_MATCHES.column("concern").max_chars or 80,
    angle_chars=_MATCHES.column("angle").max_chars or 165,
    iip_points=_IIP.max_items or 5,
    iip_chars=_IIP.max_chars or 118,
    gaps=_QUALITY.max_items or 4,
    gap_chars=_QUALITY.max_chars or 150,
    value_chars=_SNAPSHOT.max_chars or 80,
    action_chars=_ACTION.max_chars or 320,
)
#: Content is reduced before type. The outreach angle is the most actionable text, so the
#: ladder drops bullets and rows (keeping narratives whole) before it shortens narratives,
#: and reduces type only at the end (floors come from the template).
_TRIMMED = {"why_chars": 105, "concern_chars": 65, "angle_chars": 135, "value_chars": 70}
LADDER: tuple[FitConfig, ...] = (
    BASE,
    replace(BASE, iip_points=4, gaps=3),
    replace(BASE, iip_points=4, gaps=3, max_matches=7),
    replace(BASE, iip_points=4, gaps=3, max_matches=7, spacing=0.85),
    replace(BASE, iip_points=4, gaps=3, max_matches=6, spacing=0.85),
    replace(BASE, iip_points=4, gaps=3, max_matches=6, spacing=0.8, **_TRIMMED),
    replace(BASE, iip_points=4, gaps=3, max_matches=6, spacing=0.8, **_TRIMMED, body_size=8.5, table_size=7.6),
    replace(BASE, iip_points=3, gaps=2, max_matches=_MATCHES.min_items or 5, spacing=0.75, why_chars=85,
            concern_chars=55, angle_chars=110, value_chars=64, action_chars=240, body_size=BODY_FLOOR,
            table_size=TABLE_FLOOR),
)


@dataclass(frozen=True)
class SnapshotItem:
    label: str
    value: str
    source: str


@dataclass
class ReportContent:
    company_name: str
    analysis_id: str
    prepared: date
    snapshot: list[SnapshotItem]
    iip_points: list[str]
    matches: list[MatchRow]
    next_action: str
    data_quality: list[str]
    synthetic_data: bool = False
    organization: str = "TEN Capital Network"
    #: Overrides used by the rendered template (placeholders instead of real values).
    meta_text: str | None = None
    compiled_text: str | None = None

    @classmethod
    def from_record(cls, record: TenCapitalRecord, *, organization: str = "TEN Capital Network") -> ReportContent:
        values = _snapshot_values(record)
        snapshot = [SnapshotItem(f.label or f.key, *values[f.key]) for f in _SNAPSHOT.fields]
        rows = [
            MatchRow(
                name=m.investor,
                subtitle="profile, not a named firm" if m.kind == "archetype" else m.investor_type,
                tier=m.outreach_priority_key, tier_short=TIER_SHORT[m.outreach_priority_key],
                archetype=m.kind == "archetype", fit=m.fit_score, adjusted=m.adjusted_score,
                confidence=m.confidence, evidence=EVIDENCE_LABELS.get(m.evidence_quality, m.evidence_quality),
                why=m.rationale,
                concern=m.likely_concerns[0] if m.likely_concerns else (_MATCHES.column("concern").empty_text or ""),
                angle=m.outreach_angle,
            )
            for m in record.top_matches
        ]
        synthetic = record.investor_dataset.is_synthetic
        quality = [g for g in record.data_gaps if not (synthetic and "SYNTHETIC" in g)]
        return cls(
            company_name=record.company.name or (_HEADER.field("meta_line").empty_text or "Unnamed company"),
            analysis_id=record.analysis_id,
            prepared=record.timestamp.date(),
            snapshot=snapshot,
            iip_points=list(record.ideal_investor_profile.summary_points),
            matches=rows,
            next_action=record.recommended_next_action,
            data_quality=quality,
            synthetic_data=synthetic,
            organization=organization,
        )


def _snapshot_values(record: TenCapitalRecord) -> dict[str, tuple[str, str]]:
    """(display value, provenance source) for every snapshot slot the template can name."""
    c = record.company
    provenance = record.company_provenance

    def source(*keys: str) -> str:
        found = [(provenance.get(k) or {}).get("source", "missing") for k in keys]
        known = [s for s in found if s != "missing"]
        if not known:
            return "missing"
        if "inference" in known:
            return "inference"
        if "user_supplied" in known:
            return "user_supplied"
        return known[0]

    website = (provenance.get("website") or {}).get("value")
    round_parts: list[str] = []
    if c.raise_amount is not None:
        round_parts.append(f"{format_money(c.raise_amount)} {c.round_type}".strip())
    elif c.round_type:
        round_parts.append(c.round_type)
    if c.round_committed is not None:
        round_parts.append(f"{format_money(c.round_committed)} committed")
    if round_parts:
        round_parts.append(f"lead: {c.lead_investor}" if c.lead_investor else "lead: open")
    valuation = (f"{format_money(c.valuation)} {_VALUATION_TYPES.get(c.valuation_type, '')}".strip()
                 if c.valuation is not None else "")
    stage = c.stage + (f" · product {c.product_stage.lower()}" if c.product_stage else "") if c.stage else ""
    needs = _SNAPSHOT.field("strategic_need").max_items or 3
    return {
        "company": (c.name + (f" · {website}" if website and c.name else ""), source("name")),
        "sector": (" / ".join(x for x in (c.sector, c.subsector) if x), source("subsector", "sector")),
        "stage": (stage, source("company_stage")),
        "geography": (c.geography, source("headquarters")),
        "business_model": (c.business_model, source("business_model")),
        "traction": (c.traction, source("traction")),
        "round": (" · ".join(round_parts), source("raise_amount", "round_type")),
        "valuation": (valuation, source("valuation")),
        "capital_raised": (f"{format_money(c.amount_raised)} to date" if c.amount_raised is not None else "",
                           source("amount_raised")),
        "strategic_need": (", ".join(c.strategic_needs[:needs]), source("strategic_needs")),
    }


@dataclass
class ReportResult:
    pdf_bytes: bytes
    rung: int
    reductions: list[str]
    page_count: int
    config: FitConfig


def _snapshot_value(item: SnapshotItem, limit: int, styles: ReportStyles) -> str:
    italic = font("OpenSans-Italic")
    text = truncate(item.value, limit)
    if not text or item.source == "missing":
        return f'<font name="{italic}" color="#8A8A8A">{esc(_SNAPSHOT.empty_text or "Not in deck")}</font>'
    markup = esc(text)
    marker = TEMPLATE.provenance_markers.get(item.source)
    if marker:
        size = max(5.5, styles.value.fontSize - 2)
        markup += f' <font size="{size:g}" color="#8A8A8A">{esc(marker)}</font>'
    return markup


def _date_text(value: date) -> str:
    return f"{value:%B} {value.day}, {value.year}"


# -------------------------------------------------------------- section builders


def _header(content: ReportContent, cfg: FitConfig, styles: ReportStyles) -> list[Flowable]:
    meta = content.meta_text or (_HEADER.field("meta_line").format or "{company_name}").format(
        company_name=content.company_name, prepared=_date_text(content.prepared),
        analysis_id_short=content.analysis_id[:8])
    warning = _HEADER.field("synthetic_warning").value if content.synthetic_data else None
    return title_block(REPORT_TITLE, meta, warning, styles)


def _snapshot(content: ReportContent, cfg: FitConfig, styles: ReportStyles) -> list[Flowable]:
    pairs = [(item.label, _snapshot_value(item, cfg.value_chars, styles)) for item in content.snapshot]
    return [*section_header(_SNAPSHOT.heading or "", styles),
            key_value_grid(pairs, styles, FRAME_W,
                           label_width=float(getattr(_SNAPSHOT, "label_width_pt", 74) or 74))]


def _iip(content: ReportContent, cfg: FitConfig, styles: ReportStyles) -> list[Flowable]:
    points = [truncate(p, cfg.iip_chars) for p in content.iip_points[: cfg.iip_points]]
    return [*section_header(_IIP.heading or "", styles),
            *bullet_list(points or [_IIP.empty_text or ""], styles)]


def _matches(content: ReportContent, cfg: FitConfig, styles: ReportStyles) -> list[Flowable]:
    story: list[Flowable] = section_header(_MATCHES.heading or "", styles)
    rows = content.matches[: cfg.max_matches]
    if not rows:
        return [*story, Paragraph(esc(_MATCHES.empty_text), styles.body)]
    trimmed = [replace(r, why=truncate(r.why, cfg.why_chars), concern=truncate(r.concern, cfg.concern_chars),
                       angle=truncate(r.angle, cfg.angle_chars)) for r in rows]
    return [*story, match_table(trimmed, styles, FRAME_W, template_columns()), Spacer(1, 2),
            Paragraph(esc(TABLE_CAPTION), styles.caption)]


def _action(content: ReportContent, cfg: FitConfig, styles: ReportStyles) -> list[Flowable]:
    return [*section_header(_ACTION.heading or "", styles),
            callout(truncate(content.next_action, cfg.action_chars), styles, FRAME_W)]


def _quality(content: ReportContent, cfg: FitConfig, styles: ReportStyles) -> list[Flowable]:
    gaps = [truncate(g, cfg.gap_chars) for g in content.data_quality[: cfg.gaps]]
    return [*section_header(_QUALITY.heading or "", styles),
            *bullet_list(gaps or [_QUALITY.empty_text or ""], styles)]


_BUILDERS: dict[str, Callable[[ReportContent, FitConfig, ReportStyles], list[Flowable]]] = {
    "header": _header,
    "company_snapshot": _snapshot,
    "ideal_investor_profile": _iip,
    "top_investor_matches": _matches,
    "recommended_next_action": _action,
    "data_quality": _quality,
}


def build_story(content: ReportContent, cfg: FitConfig, styles: ReportStyles) -> list[Flowable]:
    """Sections in template order."""
    story: list[Flowable] = []
    for section in TEMPLATE.ordered_sections():
        story += _BUILDERS[section.id](content, cfg, styles)
    return story


def story_height(story: list[Flowable], width: float = FRAME_W, height: float = FRAME_H) -> float:
    total = 0.0
    for flowable in story:
        _w, h = flowable.wrap(width, height)
        total += h + flowable.getSpaceBefore() + flowable.getSpaceAfter()
    return total


def _render(story: list[Flowable], content: ReportContent, generated_on: date) -> bytes:
    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=(PAGE_W, PAGE_H), leftMargin=_MARGINS.left, rightMargin=_MARGINS.right,
        topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOTTOM, title=f"{FOOTER_TITLE} — {content.company_name}",
        author=content.organization, subject="Investor Matching Report",
        creator="TEN Capital Investor Matching Engine",
    )
    frame = Frame(_MARGINS.left, MARGIN_BOTTOM, FRAME_W, FRAME_H, leftPadding=0, rightPadding=0, topPadding=0,
                  bottomPadding=0, id="body")
    compiled = content.compiled_text or TEMPLATE.footer.compiled_pattern.format(
        date=_date_text(generated_on), organization=content.organization)
    doc.addPageTemplates([PageTemplate(id="report", frames=[frame], onPage=partial(
        draw_footer, title=FOOTER_TITLE, compiled=compiled, confidentiality=CONFIDENTIALITY, logo=logo_path()))])
    doc.build(story)
    return buffer.getvalue()


def page_count(pdf_bytes: bytes) -> int:
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        return document.page_count


def render_report(content: ReportContent, *, generated_on: date | None = None) -> ReportResult:
    """Render to one page, walking the degradation ladder as needed."""
    register_fonts()
    generated_on = generated_on or content.prepared
    for index, cfg in enumerate(LADDER):
        styles = make_styles(cfg.layout())
        if story_height(build_story(content, cfg, styles)) > FRAME_H:
            continue
        pdf = _render(build_story(content, cfg, styles), content, generated_on)
        pages = page_count(pdf)
        if pages == 1:
            return ReportResult(pdf_bytes=pdf, rung=index, reductions=cfg.describe_drops(BASE, content),
                                page_count=pages, config=cfg)
    raise ReportOverflowError(
        "The report could not be fitted to one page even at the minimum readable type size. "
        "Reduce the number of matches or shorten the company notes and try again."
    )


def write_report(content: ReportContent, path: Path, *, generated_on: date | None = None) -> ReportResult:
    result = render_report(content, generated_on=generated_on)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result.pdf_bytes)
    return result
