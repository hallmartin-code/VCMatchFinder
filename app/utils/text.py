"""Small text helpers shared across ingestion, extraction and reporting."""

from __future__ import annotations

import re
import unicodedata

# Horizontal whitespace, including the non-breaking and figure spaces PDF extraction emits.
_WS = re.compile("[ \t    ]+")
_BLANKS = re.compile(r"\n{3,}")
_LIST_SPLIT = re.compile(r"\s*(?:[,;|/\n•]|\band\b)\s*", re.IGNORECASE)
_ENTITY_SUFFIX = re.compile(
    r"\b(?:llc|l\.l\.c\.|lp|l\.p\.|llp|inc|inc\.|ltd|ltd\.|limited|corp|corporation|co|gmbh|plc|sa|ag|bv)\b\.?",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def normalize_text(raw: str) -> str:
    """Collapse horizontal whitespace and runs of blank lines, preserving line structure."""
    lines = [_WS.sub(" ", line).strip() for line in raw.replace("\r\n", "\n").split("\n")]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def truncate(text: str | None, limit: int, *, ellipsis: str = "…") -> str:
    """Shorten to at most ``limit`` characters, breaking on a word boundary when possible."""
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    if limit <= len(ellipsis):
        return text[:limit]
    cut = text[: limit - len(ellipsis)]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:-–—") + ellipsis


def split_list(value: str | list[str] | None) -> list[str]:
    """Split "a, b; c and d" (or an existing list) into clean, de-duplicated items."""
    if value is None:
        return []
    items = value if isinstance(value, list) else _LIST_SPLIT.split(str(value))
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        cleaned = " ".join(str(item).split()).strip(" .-")
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out


def normalize_name(name: str) -> str:
    """Canonical key for an organization name, used for de-duplication and matching.

    "ALDERMOOR Health Ventures, LLC" and "Aldermoor Health Ventures" map to the same key.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    stripped = _ENTITY_SUFFIX.sub(" ", ascii_name.lower())
    return _NON_ALNUM.sub("", stripped)


def sanitize_filename(name: str, *, max_length: int = 100) -> str:
    """Strip directories and unsafe characters from an uploaded filename."""
    base = name.replace("\\", "/").split("/")[-1]
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    base = _UNSAFE_FILENAME.sub("_", base).strip("._")
    if not base:
        base = "upload"
    if len(base) > max_length:
        stem, dot, ext = base.rpartition(".")
        if dot and len(ext) <= 8:
            base = stem[: max_length - len(ext) - 1] + "." + ext
        else:
            base = base[:max_length]
    return base


def slugify(text: str, *, max_length: int = 60) -> str:
    """Filesystem-safe slug for output filenames."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_")
    return (slug or "company")[:max_length]


def first_sentence(text: str, limit: int = 200) -> str:
    """The first sentence of a passage, truncated."""
    match = re.split(r"(?<=[.!?])\s+", " ".join(text.split()), maxsplit=1)
    return truncate(match[0] if match else text, limit)
