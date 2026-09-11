"""The format-neutral deck model every ingestion path produces."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

SourceFormat = Literal["pdf", "pptx", "notes"]

#: Below this many characters a page is treated as having no usable text (image-only).
LOW_TEXT_CHARS = 25
#: Share of low-text pages above which the whole deck is treated as scanned.
SCANNED_SHARE = 0.6


class DeckPage(BaseModel):
    number: int = Field(ge=1)
    title: str | None = None
    text: str = ""
    notes: str | None = None
    tables: list[str] = Field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.text) + sum(len(t) for t in self.tables)

    @property
    def low_text(self) -> bool:
        return self.char_count < LOW_TEXT_CHARS


@dataclass(frozen=True)
class DeckLine:
    """One line of deck text with the location it should be cited as."""

    page: int
    location: str
    text: str
    next_text: str = ""


class DeckDocument(BaseModel):
    filename: str
    source_format: SourceFormat
    pages: list[DeckPage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def unit(self) -> str:
        return "Slide" if self.source_format == "pptx" else "Page"

    def location(self, page: int, part: str | None = None) -> str:
        if self.source_format == "notes":
            return "User notes"
        base = f"{self.unit} {page}"
        return f"{base} ({part})" if part else base

    @property
    def low_text_pages(self) -> list[int]:
        return [p.number for p in self.pages if p.low_text]

    @property
    def is_scanned(self) -> bool:
        if not self.pages:
            return False
        return len(self.low_text_pages) / len(self.pages) >= SCANNED_SHARE

    @property
    def total_chars(self) -> int:
        return sum(p.char_count + len(p.notes or "") for p in self.pages)

    def iter_lines(self, *, include_notes: bool = True) -> Iterator[DeckLine]:
        """Every non-empty line with its citation, plus the following line for context."""
        for page in self.pages:
            blocks: list[tuple[str | None, str]] = [(None, page.text)]
            blocks.extend(("table", row) for row in page.tables)
            if include_notes and page.notes:
                blocks.append(("speaker notes", page.notes))
            for part, block in blocks:
                lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
                for i, line in enumerate(lines):
                    yield DeckLine(
                        page=page.number,
                        location=self.location(page.number, part),
                        text=line,
                        next_text=lines[i + 1] if i + 1 < len(lines) else "",
                    )

    def full_text(self) -> str:
        parts = []
        for page in self.pages:
            parts.append(page.text)
            parts.extend(page.tables)
            if page.notes:
                parts.append(page.notes)
        return "\n".join(parts)

    def page_text(self, number: int) -> str:
        page = next((p for p in self.pages if p.number == number), None)
        if page is None:
            return ""
        return "\n".join([page.text, *page.tables, page.notes or ""])
