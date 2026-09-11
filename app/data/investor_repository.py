"""Investor data repositories.

``InvestorRepository`` is the only thing the matching engine knows about investor data.
Local JSON/CSV files, user uploads, and future API or CRM integrations all implement it
and normalize rows through ``normalize_investor_row``, so the engine never depends on a
single provider. Nothing here fills a gap: a field missing from the source stays missing.
"""

from __future__ import annotations

import csv
import io
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.errors import InvestorDataError
from app.matching.taxonomy import parse_investor_type, parse_lead_capability, parse_stages
from app.models.investor import HistoricalInvestment, InvestorRecord
from app.utils.logging import get_logger
from app.utils.money import parse_money, parse_money_range
from app.utils.text import normalize_name, slugify

LOGGER = get_logger(__name__)

#: Canonical field -> accepted column/key names (after lower-casing and snake-casing).
_ALIASES: dict[str, tuple[str, ...]] = {
    "investor_id": ("investor_id", "id", "record_id"),
    "name": ("name", "investor", "investor_name", "fund", "fund_name", "firm", "firm_name",
             "organization"),
    "investor_type": ("investor_type", "type", "category", "investor_category"),
    "website": ("website", "url", "web", "site", "homepage"),
    "headquarters": ("headquarters", "hq", "location", "city", "office"),
    "investment_geographies": ("investment_geographies", "geographies", "geography", "geo_focus",
                               "geographic_focus", "geography_focus", "regions", "region_focus"),
    "excluded_geographies": ("excluded_geographies", "geography_exclusions"),
    "sector_preferences": ("sector_preferences", "sectors", "sector_focus", "sector", "industries",
                           "industry_focus", "focus", "thesis"),
    "excluded_sectors": ("excluded_sectors", "sector_exclusions", "does_not_invest_in", "exclusions"),
    "stage_preferences": ("stage_preferences", "stages", "stage_focus", "stage"),
    "check_size": ("check_size", "check_range", "check", "ticket_size"),
    "typical_check": ("typical_check", "average_check", "avg_check"),
    "min_check": ("min_check", "minimum_check", "check_min"),
    "max_check": ("max_check", "maximum_check", "check_max"),
    "lead_capability": ("lead_capability", "leads_rounds", "leads", "lead", "can_lead"),
    "lead_history": ("lead_history", "deal_history", "investment_history"),
    "portfolio_companies": ("portfolio_companies", "portfolio"),
    "historical_investments": ("historical_investments", "investments", "deals"),
    "last_known_investment": ("last_known_investment", "last_investment", "last_investment_date",
                              "last_deal"),
    "strategic_capabilities": ("strategic_capabilities", "capabilities", "value_add",
                               "strategic_value"),
    "business_model_preferences": ("business_model_preferences", "business_models"),
    "excluded_business_models": ("excluded_business_models",),
    "min_revenue": ("min_revenue", "minimum_revenue", "revenue_requirement"),
    "fund_vintage": ("fund_vintage", "vintage"),
    "fund_size": ("fund_size", "aum"),
    "fund_status": ("fund_status",),
    "source_urls": ("source_urls", "sources", "source_url", "source"),
    "last_verified": ("last_verified", "verified", "verified_on", "last_updated"),
    "notes": ("notes", "comments", "note"),
    "is_synthetic": ("is_synthetic", "synthetic"),
}
_LIST_FIELDS = ("investment_geographies", "excluded_geographies", "sector_preferences",
                "excluded_sectors", "portfolio_companies", "strategic_capabilities",
                "business_model_preferences", "excluded_business_models")

_DEAL_RE = re.compile(
    r"(?P<verb>co-?led|led|participated\s+in|invested\s+in|joined)\s+(?:the\s+)?"
    r"(?P<company>[A-Za-z0-9][\w&.'\- ]*?)\s+(?P<stage>pre-?seed|seed|series\s+[a-f])\b"
    r"(?:\s+(?:round\s+)?(?:in\s+)?(?P<year>(?:19|20)\d\d))?",
    re.I,
)
_URL_LIKE = re.compile(r"^(?:https?://)?[\w.-]+\.[a-z]{2,}(?:/\S*)?$", re.I)


