"""Money and percentage parsing/formatting.

Deck text says "$4M", datasets say "4,000,000", notes say "USD 750k". All of it must land
on one number, and anything that cannot be read stays ``None`` rather than becoming a guess.
"""

from __future__ import annotations

import re

_MULTIPLIERS = {"k": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "b": 1e9, "bn": 1e9}
_WORD_MULTIPLIERS = {"thousand": 1e3, "million": 1e6, "billion": 1e9}

_AMOUNT_RE = re.compile(
    r"""(?P<currency>US\$|USD|\$|€|EUR|£|GBP)?\s*
        (?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
        (?:\s*(?P<suffix>mm|mn|bn|[kmb])\b)?
        (?:\s*(?P<word>thousand|million|billion))?
    """,
    re.IGNORECASE | re.VERBOSE,
)
_RANGE_SPLIT = re.compile(r"\s*(?:--|-|–|—|\bto\b|\bthrough\b)\s*", re.IGNORECASE)
_SUFFIX_ONLY = re.compile(r"(mm|mn|bn|[kmb])\b", re.IGNORECASE)
_MAGNITUDE_HINT = re.compile(r"(mm|mn|bn|[kmb])\b|thousand|million|billion|,", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(?P<number>-?\d+(?:\.\d+)?)\s*%")


def _is_year(number: float, raw_number: str) -> bool:
    return "." not in raw_number and "," not in raw_number and 1900 <= number <= 2100


def parse_money(text: str | float | int | None, *, strict: bool = False) -> float | None:
    """Parse the first monetary amount in ``text``; ``None`` when unreadable.

    ``strict`` (used for deck text) requires a currency marker or magnitude word/suffix,
    so "Series 3", "20 hospitals" or "2026" are never read as money. Non-strict mode
    (used for structured dataset cells) also accepts bare numbers >= 1,000.
    """
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return float(text) if text >= 0 else None
    raw = str(text).strip()
    if not raw:
        return None

    for match in _AMOUNT_RE.finditer(raw):
        raw_number = match.group("number")
        number = float(raw_number.replace(",", ""))
        suffix = (match.group("suffix") or "").lower()
        word = (match.group("word") or "").lower()
        currency = match.group("currency")
        if suffix:
            return number * _MULTIPLIERS[suffix]
        if word:
            return number * _WORD_MULTIPLIERS[word]
        if currency:
            return number
        if strict or _is_year(number, raw_number) or number < 1000:
            continue
        return number
    return None


def parse_money_range(text: str | float | int | None) -> tuple[float | None, float | None]:
    """Parse "$1M-$3M", "$1-3M", "500k to 2M" into (min, max).

    A bare low side inherits the high side's suffix: "$1-3M" means $1M-$3M.
    """
    if text is None or isinstance(text, bool):
        return (None, None)
    if isinstance(text, (int, float)):
        return (float(text), float(text))
    raw = str(text).strip()
    if not raw:
        return (None, None)
    parts = _RANGE_SPLIT.split(raw, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        low = parse_money(parts[0])
        high = parse_money(parts[1])
        high_suffix = _SUFFIX_ONLY.search(parts[1])
        if high is not None and high_suffix and not _MAGNITUDE_HINT.search(parts[0]):
            bare = re.search(r"(\d+(?:\.\d+)?)", parts[0])
            if bare:
                low = float(bare.group(1)) * _MULTIPLIERS[high_suffix.group(1).lower()]
        if low is not None and high is not None and low > high:
            low, high = high, low
        return (low, high)
    single = parse_money(raw)
    return (single, single)


def parse_percent(text: str | None) -> float | None:
    """First percentage in the text, as a number (``"140% YoY"`` -> 140.0)."""
    if not text:
        return None
    match = _PERCENT_RE.search(text)
    return float(match.group("number")) if match else None


def format_money(value: float | None, *, none_text: str = "Not disclosed") -> str:
    """Compact display form: $4.2M, $750K, $1.25B."""
    if value is None:
        return none_text
    magnitude = abs(value)
    if magnitude >= 1e9:
        return f"${value / 1e9:.2f}B".replace(".00B", "B")
    if magnitude >= 1e6:
        text = f"${value / 1e6:.1f}M"
        return text.replace(".0M", "M")
    if magnitude >= 1e3:
        return f"${value / 1e3:.0f}K"
    return f"${value:,.0f}"


def format_money_range(low: float | None, high: float | None, *, none_text: str = "Unknown") -> str:
    if low is None and high is None:
        return none_text
    if low is not None and high is not None:
        if abs(low - high) < 1e-6:
            return format_money(low)
        return f"{format_money(low)}–{format_money(high)}"
    if high is None:
        return f"{format_money(low)}+"
    return f"up to {format_money(high)}"
