"""Geography normalization.

Company locations and investor mandates are reduced to a common token vocabulary:

* ``US-TX``            - a US state
* ``US-REGION:SOUTH``  - a US region (as investors often phrase mandates)
* ``US`` / ``GB`` ...  - a country (ISO 3166 alpha-2)
* ``NORTH_AMERICA``    - a macro region
* ``GLOBAL``           - no geographic restriction

A company location expands *upward* (Austin -> US-TX -> US-REGION:SOUTH -> US ->
NORTH_AMERICA); an investor token matches when it appears in that expansion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from functools import lru_cache

US_STATES: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
_ABBR_TO_STATE = {abbr: name for name, abbr in US_STATES.items()}

US_REGIONS: dict[str, set[str]] = {
    "NORTHEAST": {"CT", "ME", "MA", "NH", "RI", "VT", "NJ", "NY", "PA"},
    "MIDWEST": {"IL", "IN", "MI", "OH", "WI", "IA", "KS", "MN", "MO", "NE", "ND", "SD"},
    "SOUTH": {"DE", "FL", "GA", "MD", "NC", "SC", "VA", "DC", "WV", "AL", "KY", "MS", "TN",
              "AR", "LA", "OK", "TX"},
    "WEST": {"AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY", "AK", "CA", "HI", "OR", "WA"},
}
_US_REGION_ALIASES: dict[str, str] = {
    "northeast": "NORTHEAST", "new england": "NORTHEAST", "mid-atlantic": "NORTHEAST",
    "midwest": "MIDWEST", "great lakes": "MIDWEST",
    "southeast": "SOUTH", "south": "SOUTH", "southwest": "SOUTH", "texas triangle": "SOUTH",
    "west coast": "WEST", "pacific northwest": "WEST", "mountain west": "WEST", "west": "WEST",
}

#: Country name/alias -> (ISO alpha-2, macro region)
COUNTRIES: dict[str, tuple[str, str]] = {
    "united states": ("US", "NORTH_AMERICA"), "united states of america": ("US", "NORTH_AMERICA"),
    "usa": ("US", "NORTH_AMERICA"), "u.s.": ("US", "NORTH_AMERICA"), "u.s.a.": ("US", "NORTH_AMERICA"),
    # (bare "america" is deliberately absent: it would match "North/South/Latin America")
    "canada": ("CA", "NORTH_AMERICA"), "mexico": ("MX", "LATAM"),
    "united kingdom": ("GB", "EUROPE"), "uk": ("GB", "EUROPE"), "england": ("GB", "EUROPE"),
    "scotland": ("GB", "EUROPE"), "ireland": ("IE", "EUROPE"), "germany": ("DE", "EUROPE"),
    "france": ("FR", "EUROPE"), "netherlands": ("NL", "EUROPE"), "belgium": ("BE", "EUROPE"),
    "switzerland": ("CH", "EUROPE"), "sweden": ("SE", "EUROPE"), "norway": ("NO", "EUROPE"),
    "denmark": ("DK", "EUROPE"), "finland": ("FI", "EUROPE"), "spain": ("ES", "EUROPE"),
    "italy": ("IT", "EUROPE"), "portugal": ("PT", "EUROPE"), "austria": ("AT", "EUROPE"),
    "poland": ("PL", "EUROPE"), "israel": ("IL", "MENA"), "united arab emirates": ("AE", "MENA"),
    "uae": ("AE", "MENA"), "saudi arabia": ("SA", "MENA"), "qatar": ("QA", "MENA"),
    "egypt": ("EG", "MENA"), "india": ("IN", "ASIA_PACIFIC"), "china": ("CN", "ASIA_PACIFIC"),
    "japan": ("JP", "ASIA_PACIFIC"), "south korea": ("KR", "ASIA_PACIFIC"),
    "korea": ("KR", "ASIA_PACIFIC"), "singapore": ("SG", "ASIA_PACIFIC"),
    "australia": ("AU", "ASIA_PACIFIC"), "new zealand": ("NZ", "ASIA_PACIFIC"),
    "hong kong": ("HK", "ASIA_PACIFIC"), "taiwan": ("TW", "ASIA_PACIFIC"),
    "indonesia": ("ID", "ASIA_PACIFIC"), "vietnam": ("VN", "ASIA_PACIFIC"),
    "brazil": ("BR", "LATAM"), "argentina": ("AR", "LATAM"), "chile": ("CL", "LATAM"),
    "colombia": ("CO", "LATAM"), "nigeria": ("NG", "AFRICA"), "kenya": ("KE", "AFRICA"),
    "south africa": ("ZA", "AFRICA"), "ghana": ("GH", "AFRICA"),
}
_ISO_TO_MACRO = {iso: macro for iso, macro in COUNTRIES.values()}
_ISO_NAMES = {
    "US": "United States", "CA": "Canada", "GB": "United Kingdom", "DE": "Germany",
    "FR": "France", "IL": "Israel", "IN": "India", "SG": "Singapore", "AU": "Australia",
}

MACRO_REGIONS: dict[str, str] = {
    "north america": "NORTH_AMERICA", "europe": "EUROPE", "european union": "EUROPE",
    "eu": "EUROPE", "emea": "EUROPE", "asia": "ASIA_PACIFIC", "apac": "ASIA_PACIFIC",
    "asia pacific": "ASIA_PACIFIC", "asia-pacific": "ASIA_PACIFIC", "latin america": "LATAM",
    "latam": "LATAM", "south america": "LATAM", "middle east": "MENA", "mena": "MENA",
    "africa": "AFRICA",
}
_GLOBAL_WORDS = ("global", "worldwide", "international", "anywhere", "geography agnostic")
_MACRO_LABELS = {
    "NORTH_AMERICA": "North America", "EUROPE": "Europe", "ASIA_PACIFIC": "Asia-Pacific",
    "LATAM": "Latin America", "MENA": "Middle East & North Africa", "AFRICA": "Africa",
}

#: A few frequent startup-hub cities, so "based in Austin" resolves without a state.
CITIES: dict[str, str] = {
    "austin": "TX", "dallas": "TX", "houston": "TX", "san antonio": "TX", "boston": "MA",
    "cambridge, ma": "MA", "san francisco": "CA", "palo alto": "CA", "los angeles": "CA",
    "san diego": "CA", "seattle": "WA", "new york city": "NY", "nyc": "NY", "chicago": "IL",
    "denver": "CO", "atlanta": "GA", "miami": "FL", "nashville": "TN", "pittsburgh": "PA",
    "philadelphia": "PA", "minneapolis": "MN", "salt lake city": "UT", "raleigh": "NC",
    "durham": "NC", "baltimore": "MD", "cleveland": "OH", "columbus": "OH", "detroit": "MI",
    "phoenix": "AZ", "portland": "OR",
}
_INTL_CITIES: dict[str, str] = {
    "london": "united kingdom", "berlin": "germany", "munich": "germany", "paris": "france",
    "amsterdam": "netherlands", "toronto": "canada", "vancouver": "canada", "montreal": "canada",
    "tel aviv": "israel", "bangalore": "india", "bengaluru": "india", "singapore": "singapore",
    "sydney": "australia", "dublin": "ireland", "stockholm": "sweden", "zurich": "switzerland",
    "dubai": "united arab emirates",
}


def _has_word(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(needle)}(?![a-z])", haystack) is not None


@dataclass
class GeoLocation:
    """A resolved company location and its upward token expansion."""

    label: str
    country: str | None = None
    subdivision: str | None = None
    macro_region: str | None = None
    tokens: set[str] = field(default_factory=set)

    @property
    def resolved(self) -> bool:
        return bool(self.tokens)


def parse_geo_tokens(text: str | None) -> set[str]:
    """Tokens for every place named in free text (investor mandates, preferences)."""
    if not text:
        return set()
    return set(_geo_tokens(text))


# A 20,000-investor list repeats the same few hundred mandate strings; parse each once.
@lru_cache(maxsize=65536)
def _geo_tokens(text: str) -> frozenset[str]:
    lowered = f" {text.lower()} "
    tokens: set[str] = set()
    if any(_has_word(lowered, word) for word in _GLOBAL_WORDS):
        tokens.add("GLOBAL")
    for name, abbr in US_STATES.items():
        if _has_word(lowered, name) and not (name == "washington" and "washington, d" in lowered):
            tokens.add(f"US-{abbr}")
    for alias, region in _US_REGION_ALIASES.items():
        if _has_word(lowered, alias):
            tokens.add(f"US-REGION:{region}")
    for name, (iso, _macro) in COUNTRIES.items():
        if _has_word(lowered, name):
            tokens.add(iso)
    for name, macro in MACRO_REGIONS.items():
        if _has_word(lowered, name):
            tokens.add(macro)
    # Upper-case abbreviations are only trusted in their exact upper-case form.
    for match in re.finditer(r"\b(US|USA|UK|EU)\b", text):
        tokens.add({"US": "US", "USA": "US", "UK": "GB", "EU": "EUROPE"}[match.group(1)])
    for match in re.finditer(r",\s*([A-Z]{2})\b", text):
        if match.group(1) in _ABBR_TO_STATE:
            tokens.add(f"US-{match.group(1)}")
    for city, abbr in CITIES.items():
        if _has_word(lowered, city):
            tokens.add(f"US-{abbr}")
    for city, country in _INTL_CITIES.items():
        if _has_word(lowered, city):
            tokens.add(COUNTRIES[country][0])
    # "West Virginia" contains "virginia"; keep Virginia only when it is named on its own.
    if "US-WV" in tokens and "virginia" not in lowered.replace("west virginia", ""):
        tokens.discard("US-VA")
    # "Georgia" alone is ambiguous; keep the US reading only with other US context.
    if "US-GA" in tokens and "georgia" in lowered and not (tokens & {"US", "NORTH_AMERICA"}) \
            and not re.search(r"atlanta|, ga\b", lowered):
        tokens.discard("US-GA")
    return frozenset(tokens)


def resolve_location(text: str | None) -> GeoLocation | None:
    """Resolve a company HQ string to a location with its upward expansion."""
    if not text or not text.strip():
        return None
    location = _resolve_location(text)
    # A copy, so a caller that edits its location can never change the cached one.
    return replace(location, tokens=set(location.tokens)) if location else None


@lru_cache(maxsize=4096)
def _resolve_location(text: str) -> GeoLocation | None:
    tokens = parse_geo_tokens(text)
    states = sorted(t[3:] for t in tokens if t.startswith("US-") and not t.startswith("US-REGION"))
    countries = sorted(t for t in tokens if len(t) == 2 and t.isupper())
    macros = sorted(t for t in tokens if t in _MACRO_LABELS)

    location = GeoLocation(label=" ".join(text.split())[:80])
    if states:
        state = states[0]
        location.subdivision = f"US-{state}"
        location.country = "US"
    elif countries:
        # Prefer a non-US country when both appear ("US and Israel" -> keep first mention).
        location.country = countries[0] if len(countries) == 1 else _first_mentioned(text, countries)
    elif macros:
        location.macro_region = macros[0]

    if location.country:
        location.macro_region = _ISO_TO_MACRO.get(location.country, location.macro_region)
    expanded: set[str] = set()
    if location.subdivision:
        expanded.add(location.subdivision)
        state = location.subdivision[3:]
        for region, members in US_REGIONS.items():
            if state in members:
                expanded.add(f"US-REGION:{region}")
    if location.country:
        expanded.add(location.country)
    if location.macro_region:
        expanded.add(location.macro_region)
    location.tokens = expanded
    return location if expanded else None


def _first_mentioned(text: str, isos: list[str]) -> str:
    lowered = text.lower()
    best, best_pos = isos[0], len(lowered) + 1
    for name, (iso, _macro) in COUNTRIES.items():
        if iso in isos:
            pos = lowered.find(name)
            if 0 <= pos < best_pos:
                best, best_pos = iso, pos
    return best


def describe_location(location: GeoLocation | None) -> str:
    """Human label, e.g. "Austin, Texas (US)"."""
    if location is None:
        return "Unknown"
    suffix = location.country or _MACRO_LABELS.get(location.macro_region or "", "")
    if suffix and suffix not in location.label:
        return f"{location.label} ({suffix})"
    return location.label


def describe_token(token: str) -> str:
    if token == "GLOBAL":
        return "Global"
    if token.startswith("US-REGION:"):
        return f"US {token.split(':', 1)[1].title()}"
    if token.startswith("US-"):
        return _ABBR_TO_STATE.get(token[3:], token).title()
    if token in _MACRO_LABELS:
        return _MACRO_LABELS[token]
    return _ISO_NAMES.get(token, token)


def geography_match(company_tokens: set[str], investor_tokens: set[str]) -> tuple[float, str | None]:
    """Best raw match (0-100) of an investor mandate against a company's expanded tokens.

    Returns (score, matched token). Same state/region/country scores 100, same macro
    region 85, a global mandate 70, and no overlap 0.
    """
    best: tuple[float, str | None] = (0.0, None)
    for token in investor_tokens:
        if token == "GLOBAL":
            score = 70.0
        elif token in company_tokens:
            score = 85.0 if token in _MACRO_LABELS else 100.0
        else:
            continue
        if score > best[0]:
            best = (score, token)
    return best
