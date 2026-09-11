"""The Investor Matching Report template: the single source of truth for the report's structure.

``templates/investor_matching_report.template.json`` defines the page geometry, typography,
section order and headings, every field slot (label, placeholder, record binding, format,
length limit, empty text), the match-table columns, the footer, the controlled
vocabularies, and the fields analyzed for every report. It holds no company-, investor- or
deal-specific content.

The PDF generator reads its structure from here, and ``render_template_pdf`` renders the
template itself through the ordinary renderer, so the template can never describe a layout
the application does not emit.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.errors import ConfigError

if TYPE_CHECKING:
    from app.reporting.pdf_generator import ReportContent, ReportResult

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "investor_matching_report.template.json"

#: Sections the renderer has builders for, in the default order.
REQUIRED_SECTIONS: tuple[str, ...] = ("header", "company_snapshot", "ideal_investor_profile",
                                      "top_investor_matches", "recommended_next_action", "data_quality")
#: Snapshot slots the renderer knows how to bind.
SNAPSHOT_KEYS: frozenset[str] = frozenset({"company", "sector", "stage", "geography", "business_model",
                                           "traction", "round", "valuation", "capital_raised", "strategic_need"})
#: Match-table columns the renderer knows how to fill.
MATCH_COLUMN_KEYS: frozenset[str] = frozenset({"investor", "fit", "confidence", "why", "concern", "angle"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")


class TemplateField(_Model):
    key: str
    label: str | None = None
    type: str = "text"
    placeholder: str | None = None
    value: str | None = None
    source: str | list[str] | None = None
    format: str | None = None
    max_chars: int | None = None
    max_items: int | None = None
    empty_text: str | None = None
    required: bool = False


class TemplateColumn(_Model):
    key: str
    header: str
    width_fraction: float = Field(gt=0, lt=1)
    placeholder: str
    secondary_placeholder: str | None = None
    source: str | list[str] | None = None
    secondary_source: str | list[str] | None = None
    max_chars: int | None = None
    #: Length Claude aims for, set below the ladder's trimmed limit so text is never cut.
    target_chars: int | None = None
    empty_text: str | None = None


class TemplateSection(_Model):
    id: str
    order: int
    heading: str | None = None
    component: str
    source: str | None = None
    placeholder: str | None = None
    fields: list[TemplateField] = Field(default_factory=list)
    columns: list[TemplateColumn] = Field(default_factory=list)
    min_items: int | None = None
    max_items: int | None = None
    max_chars: int | None = None
    empty_text: str | None = None
    caption: str | None = None

    def field(self, key: str) -> TemplateField:
        for item in self.fields:
            if item.key == key:
                return item
        raise ConfigError(f"Report template section '{self.id}' has no field '{key}'.")

    def column(self, key: str) -> TemplateColumn:
        for item in self.columns:
            if item.key == key:
                return item
        raise ConfigError(f"Report template has no match-table column '{key}'.")


class Margins(_Model):
    left: float
    right: float
    top: float
    bottom: float


class Page(_Model):
    size: str
    orientation: str
    width_pt: float
    height_pt: float
    pages: int = 1
    margins_pt: Margins


class Typography(_Model):
    family: str
    fallback: str
    title_pt: float
    section_heading_pt: float
    body_pt: float
    body_floor_pt: float
    table_pt: float
    table_floor_pt: float
    footer_pt: float


class Footer(_Model):
    title_text: str
    compiled_pattern: str
    compiled_placeholder: str
    confidentiality: str
    font_pt: float
    logo: dict[str, Any] = Field(default_factory=dict)


class ReportTemplate(_Model):
    template_id: str
    template_version: str
    description: str
    document_title: str
    page: Page
    typography: Typography
    rules: list[str]
    placeholder_syntax: dict[str, Any]
    provenance_markers: dict[str, str]
    sections: list[TemplateSection]
    footer: Footer
    vocabularies: dict[str, list[str]]
    analysis_fields: dict[str, Any]

    @model_validator(mode="after")
    def _consistent(self) -> ReportTemplate:
        ids = [s.id for s in self.sections]
        if sorted(ids) != sorted(REQUIRED_SECTIONS):
            raise ConfigError(f"Report template sections must be exactly {', '.join(REQUIRED_SECTIONS)}; got {', '.join(ids)}.")
        orders = [s.order for s in self.sections]
        if len(set(orders)) != len(orders):
            raise ConfigError("Report template section 'order' values must be unique.")
        snapshot = {f.key for f in self.section("company_snapshot").fields}
        if snapshot != SNAPSHOT_KEYS:
            raise ConfigError(f"Company Snapshot fields must be {', '.join(sorted(SNAPSHOT_KEYS))}.")
        matches = self.section("top_investor_matches")
        if {c.key for c in matches.columns} != MATCH_COLUMN_KEYS or len(matches.columns) != len(MATCH_COLUMN_KEYS):
            raise ConfigError(f"Match-table columns must be {', '.join(sorted(MATCH_COLUMN_KEYS))}, each once.")
        width = sum(c.width_fraction for c in matches.columns)
        if abs(width - 1.0) > 0.002:
            raise ConfigError(f"Match-table column widths must sum to 1.0 (got {width:.3f}).")
        for key in ("why", "concern", "angle"):
            if not matches.column(key).max_chars:
                raise ConfigError(f"Match-table column '{key}' needs max_chars.")
        for section_id in ("company_snapshot", "ideal_investor_profile", "recommended_next_action", "data_quality"):
            if not self.section(section_id).max_chars:
                raise ConfigError(f"Section '{section_id}' needs max_chars.")
        for section_id in ("ideal_investor_profile", "top_investor_matches", "data_quality"):
            if not self.section(section_id).max_items:
                raise ConfigError(f"Section '{section_id}' needs max_items.")
        if not matches.min_items or matches.min_items > (matches.max_items or 0):
            raise ConfigError("Match table needs min_items <= max_items.")
        t = self.typography
        if t.body_floor_pt > t.body_pt or t.table_floor_pt > t.table_pt:
            raise ConfigError("Type floors cannot exceed the base type sizes.")
        return self

    def section(self, section_id: str) -> TemplateSection:
        for item in self.sections:
            if item.id == section_id:
                return item
        raise ConfigError(f"Report template has no section '{section_id}'.")

    def ordered_sections(self) -> list[TemplateSection]:
        return sorted(self.sections, key=lambda s: s.order)


@lru_cache(maxsize=4)
def load_template(path: str | None = None) -> ReportTemplate:
    """Load and validate the report template (cached)."""
    source = Path(path) if path else TEMPLATE_PATH
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read report template {source.name}: {exc}") from exc
    try:
        return ReportTemplate.model_validate(data)
    except ConfigError:
        raise
    except Exception as exc:  # pydantic ValidationError, with field detail
        raise ConfigError(f"Invalid report template {source.name}: {exc}") from exc


def placeholder_content(template: ReportTemplate | None = None) -> ReportContent:
    """Report content made only of the template's placeholders - no company data at all."""
    from app.reporting.pdf_generator import ReportContent, SnapshotItem
    from app.reporting.report_layout import MatchRow

    t = template or load_template()
    header = t.section("header")
    matches = t.section("top_investor_matches")
    col = matches.column
    tier_slot, _sep, type_slot = (col("investor").secondary_placeholder or "[TIER] · [INVESTOR TYPE]").partition(" · ")
    row = MatchRow(
        name=col("investor").placeholder, subtitle=type_slot or "[INVESTOR TYPE]", tier="template",
        tier_short=tier_slot, archetype=False, fit=col("fit").placeholder,
        adjusted=(col("fit").secondary_placeholder or "adj [ADJ]").removeprefix("adj ").strip(),
        confidence=col("confidence").placeholder, evidence=col("confidence").secondary_placeholder or "[EVIDENCE]",
        why=col("why").placeholder, concern=col("concern").placeholder, angle=col("angle").placeholder,
    )
    return ReportContent(
        company_name="[COMPANY NAME]",
        analysis_id="[ANALYSIS ID]",
        prepared=date.today(),
        snapshot=[SnapshotItem(f.label or f.key, f.placeholder or f"[{f.key.upper()}]", "pitch_deck")
                  for f in t.section("company_snapshot").fields],
        iip_points=[f.placeholder or "" for f in t.section("ideal_investor_profile").fields],
        matches=[row] * (matches.max_items or 8),
        next_action=t.section("recommended_next_action").placeholder or "[RECOMMENDED NEXT ACTION]",
        data_quality=[f.placeholder or "" for f in t.section("data_quality").fields],
        synthetic_data=True,  # shows the conditional warning slot
        meta_text=header.field("meta_line").placeholder,
        compiled_text=t.footer.compiled_placeholder,
    )


def render_template_pdf(path: Path) -> ReportResult:
    """Render the placeholder template through the ordinary renderer and write it to ``path``."""
    from app.reporting.pdf_generator import render_report

    result = render_report(placeholder_content())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result.pdf_bytes)
    return result