@dataclass
class DatasetInfo:
    name: str
    source: str
    is_synthetic: bool = False
    description: str = ""
    record_count: int = 0


# ------------------------------------------------------------------ normalization


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _canonical_row(row: dict[str, Any]) -> dict[str, Any]:
    keyed = {_key(str(k)): v for k, v in row.items() if k is not None}
    out: dict[str, Any] = {}
    for field, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in keyed and not _blank(keyed[alias]):
                out[field] = keyed[alias]
                break
    return out


def _as_list(value: Any) -> list[str]:
    if _blank(value):
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r"[;,|\n]", str(value))
    return [" ".join(str(item).split()) for item in items if str(item).strip()]


def _parse_date(value: Any) -> date | None:
    if _blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    match = re.search(r"\b(?:19|20)\d\d\b", text)
    return date(int(match.group(0)), 1, 1) if match else None


def _parse_int(value: Any) -> int | None:
    if _blank(value):
        return None
    match = re.search(r"\d{4}|\d+", str(value))
    return int(match.group(0)) if match else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "y", "1"}


def parse_fund_status(value: Any) -> tuple[str, str | None]:
    """Map free-text fund status to a canonical status (and keep the original as a note)."""
    if _blank(value):
        return "unknown", None
    text = " ".join(str(value).split())
    low = text.lower()
    if low in {"active", "fundraising", "between_funds", "inactive", "unknown"}:
        return low, None
    if re.search(r"between\s+funds|no\s+dry\s+powder|fully\s+deployed|not\s+(?:currently\s+)?making\s+new|"
                 r"paused|cannot\s+make\s+new|no\s+new\s+investments", low):
        return "between_funds", text
    if re.search(r"inactive|no\s+longer\s+investing|closed\s+to\s+new|winding\s+down|dissolved|defunct", low):
        return "inactive", text
    if re.search(r"active|deploying|investing|evergreen|balance\s+sheet", low):
        return "active", text
    if re.search(r"raising|fundraising", low):
        return "fundraising", text
    return "unknown", text


def deals_from_text(text: str) -> list[HistoricalInvestment]:
    """Parse "Led Cardiosense Series A 2025; co-led Nomad Bio Series B 2024"."""
    deals: list[HistoricalInvestment] = []
    for match in _DEAL_RE.finditer(text or ""):
        verb = match.group("verb").lower()
        deals.append(HistoricalInvestment(
            company=match.group("company").strip(),
            stage=match.group("stage"),
            year=int(match.group("year")) if match.group("year") else None,
            led=verb in {"led", "co-led", "coled"},
        ))
    return deals


def _history_item(item: dict[str, Any]) -> HistoricalInvestment:
    keyed = {_key(str(k)): v for k, v in item.items()}
    led = keyed.get("led", keyed.get("lead"))
    return HistoricalInvestment(
        company=str(keyed.get("company") or keyed.get("name") or "").strip() or "Unnamed",
        sector=keyed.get("sector") or keyed.get("sectors"),
        stage=keyed.get("stage") or keyed.get("round"),
        year=_parse_int(keyed.get("year") or keyed.get("date")),
        led=None if led is None else _truthy(led),
        amount=parse_money(keyed.get("amount")),
        source_url=keyed.get("source_url") or keyed.get("source"),
    )


