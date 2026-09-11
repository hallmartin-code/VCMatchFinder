"""PDF/PPTX ingestion, upload validation and failure handling."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.errors import IngestError, UnsupportedFormatError, UploadRejectedError
from app.ingestion import ppt_parser
from app.ingestion.deck_parser import notes_document, parse_deck, parse_uploaded_deck
from app.utils.text import sanitize_filename
from app.utils.validation import validate_upload

MB = 1024 * 1024


def test_pdf_extracts_text_page_by_page(samples):
    doc = parse_deck(samples["pdf"])
    assert doc.source_format == "pdf"
    assert doc.page_count == 12
    assert "We are raising a $6M Series A" in doc.page_text(12)
    assert doc.location(12) == "Page 12"
    assert not doc.is_scanned


def test_pptx_reads_slides_notes_tables_and_charts(samples):
    doc = parse_deck(samples["pptx"])
    assert doc.source_format == "pptx"
    assert doc.page_count == 13
    assert "Pilot sites went live" in (doc.pages[4].notes or "")
    assert any("Raise | $6M" in row for row in doc.pages[12].tables)
    assert any(row.startswith("Chart: Quarterly revenue") for row in doc.pages[4].tables)
    locations = {line.location for line in doc.iter_lines()}
    assert "Slide 5 (speaker notes)" in locations


def test_scanned_pdf_is_flagged_with_actionable_warning(samples):
    doc = parse_deck(samples["scanned"])
    assert doc.is_scanned
    assert any("scanned" in w and "manual entry" in w for w in doc.warnings)


def test_corrupt_pdf_raises_ingest_error(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"%PDF-1.7\n this is not really a pdf \x00\x01")
    with pytest.raises(IngestError):
        parse_deck(bad)


def test_extension_mismatch_is_rejected(samples):
    with pytest.raises(UnsupportedFormatError, match="extension"):
        validate_upload("deck.pptx", samples["pdf"].read_bytes(), max_bytes=50 * MB)


def test_unsupported_extension_is_rejected():
    with pytest.raises(UnsupportedFormatError, match="unsupported file type"):
        validate_upload("deck.docx", b"PK\x03\x04rest", max_bytes=50 * MB)


def test_zip_that_is_not_a_presentation_is_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<xml/>")
    with pytest.raises(UnsupportedFormatError, match="not a PowerPoint"):
        validate_upload("deck.pptx", buffer.getvalue(), max_bytes=50 * MB)


def test_upload_size_limit_and_empty_upload():
    with pytest.raises(UploadRejectedError, match="upload limit"):
        validate_upload("deck.pdf", b"%PDF" + b"0" * (2 * MB), max_bytes=1 * MB)
    with pytest.raises(UploadRejectedError, match="empty"):
        validate_upload("deck.pdf", b"", max_bytes=1 * MB)


def test_legacy_ppt_without_libreoffice_gives_actionable_error(monkeypatch):
    monkeypatch.setattr(ppt_parser, "find_soffice", lambda: None)
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
    with pytest.raises(UnsupportedFormatError, match=r"Save As"):
        parse_uploaded_deck("old_deck.ppt", ole)


def test_uploaded_deck_parses_with_sanitized_name(samples):
    doc = parse_uploaded_deck("../../secret folder/Lumora Deck.pdf", samples["pdf"].read_bytes())
    assert doc.filename == "Lumora_Deck.pdf"
    assert doc.page_count == 12


def test_filename_sanitization():
    assert sanitize_filename("../../etc/pass wd.pdf") == "pass_wd.pdf"
    assert sanitize_filename("C:\\Users\\x\\deck (final).pptx") == "deck_final_.pptx"
    assert sanitize_filename("") == "upload"


def test_notes_document_is_citable():
    doc = notes_document("We are raising $8M.")
    assert doc is not None and doc.location(1) == "User notes"
    assert notes_document("   ") is None
