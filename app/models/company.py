"""The normalized company record and the user's investor preferences.

Every company field is a ``Sourced`` value, so the engine always knows whether a number
came from the deck (and which slide), from the user, or from its own inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.matching.taxonomy import (
    BUSINESS_MODELS,
    PRODUCT_STAGES,
    REVENUE_STAGES,
    STAGES,
    STRATEGIC_TAGS,
    SUBSECTORS,
    business_model_label,
    family_label,
    family_of,
    parse_business_models,
    parse_stage,
    parse_strategic_tags,
    revenue_stage_for,
    stage_label,
    strategic_label,
    subsector_label,
)
from app.utils.geography import GeoLocation, describe_location, resolve_location
from app.utils.money import format_money, parse_money, parse_percent
from app.utils.provenance import Sourced, user_supplied
from app.utils.text import split_list

FieldKind = Literal["text", "money", "percent", "int", "stage", "subsector", "list",
                    "business_model", "strategic", "product_stage", "revenue_stage", "bool"]


@dataclass(frozen=True)
class FieldSpec:
    label: str
    kind: FieldKind
    #: How much a gap in this field can move the ranking (0-3). Drives the data-gap list.
    ranking_impact: int = 0
    #: Shown in the review step as a field the user should fill if missing.
    reviewable: bool = True


#: Field name -> spec. Order is the order of the review form.
FIELD_SPECS: dict[str, FieldSpec] = {
    "name": FieldSpec("Company name", "text", 1),
    "website": FieldSpec("Website", "text", 0),
    "headquarters": FieldSpec("Headquarters", "text", 2),
    "sector": FieldSpec("Sector", "text", 0, reviewable=False),
    "subsector": FieldSpec("Subsector", "subsector", 3),
    "business_model": FieldSpec("Business model", "business_model", 2),
    "business_model_description": FieldSpec("Business model (as described)", "text", 0),
    "company_stage": FieldSpec("Company stage", "stage", 3),
    "product_stage": FieldSpec("Product stage", "product_stage", 1),
    "revenue_stage": FieldSpec("Revenue stage", "revenue_stage", 2),
    "revenue": FieldSpec("Revenue (annual, USD)", "money", 2),
    "revenue_growth": FieldSpec("Revenue growth (%)", "percent", 1),
    "customers": FieldSpec("Customers / pilots", "text", 1),
    "customer_count": FieldSpec("Customer / site count", "int", 0),
    "traction": FieldSpec("Traction summary", "text", 1),
    "market_size": FieldSpec("Market size (TAM, USD)", "money", 0),
    "round_type": FieldSpec("Round", "text", 1),
    "raise_amount": FieldSpec("Amount being raised (USD)", "money", 3),
    "amount_raised": FieldSpec("Capital raised to date (USD)", "money", 1),
    "round_committed": FieldSpec("Committed in this round (USD)", "money", 1),
    "valuation": FieldSpec("Valuation (USD)", "money", 1),
    "valuation_type": FieldSpec("Valuation basis (pre/post/cap)", "text", 0),
    "use_of_funds": FieldSpec("Use of funds", "list", 1),
    "strategic_needs": FieldSpec("Strategic needs", "strategic", 1),
    "regulatory_status": FieldSpec("Regulatory status", "text", 1),
    "intellectual_property": FieldSpec("Intellectual property", "text", 0),
    "existing_investors": FieldSpec("Existing investors", "list", 1),
    "lead_investor": FieldSpec("Lead investor (this round)", "text", 2),
    "seeking_lead": FieldSpec("Seeking a lead investor", "bool", 1),
    "competitors": FieldSpec("Named competitors", "list", 1),
    "partnerships": FieldSpec("Partnerships", "text", 0),
}


class CompanyProfile(BaseModel):
    """Normalized internal company record. Created by extraction, edited by the user."""

    name: Sourced[str] = Field(default_factory=Sourced[str])
    website: Sourced[str] = Field(default_factory=Sourced[str])
    headquarters: Sourced[str] = Field(default_factory=Sourced[str])
    sector: Sourced[str] = Field(default_factory=Sourced[str])
    subsector: Sourced[str] = Field(default_factory=Sourced[str])
    business_model: Sourced[list[str]] = Field(default_factory=Sourced[list[str]])
    business_model_description: Sourced[str] = Field(default_factory=Sourced[str])
    company_stage: Sourced[str] = Field(default_factory=Sourced[str])
    product_stage: Sourced[str] = Field(default_factory=Sourced[str])
    revenue_stage: Sourced[str] = Field(default_factory=Sourced[str])
    revenue: Sourced[float] = Field(default_factory=Sourced[float])
    revenue_growth: Sourced[float] = Field(default_factory=Sourced[float])
    customers: Sourced[str] = Field(default_factory=Sourced[str])
    customer_count: Sourced[int] = Field(default_factory=Sourced[int])
    traction: Sourced[str] = Field(default_factory=Sourced[str])
    market_size: Sourced[float] = Field(default_factory=Sourced[float])
    round_type: Sourced[str] = Field(default_factory=Sourced[str])
    raise_amount: Sourced[float] = Field(default_factory=Sourced[float])
    amount_raised: Sourced[float] = Field(default_factory=Sourced[float])
    round_committed: Sourced[float] = Field(default_factory=Sourced[float])
    valuation: Sourced[float] = Field(default_factory=Sourced[float])
    valuation_type: Sourced[str] = Field(default_factory=Sourced[str])
    use_of_funds: Sourced[list[str]] = Field(default_factory=Sourced[list[str]])
    strategic_needs: Sourced[list[str]] = Field(default_factory=Sourced[list[str]])
    regulatory_status: Sourced[str] = Field(default_factory=Sourced[str])
    intellectual_property: Sourced[str] = Field(default_factory=Sourced[str])
    existing_investors: Sourced[list[str]] = Field(default_factory=Sourced[list[str]])
    lead_investor: Sourced[str] = Field(default_factory=Sourced[str])
    seeking_lead: Sourced[bool] = Field(default_factory=Sourced[bool])
    competitors: Sourced[list[str]] = Field(default_factory=Sourced[list[str]])
    partnerships: Sourced[str] = Field(default_factory=Sourced[str])

    # ------------------------------------------------------------------ access

    def field(self, name: str) -> Sourced[Any]:
        return getattr(self, name)

    def fields(self) -> list[tuple[str, Sourced[Any]]]:
        return [(name, getattr(self, name)) for name in FIELD_SPECS]

    def location(self) -> GeoLocation | None:
        return resolve_location(self.headquarters.value)

    @property
    def display_name(self) -> str:
        return self.name.value if self.name.known else "Unnamed company"

    def missing_fields(self, *, min_impact: int = 0) -> list[str]:
        # A deck that says it is seeking a lead has explained why no lead is named.
        explained = {"lead_investor"} if (self.seeking_lead.known and self.seeking_lead.value) else set()
        return [
            name for name, spec in FIELD_SPECS.items()
            if spec.ranking_impact >= min_impact and not self.field(name).known and name not in explained
        ]

    def needs_lead(self, lead_required: bool | None = None) -> bool:
        """Whether the round still needs a lead investor.

        An explicit user preference wins; otherwise a named lead means no, and anything
        else (including "unknown") is treated conservatively as needing one.
        """
        if lead_required is not None:
            return lead_required
        if self.lead_investor.known:
            return False
        return True

    # ----------------------------------------------------------------- display

    def display_value(self, name: str) -> str:
        """Human-readable value for a field ("Not in deck" when missing)."""
        item = self.field(name)
        if not item.known:
            return "Not in deck"
        value = item.value
        kind = FIELD_SPECS[name].kind if name in FIELD_SPECS else "text"
        if kind == "money":
            return format_money(value)
        if kind == "percent":
            return f"{value:g}%"
        if kind == "stage":
            return stage_label(value)
        if kind == "subsector":
            return subsector_label(value)
        if kind == "business_model":
            return business_model_label(value)
        if kind == "strategic":
            return strategic_label(value)
        if kind == "product_stage":
            return PRODUCT_STAGES.get(value, str(value))
        if kind == "revenue_stage":
            return REVENUE_STAGES.get(value, str(value))
        if kind == "bool":
            return "Yes" if value else "No"
        if name == "sector":
            return family_label(value)
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value)

    def geography_label(self) -> str:
        location = self.location()
        if location is None:
            return self.headquarters.value if self.headquarters.known else "Not in deck"
        return describe_location(location)

    # --------------------------------------------------------------- record I/O

    def summary(self) -> dict[str, Any]:
        """Flat company block for the TEN Capital JSON record."""
        return {
            "name": self.name.value or "",
            "sector": family_label(self.sector.value) if self.sector.known else "",
            "subsector": subsector_label(self.subsector.value) if self.subsector.known else "",
            "stage": stage_label(self.company_stage.value) if self.company_stage.known else "",
            "geography": self.geography_label() if self.headquarters.known else "",
            "business_model": self.display_value("business_model")
            if self.business_model.known else "",
            "product_stage": self.display_value("product_stage")
            if self.product_stage.known else "",
            "revenue_stage": self.display_value("revenue_stage")
            if self.revenue_stage.known else "",
            "revenue": self.revenue.value,
            "revenue_growth_pct": self.revenue_growth.value,
            "traction": self.traction.value or "",
            "market_size": self.market_size.value,
            "round_type": self.round_type.value or "",
            "raise_amount": self.raise_amount.value,
            "amount_raised": self.amount_raised.value,
            "round_committed": self.round_committed.value,
            "valuation": self.valuation.value,
            "valuation_type": self.valuation_type.value or "",
            "use_of_funds": list(self.use_of_funds.value or []),
            "strategic_needs": [STRATEGIC_TAGS.get(t, t) for t in (self.strategic_needs.value or [])],
            "regulatory_status": self.regulatory_status.value or "",
            "intellectual_property": self.intellectual_property.value or "",
            "existing_investors": list(self.existing_investors.value or []),
            "lead_investor": self.lead_investor.value or "",
            "competitors": list(self.competitors.value or []),
        }

    def provenance(self) -> dict[str, dict[str, Any]]:
        """Per-field provenance for the JSON record."""
        out: dict[str, dict[str, Any]] = {}
        for name, item in self.fields():
            out[name] = {
                "value": item.value,
                "source": item.source if item.known else "missing",
                "location": item.location,
                "confidence": round(item.confidence, 2),
                "method": item.method,
                "note": item.note,
            }
        return out

    def apply_overrides(self, overrides: dict[str, Any]) -> CompanyProfile:
        """Return a copy with user corrections applied (source becomes ``user_supplied``).

        Blank values clear a field only when the user explicitly passes ``None``; an empty
        string is treated as "leave unchanged" so an untouched form never erases data.
        """
        updated = self.model_copy(deep=True)
        for name, raw in overrides.items():
            if name not in FIELD_SPECS:
                continue
            if isinstance(raw, str) and not raw.strip():
                continue
            if raw is None:
                setattr(updated, name, Sourced(value=None, source="missing",
                                               note="Cleared by user"))
                continue
            value = coerce_field_value(name, raw)
            if value is None:
                continue
            setattr(updated, name, user_supplied(value))
        return reconcile_derived_fields(updated)


def coerce_field_value(name: str, raw: Any) -> Any:
    """Parse a user-entered value into the field's canonical type (``None`` if invalid)."""
    kind = FIELD_SPECS[name].kind
    if kind == "money":
        return parse_money(raw)
    if kind == "percent":
        if isinstance(raw, (int, float)):
            return float(raw)
        return parse_percent(str(raw)) if "%" in str(raw) else _float_or_none(raw)
    if kind == "int":
        number = _float_or_none(raw)
        return int(number) if number is not None else None
    if kind == "stage":
        text = str(raw).strip().lower()
        return text if text in STAGES else parse_stage(text)
    if kind == "subsector":
        text = str(raw).strip().lower().replace(" ", "_")
        if text in SUBSECTORS:
            return text
        for key, sub in SUBSECTORS.items():
            if str(raw).strip().lower() in {sub.label.lower(), *sub.aliases}:
                return key
        return None
    if kind == "business_model":
        return sorted(parse_business_models(raw if isinstance(raw, list) else split_list(raw)))
    if kind == "strategic":
        return sorted(parse_strategic_tags(raw if isinstance(raw, list) else split_list(raw)))
    if kind == "product_stage":
        text = str(raw).strip().lower()
        for key, label in PRODUCT_STAGES.items():
            if text in {key, label.lower()}:
                return key
        return None
    if kind == "revenue_stage":
        text = str(raw).strip().lower()
        for key, label in REVENUE_STAGES.items():
            if text in {key, label.lower()}:
                return key
        return None
    if kind == "list":
        return split_list(raw) if not isinstance(raw, list) else [str(v) for v in raw if str(v).strip()]
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        return True if text in {"yes", "true", "y", "1"} else False if text in {"no", "false", "n", "0"} else None
    return str(raw).strip()