def normalize_investor_row(
    row: dict[str, Any], *, data_source: str, is_synthetic: bool = False, row_label: str = "",
) -> tuple[InvestorRecord | None, list[str]]:
    """One raw row (any supported column naming) -> InvestorRecord, plus warnings."""
    warnings: list[str] = []
    data = _canonical_row(row)
    name = " ".join(str(data.get("name", "")).split())
    if not name:
        return None, [f"{data_source} {row_label}: skipped - no investor name."]

    low, high = parse_money_range(data.get("check_size")) if "check_size" in data else (None, None)
    history: list[HistoricalInvestment] = []
    raw_history = data.get("historical_investments")
    if isinstance(raw_history, list):
        for item in raw_history:
            try:
                if isinstance(item, dict):
                    history.append(_history_item(item))
                elif isinstance(item, str):
                    history.extend(deals_from_text(item))
            except ValidationError:
                warnings.append(f"{data_source} {row_label} ({name}): one historical investment was malformed and skipped.")
    elif isinstance(raw_history, str):
        history.extend(deals_from_text(raw_history))
    if "lead_history" in data:
        history.extend(deals_from_text(str(data["lead_history"])))

    status, status_note = parse_fund_status(data.get("fund_status"))
    source_urls = [u for u in _as_list(data.get("source_urls")) if _URL_LIKE.match(u)]

    fields: dict[str, Any] = {
        "investor_id": str(data.get("investor_id") or slugify(name).lower()),
        "name": name,
        "investor_type": parse_investor_type(str(data.get("investor_type", ""))),
        "website": str(data["website"]).strip() if "website" in data else None,
        "headquarters": str(data["headquarters"]).strip() if "headquarters" in data else None,
        "stage_preferences": parse_stages(_as_list(data.get("stage_preferences"))),
        "min_check": parse_money(data["min_check"]) if "min_check" in data else low,
        "max_check": parse_money(data["max_check"]) if "max_check" in data else high,
        "typical_check": parse_money(data.get("typical_check")),
        "lead_capability": parse_lead_capability(data.get("lead_capability"))
        if "lead_capability" in data else "unknown",
        "historical_investments": history,
        "last_known_investment": _parse_date(data.get("last_known_investment")),
        "min_revenue": parse_money(data.get("min_revenue")),
        "fund_vintage": _parse_int(data.get("fund_vintage")),
        "fund_size": parse_money(data.get("fund_size")),
        "fund_status": status,
        "fund_status_note": status_note,
        "source_urls": source_urls,
        "last_verified": _parse_date(data.get("last_verified")),
        "data_source": data_source,
        "is_synthetic": bool(is_synthetic) or _truthy(data.get("is_synthetic", False)),
        "notes": str(data["notes"]).strip() if "notes" in data else None,
    }
    for list_field in _LIST_FIELDS:
        fields[list_field] = _as_list(data.get(list_field))
    try:
        return InvestorRecord(**fields), warnings
    except ValidationError as exc:
        first = exc.errors()[0]
        return None, [*warnings, f"{data_source} {row_label} ({name}): skipped - invalid "
                      f"{'.'.join(map(str, first['loc']))}: {first['msg']}."]


# ------------------------------------------------------------------ de-duplication


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = re.sub(r"^https?://", "", url.strip().lower()).split("/")[0]
    host = host.removeprefix("www.")
    return host or None


def _completeness(record: InvestorRecord) -> int:
    dumped = record.model_dump()
    return sum(1 for v in dumped.values() if not _blank(v) and v != "unknown")


def _merge_group(group: list[InvestorRecord]) -> InvestorRecord:
    group = sorted(group, key=_completeness, reverse=True)
    base = group[0].model_dump()
    for other in group[1:]:
        for key, value in other.model_dump().items():
            current = base.get(key)
            if key == "historical_investments":
                seen = {(d["company"].lower(), str(d.get("stage")), d.get("year")) for d in current}
                for deal in value:
                    marker = (deal["company"].lower(), str(deal.get("stage")), deal.get("year"))
                    if marker not in seen:
                        current.append(deal)
                        seen.add(marker)
            elif isinstance(current, list):
                lowered = {str(v).lower() for v in current}
                current.extend(v for v in value if str(v).lower() not in lowered)
            elif key in {"last_verified", "last_known_investment"}:
                base[key] = max((d for d in (current, value) if d is not None), default=None)
            elif key in {"fund_status", "lead_capability", "investor_type"}:
                if current == "unknown" and value != "unknown":
                    base[key] = value
                    if key == "fund_status":
                        base["fund_status_note"] = other.fund_status_note
            elif key == "is_synthetic":
                base[key] = bool(current or value)
            elif key in {"notes", "data_source"}:
                parts = [p for p in (current, value) if p]
                base[key] = " | ".join(dict.fromkeys(parts)) or None
            elif _blank(current) and not _blank(value):
                base[key] = value
    return InvestorRecord.model_validate(base)


