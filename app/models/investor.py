"""Investor records as held by an ``InvestorRepository``.

Fields are optional wherever real datasets are incomplete. Missing investor data is
never filled in: a blank check size stays ``None`` and scores as unknown.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.matching.taxonomy import (
    parse_business_models,
    parse_sector_tokens,
    parse_stage,
    parse_strategic_tags,
)
from app.utils.geography import parse_geo_tokens

LeadCapability = Literal["yes", "no", "sometimes", "unknown"]
FundStatus = Literal["active", "fundraising", "between_funds", "inactive", "unknown"]

#: Statuses under which a fund cannot make new investments (hard filter).
NOT_INVESTING_STATUSES: frozenset[str] = frozenset({"between_funds", "inactive"})


class HistoricalInvestment(BaseModel):
    company: str
    sector: str | None = None
    stage: str | None = None
    year: int | None = None
    led: bool | None = None
    amount: float | None = None
    source_url: str | None = None

    def sector_tokens(self) -> set[str]:
        return parse_sector_tokens(self.sector)

    def stage_key(self) -> str | None:
        return parse_stage(self.stage) if self.stage else None


class InvestorRecord(BaseModel):
    investor_id: str
    name: str
    investor_type: str = "unknown"
    website: str | None = None
    headquarters: str | None = None
    investment_geographies: list[str] = Field(default_factory=list)
    excluded_geographies: list[str] = Field(default_factory=list)
    sector_preferences: list[str] = Field(default_factory=list)
    excluded_sectors: list[str] = Field(default_factory=list)
    stage_preferences: list[str] = Field(default_factory=list)
    typical_check: float | None = None
    min_check: float | None = None
    max_check: float | None = None
    lead_capability: LeadCapability = "unknown"
    portfolio_companies: list[str] = Field(default_factory=list)
    historical_investments: list[HistoricalInvestment] = Field(default_factory=list)
    last_known_investment: date | None = None
    strategic_capabilities: list[str] = Field(default_factory=list)
    business_model_preferences: list[str] = Field(default_factory=list)
    excluded_business_models: list[str] = Field(default_factory=list)
    min_revenue: float | None = None
    fund_vintage: int | None = None
    fund_size: float | None = None
    fund_status: FundStatus = "unknown"
    fund_status_note: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    last_verified: date | None = None
    data_source: str = "unspecified"
    is_synthetic: bool = False
    is_archetype: bool = False
    notes: str | None = None

    # ----------------------------------------------------------- normalized views

    def sector_tokens(self) -> set[str]:
        return parse_sector_tokens(self.sector_preferences)

    def excluded_sector_tokens(self) -> set[str]:
        return parse_sector_tokens(self.excluded_sectors)

    def geo_tokens(self) -> set[str]:
        return set().union(*(parse_geo_tokens(g) for g in self.investment_geographies)) \
            if self.investment_geographies else set()

    def excluded_geo_tokens(self) -> set[str]:
        return set().union(*(parse_geo_tokens(g) for g in self.excluded_geographies)) \
            if self.excluded_geographies else set()

    def strategic_tags(self) -> set[str]:
        return parse_strategic_tags(self.strategic_capabilities)

    def business_model_tags(self) -> set[str]:
        return parse_business_models(self.business_model_preferences)

    def excluded_business_model_tags(self) -> set[str]:
        return parse_business_models(self.excluded_business_models)

    def check_range(self) -> tuple[float | None, float | None]:
        """(min, max) check, using the typical check to fill a missing bound."""
        low = self.min_check if self.min_check is not None else self.typical_check
        high = self.max_check if self.max_check is not None else self.typical_check
        if low is not None and high is not None and low > high:
            low, high = high, low
        return low, high

    @property
    def has_verification(self) -> bool:
        return bool(self.source_urls) and self.last_verified is not None
