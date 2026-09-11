"""Loads investor data for a run from configured files and user uploads.

Failures in one dataset become warnings, not crashes: a run can still proceed with the
remaining data, or with investor archetypes when no named-investor data is available.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings
from app.data.investor_repository import (
    CompositeInvestorRepository,
    CsvInvestorRepository,
    DatasetInfo,
    InvestorRepository,
    JsonInvestorRepository,
)
from app.errors import InvestorDataError, MatchingEngineError
from app.models.investor import InvestorRecord
from app.utils.validation import validate_investor_upload


@dataclass
class InvestorDataset:
    investors: list[InvestorRecord] = field(default_factory=list)
    sources: list[DatasetInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def any_synthetic(self) -> bool:
        return any(i.is_synthetic for i in self.investors)

    @property
    def all_synthetic(self) -> bool:
        return bool(self.investors) and all(i.is_synthetic for i in self.investors)

    @property
    def unverified_count(self) -> int:
        return sum(1 for i in self.investors if not i.has_verification)

    def label(self) -> str:
        if not self.sources:
            return "No investor dataset loaded"
        names = ", ".join(s.name for s in self.sources)
        return f"{names} (SYNTHETIC)" if self.any_synthetic else names

    def summary(self) -> dict[str, Any]:
        return {
            "sources": [s.name for s in self.sources],
            "is_synthetic": self.any_synthetic,
            "record_count": len(self.investors),
            "unverified_records": self.unverified_count,
            "warnings": list(self.warnings),
        }


def repository_for_path(path: Path) -> InvestorRepository:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return JsonInvestorRepository(path)
    if suffix == ".csv":
        return CsvInvestorRepository(path)
    raise InvestorDataError(f"{path.name}: investor datasets must be .json or .csv files.")


def repository_for_upload(filename: str, data: bytes) -> InvestorRepository:
    safe_name = validate_investor_upload(filename, data)
    if safe_name.lower().endswith(".json"):
        return JsonInvestorRepository(data=data, name=safe_name)
    return CsvInvestorRepository(data=data, name=safe_name)


def load_investor_dataset(
    paths: Iterable[Path] = (), uploads: Iterable[tuple[str, bytes]] = (),
) -> InvestorDataset:
    repositories: list[InvestorRepository] = []
    warnings: list[str] = []
    for path in paths:
        if not path.exists():
            warnings.append(f"Investor dataset not found: {path.name}")
            continue
        try:
            repositories.append(repository_for_path(path))
        except MatchingEngineError as exc:
            warnings.append(str(exc))
    for filename, data in uploads:
        try:
            repositories.append(repository_for_upload(filename, data))
        except MatchingEngineError as exc:
            warnings.append(str(exc))

    if not repositories:
        warnings.append(
            "No investor data is available, so results are investor archetypes (categories), "
            "not named investors."
        )
        return InvestorDataset(warnings=warnings)

    composite = CompositeInvestorRepository(repositories)
    investors = composite.list_investors()
    warnings.extend(composite.warnings)
    if any(r.info.is_synthetic for r in repositories):
        warnings.append(
            "Investor data includes a SYNTHETIC test dataset: those firms are fictitious and "
            "must not be used for outreach."
        )
    return InvestorDataset(investors=investors, sources=composite.sources, warnings=warnings)


def load_default_dataset(settings: Settings, uploads: Iterable[tuple[str, bytes]] = (),
                         *, include_defaults: bool = True) -> InvestorDataset:
    paths = settings.investor_paths() if include_defaults else []
    return load_investor_dataset(paths, uploads)
