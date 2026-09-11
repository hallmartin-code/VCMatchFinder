"""Upload security checks: type sniffing, size limits, and archive sanity.

The extension is never trusted on its own. A file is accepted only when its magic bytes
agree with an allowed extension, and PPTX archives are inspected for structure and
decompressed size before python-pptx ever opens them.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.errors import UnsupportedFormatError, UploadRejectedError
from app.utils.text import sanitize_filename

DeckFormat = Literal["pdf", "pptx", "ppt"]

PDF_MAGIC = b"%PDF"
ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy Office binary (.ppt)

ALLOWED_EXTENSIONS: dict[str, DeckFormat] = {".pdf": "pdf", ".pptx": "pptx", ".ppt": "ppt"}

#: Decompressed-size ceiling for PPTX archives (zip-bomb guard).
MAX_PPTX_UNCOMPRESSED = 512 * 1024 * 1024
MAX_PPTX_MEMBERS = 10_000

INVESTOR_DATA_EXTENSIONS = {".csv", ".json"}
MAX_INVESTOR_FILE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class ValidatedUpload:
    safe_name: str
    format: DeckFormat
    size_bytes: int


def sniff_format(head: bytes) -> DeckFormat | None:
    """Identify a deck from its first bytes, ignoring the extension."""
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(ZIP_MAGIC):
        return "pptx"
    if head.startswith(OLE_MAGIC):
        return "ppt"
    # Some PDF writers emit a short preamble before the header.
    if b"%PDF" in head[:1024]:
        return "pdf"
    return None


def check_pptx_archive(data: bytes) -> None:
    """Reject ZIPs that are not PowerPoint packages or that decompress implausibly large."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            names = {m.filename for m in members}
    except zipfile.BadZipFile as exc:
        raise UploadRejectedError(
            "The file has a PowerPoint extension but is not a valid PPTX archive "
            f"({exc}). Re-export it from PowerPoint or upload a PDF."
        ) from exc
    if len(members) > MAX_PPTX_MEMBERS:
        raise UploadRejectedError("The PPTX archive contains an implausible number of parts.")
    if sum(m.file_size for m in members) > MAX_PPTX_UNCOMPRESSED:
        raise UploadRejectedError(
            "The PPTX archive decompresses to more than 512 MB and was rejected as unsafe."
        )
    if "ppt/presentation.xml" not in names:
        raise UnsupportedFormatError(
            "The ZIP archive is not a PowerPoint presentation (no ppt/presentation.xml). "
            "Word/Excel files and other archives are not supported."
        )


def validate_upload(filename: str, data: bytes, *, max_bytes: int) -> ValidatedUpload:
    """Validate an uploaded deck; raise an actionable error or return its safe identity."""
    safe_name = sanitize_filename(filename)
    if not data:
        raise UploadRejectedError(f"{safe_name} is empty (0 bytes).")
    if len(data) > max_bytes:
        raise UploadRejectedError(
            f"{safe_name} is {len(data) / 1_048_576:.1f} MB, above the "
            f"{max_bytes / 1_048_576:.0f} MB upload limit. Compress images or split the deck."
        )
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"{safe_name}: unsupported file type '{extension or 'none'}'. "
            "Upload a PDF (.pdf) or PowerPoint (.pptx, .ppt) deck."
        )
    sniffed = sniff_format(data[:1024])
    if sniffed is None:
        raise UnsupportedFormatError(
            f"{safe_name} is not a valid PDF or PowerPoint file (unrecognized contents). "
            "The file may be corrupt; try re-exporting it."
        )
    expected = ALLOWED_EXTENSIONS[extension]
    if sniffed != expected:
        raise UnsupportedFormatError(
            f"{safe_name} has a {extension} extension but its contents are {sniffed.upper()}. "
            "Rename or re-export the file so the extension matches its format."
        )
    if sniffed == "pptx":
        check_pptx_archive(data)
    return ValidatedUpload(safe_name=safe_name, format=sniffed, size_bytes=len(data))


def validate_investor_upload(filename: str, data: bytes) -> str:
    """Validate an uploaded investor dataset; returns its safe name."""
    safe_name = sanitize_filename(filename)
    extension = Path(safe_name).suffix.lower()
    if extension not in INVESTOR_DATA_EXTENSIONS:
        raise UnsupportedFormatError(
            f"{safe_name}: investor datasets must be .csv or .json files."
        )
    if not data:
        raise UploadRejectedError(f"{safe_name} is empty.")
    if len(data) > MAX_INVESTOR_FILE_BYTES:
        raise UploadRejectedError(f"{safe_name} exceeds the 20 MB investor-dataset limit.")
    return safe_name