def deduplicate_investors(records: Iterable[InvestorRecord]) -> tuple[list[InvestorRecord], list[str]]:
    """Merge records sharing a normalized name or website domain."""
    groups: list[list[InvestorRecord]] = []
    index_by_key: dict[str, int] = {}
    for record in records:
        keys = [f"name:{normalize_name(record.name)}"]
        domain = _domain(record.website)
        if domain:
            keys.append(f"domain:{domain}")
        index = next((index_by_key[k] for k in keys if k in index_by_key), None)
        if index is None:
            index = len(groups)
            groups.append([])
        groups[index].append(record)
        for k in keys:
            index_by_key.setdefault(k, index)

    merged: list[InvestorRecord] = []
    warnings: list[str] = []
    for group in groups:
        record = group[0] if len(group) == 1 else _merge_group(group)
        if len(group) > 1:
            warnings.append(f"Merged {len(group)} duplicate records for {record.name}.")
        merged.append(record)

    seen_ids: dict[str, int] = {}
    for record in merged:
        count = seen_ids.get(record.investor_id, 0)
        seen_ids[record.investor_id] = count + 1
        if count:
            record.investor_id = f"{record.investor_id}-{count + 1}"
    return merged, warnings


# ------------------------------------------------------------------- repositories


class InvestorRepository(ABC):
    """Interface every investor data source implements."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    @abstractmethod
    def list_investors(self) -> list[InvestorRecord]:
        """All investors from this source, normalized."""

    @property
    @abstractmethod
    def info(self) -> DatasetInfo:
        """Where the data came from and whether it is synthetic."""

    def get(self, investor_id: str) -> InvestorRecord | None:
        return next((i for i in self.list_investors() if i.investor_id == investor_id), None)


class InMemoryInvestorRepository(InvestorRepository):
    def __init__(self, investors: Iterable[InvestorRecord], *, name: str = "in-memory",
                 is_synthetic: bool = False) -> None:
        super().__init__()
        self._investors = list(investors)
        self._info = DatasetInfo(name=name, source="memory", is_synthetic=is_synthetic,
                                 record_count=len(self._investors))

    def list_investors(self) -> list[InvestorRecord]:
        return list(self._investors)

    @property
    def info(self) -> DatasetInfo:
        return self._info


class _FileRepository(InvestorRepository):
    """Shared loading for file-backed datasets (from disk or an upload)."""

    def __init__(self, path: Path | None = None, *, data: bytes | None = None,
                 name: str | None = None) -> None:
        super().__init__()
        if data is None:
            if path is None:
                raise InvestorDataError("An investor dataset needs a file path or file contents.")
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise InvestorDataError(f"Could not read investor dataset {path.name}: {exc}") from exc
        self._file_name = name or (path.name if path else "upload")
        self._source = str(path) if path else f"upload:{self._file_name}"
        self._dataset_name = self._file_name
        self._synthetic = False
        self._description = ""
        self._records = self._load(data)
        LOGGER.info("Loaded %d investor records from %s", len(self._records), self._file_name)

    @abstractmethod
    def _load(self, data: bytes) -> list[InvestorRecord]: ...

    def _normalize_rows(self, rows: Iterable[dict[str, Any]]) -> list[InvestorRecord]:
        records: list[InvestorRecord] = []
        for number, row in enumerate(rows, start=1):
            record, row_warnings = normalize_investor_row(
                row, data_source=self._dataset_name, is_synthetic=self._synthetic,
                row_label=f"row {number}",
            )
            self.warnings.extend(row_warnings)
            if record is not None:
                records.append(record)
        if not records:
            raise InvestorDataError(f"{self._file_name} contains no usable investor records.")
        return records

    def list_investors(self) -> list[InvestorRecord]:
        return list(self._records)

    @property
    def info(self) -> DatasetInfo:
        return DatasetInfo(name=self._dataset_name, source=self._source,
                           is_synthetic=self._synthetic, description=self._description,
                           record_count=len(self._records))


class JsonInvestorRepository(_FileRepository):
    """A JSON list of investors, or ``{"dataset": {...}, "investors": [...]}``."""

    def _load(self, data: bytes) -> list[InvestorRecord]:
        try:
            payload = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvestorDataError(f"{self._file_name} is not valid JSON: {exc}") from exc
        meta: dict[str, Any] = {}
        if isinstance(payload, dict):
            meta = payload.get("dataset") or {}
            rows = payload.get("investors")
        else:
            rows = payload
        if not isinstance(rows, list):
            raise InvestorDataError(
                f"{self._file_name}: expected a list of investors or an object with an "
                "'investors' list."
            )
        self._synthetic = bool(meta.get("is_synthetic", False))
        self._description = str(meta.get("description", ""))
        self._dataset_name = str(meta.get("name") or self._file_name)
        dict_rows = [r for r in rows if isinstance(r, dict)]
        if len(dict_rows) != len(rows):
            self.warnings.append(f"{self._file_name}: {len(rows) - len(dict_rows)} non-object entries ignored.")
        return self._normalize_rows(dict_rows)


class CsvInvestorRepository(_FileRepository):
    """A CSV with one investor per row; column names are matched flexibly."""

    def _load(self, data: bytes) -> list[InvestorRecord]:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        try:
            reader = csv.DictReader(io.StringIO(text))
            fieldnames = reader.fieldnames or []
            rows = list(reader)
        except csv.Error as exc:
            raise InvestorDataError(f"{self._file_name} is not a readable CSV: {exc}") from exc
        if not {_key(f) for f in fieldnames} & set(_ALIASES["name"]):
            raise InvestorDataError(
                f"{self._file_name} has no investor name column (expected one of: Investor, "
                "Name, Fund, Firm)."
            )
        return self._normalize_rows(rows)


class RemoteInvestorRepository(InvestorRepository, ABC):
    """Base class for API and CRM integrations.

    A subclass implements ``fetch_rows`` (paging, auth, rate limits) and returns raw
    dictionaries; normalization, validation and de-duplication are shared with the file
    repositories. Keep credentials in environment variables, never in the subclass.
    """

    def __init__(self, name: str, *, is_synthetic: bool = False) -> None:
        super().__init__()
        self._name = name
        self._synthetic = is_synthetic
        self._cache: list[InvestorRecord] | None = None

    @abstractmethod
    def fetch_rows(self) -> Iterator[dict[str, Any]]:
        """Yield raw investor rows from the remote system."""

    def list_investors(self) -> list[InvestorRecord]:
        if self._cache is None:
            records: list[InvestorRecord] = []
            try:
                for number, row in enumerate(self.fetch_rows(), start=1):
                    record, row_warnings = normalize_investor_row(
                        row, data_source=self._name, is_synthetic=self._synthetic,
                        row_label=f"record {number}",
                    )
                    self.warnings.extend(row_warnings)
                    if record is not None:
                        records.append(record)
            except InvestorDataError:
                raise
            except Exception as exc:  # noqa: BLE001 - provider errors vary; surface actionably
                raise InvestorDataError(f"Investor source {self._name} failed: {exc}") from exc
            self._cache = records
        return list(self._cache)

    @property
    def info(self) -> DatasetInfo:
        return DatasetInfo(name=self._name, source=f"remote:{self._name}",
                           is_synthetic=self._synthetic, record_count=len(self._cache or []))


class CompositeInvestorRepository(InvestorRepository):
    """Several sources merged and de-duplicated."""

    def __init__(self, repositories: Iterable[InvestorRepository]) -> None:
        super().__init__()
        self._repositories = list(repositories)
        self._merged: list[InvestorRecord] | None = None

    def list_investors(self) -> list[InvestorRecord]:
        if self._merged is None:
            combined: list[InvestorRecord] = []
            for repo in self._repositories:
                combined.extend(repo.list_investors())
                self.warnings.extend(repo.warnings)
            self._merged, dedupe_warnings = deduplicate_investors(combined)
            self.warnings.extend(dedupe_warnings)
        return list(self._merged)

    @property
    def info(self) -> DatasetInfo:
        infos = [r.info for r in self._repositories]
        return DatasetInfo(
            name=" + ".join(i.name for i in infos) or "none",
            source="composite",
            is_synthetic=any(i.is_synthetic for i in infos),
            record_count=len(self._merged or []),
        )

    @property
    def sources(self) -> list[DatasetInfo]:
        return [r.info for r in self._repositories]
