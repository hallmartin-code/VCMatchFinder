"""Entry point for deck ingestion: validation, dispatch, and extraction-quality warnings.

Uploaded decks are written to a private temporary directory only for the duration of
parsing and deleted immediately afterwards; nothing about the deck is logged.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.errors import IngestError, UnsupportedFormatError
from app.ingestion.models import DeckDocument, DeckPage
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.ppt_parser import parse_legacy_ppt, parse_pptx
from app.utils.text import normalize_text
from app.utils.validation import ALLOWED_EXTENSIONS, sniff_format, validate_upload

DEFAULT_MAX_BYTES = 50 * 1024 * 1024


def parse_deck(path: Path, *, display_name: str | None = None,
               max_bytes: int = DEFAULT_MAX_BYTES) -> DeckDocument:
    """Parse a deck on disk (PDF, PPTX, or legacy PPT)."""
    if not path.is_file():
        raise IngestError(f"No such file: {path.name}")
    data_head = path.open("rb").read(1024)
    name = display_name or path.name
    if path.stat().st_size > max_bytes:
        raise IngestError(f"{name} exceeds the {max_bytes // 1_048_576} MB limit.")
    fmt = sniff_format(data_head)
    extension = path.suffix.lower()
    if fmt is None:
        raise UnsupportedFormatError(
            f"{name} is not a PDF or PowerPoint file. Supported: "
            f"{', '.join(sorted(ALLOWED_EXTENSIONS))}."
        )
    if extension in ALLOWED_EXTENSIONS and ALLOWED_EXTENSIONS[extension] != fmt:
        raise UnsupportedFormatError(
            f"{name} has a {extension} extension but contains {fmt.upper()} data. "
            "Rename or re-export the file."
        )
    if fmt == "pdf":
        doc = parse_pdf(path, display_name=name)
    elif fmt == "pptx":
        doc = parse_pptx(path, display_name=name)
    else:
        doc = parse_legacy_ppt(path, display_name=name)
    add_quality_warnings(doc)
    return doc


def parse_uploaded_deck(filename: str, data: bytes, *, max_bytes: int = DEFAULT_MAX_BYTES) -> DeckDocument:
    """Validate and parse an uploaded deck held in memory."""
    upload = validate_upload(filename, data, max_bytes=max_bytes)
    with tempfile.TemporaryDirectory(prefix="ten-deck-") as tmp:
        temp_path = Path(tmp) / upload.safe_name
        temp_path.write_bytes(data)
        try:
            return parse_deck(temp_path, display_name=upload.safe_name, max_bytes=max_bytes)
        finally:
            temp_path.unlink(missing_ok=True)


def add_quality_warnings(doc: DeckDocument) -> None:
    if doc.is_scanned:
        doc.warnings.append(
            f"Little or no extractable text ({len(doc.low_text_pages)} of {doc.page_count} "
            f"{doc.unit.lower()}s are image-only). The deck appears to be scanned or exported "
            "as images; most fields will need manual entry unless external vision extraction "
            "is enabled."
        )
    elif doc.low_text_pages:
        shown = ", ".join(map(str, doc.low_text_pages[:12]))
        doc.warnings.append(
            f"{len(doc.low_text_pages)} {doc.unit.lower()}(s) have no extractable text "
            f"({shown}); charts or images on them were not read."
        )


def notes_document(text: str) -> DeckDocument | None:
    """Wrap user-supplied additional company information as a citable document."""
    cleaned = normalize_text(text or "")
    if not cleaned:
        return None
    return DeckDocument(
        filename="user_notes",
        source_format="notes",
        pages=[DeckPage(number=1, title="User notes", text=cleaned)],
    )
