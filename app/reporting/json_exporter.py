"""JSON export and validation of the TEN Capital record."""

from __future__ import annotations

from pathlib import Path

from app.models.record import TenCapitalRecord


def record_to_json(record: TenCapitalRecord) -> str:
    """Serialize after a validation round-trip, so what is written always re-parses."""
    text = record.model_dump_json(indent=2)
    TenCapitalRecord.model_validate_json(text)
    return text


def validate_record_json(text: str) -> TenCapitalRecord:
    return TenCapitalRecord.model_validate_json(text)


def export_json(record: TenCapitalRecord, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record_to_json(record), encoding="utf-8")
    return path