def _float_or_none(raw: Any) -> float | None:
    try:
        return float(str(raw).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def reconcile_derived_fields(profile: CompanyProfile) -> CompanyProfile:
    """Keep dependent fields consistent after edits (sector follows subsector, etc.)."""
    if profile.subsector.known:
        family = family_of(profile.subsector.value)
        if family and (not profile.sector.known or profile.sector.value != family):
            profile.sector = Sourced(
                value=family,
                source=profile.subsector.source if profile.subsector.source != "pitch_deck"
                else "inference",
                location=profile.subsector.location,
                confidence=profile.subsector.confidence,
                method="taxonomy",
                note="Sector family implied by subsector",
            )
    if profile.revenue.known and profile.revenue.source == "user_supplied":
        stage = revenue_stage_for(profile.revenue.value)
        if stage and profile.revenue_stage.source != "user_supplied":
            profile.revenue_stage = Sourced(
                value=stage, source="inference", confidence=0.9, method="derived",
                note="Derived from user-supplied revenue",
            )
    return profile


class InvestorPreferences(BaseModel):
    """User-supplied investor targeting preferences."""

    preferred_types: list[str] = Field(default_factory=list)
    preferred_geographies: list[str] = Field(default_factory=list)
    excluded_types: list[str] = Field(default_factory=list)
    excluded_investors: list[str] = Field(default_factory=list)
    lead_required: bool | None = None
    notes: str | None = None


# Re-exported for callers that build dropdowns.
BUSINESS_MODEL_CHOICES = BUSINESS_MODELS
